"""Minimal IPP client.

Speaks just enough IPP to submit a PWG-Raster job, poll job attributes, and
read printer attributes — the three operations the design needs. The wire
format is documented in RFC 8011 § 4; this is a hand-rolled implementation
because the asynchronous Python IPP libraries don't fit the synchronous
FastMCP tool surface, and shelling out to ``ipptool`` would mean carrying a
CUPS apt package just for one binary.

Probes against the HL-L2865DW (discussions/890) established that:

- ``job-impressions-completed`` advances once per physical sheet eject during
  printing, not in a single batch at completion. Polling it is the
  per-page signal ``watch_page`` blocks on.
- ``job-media-sheets-completed`` is not returned by this printer at all.
- ``job-impressions`` returns ``no-value`` — the printer does not pre-count
  pages, so total pages must come from the PDF pipeline (see ``pdf.py``).
"""

from __future__ import annotations

import http.client
import struct
import urllib.parse
from dataclasses import dataclass
from typing import Any

IPP_VERSION = b"\x02\x00"  # 2.0
DEFAULT_HTTP_TIMEOUT = 30.0

# Operation IDs (RFC 8011 § 5.4.15)
OP_PRINT_JOB = 0x0002
OP_GET_JOB_ATTRIBUTES = 0x0009
OP_GET_PRINTER_ATTRIBUTES = 0x000B

# Delimiter / value tags (RFC 8011 § 5.4.14)
TAG_OPERATION_ATTRS = 0x01
TAG_JOB_ATTRS = 0x02
TAG_END_OF_ATTRS = 0x03
TAG_PRINTER_ATTRS = 0x04
TAG_UNSUPPORTED_VALUE = 0x10
TAG_NO_VALUE = 0x13
TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_OCTETSTRING_UNSPEC = 0x30
TAG_DATETIME = 0x31
TAG_RESOLUTION = 0x32
TAG_RANGE_OF_INTEGER = 0x33
TAG_BEG_COLLECTION = 0x34
TAG_END_COLLECTION = 0x37
TAG_MEMBER_NAME = 0x4A
TAG_TEXT_WITHOUT_LANG = 0x41
TAG_NAME_WITHOUT_LANG = 0x42
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_CHARSET = 0x47
TAG_NATURAL_LANGUAGE = 0x48
TAG_MIME_MEDIA_TYPE = 0x49

_INTEGER_TAGS = {TAG_INTEGER, TAG_ENUM, TAG_BOOLEAN}
_STRING_TAGS = {
    TAG_KEYWORD,
    TAG_URI,
    TAG_CHARSET,
    TAG_NATURAL_LANGUAGE,
    TAG_MIME_MEDIA_TYPE,
    TAG_NAME_WITHOUT_LANG,
    TAG_TEXT_WITHOUT_LANG,
}

# Job state enums (RFC 8011 § 5.3.7)
JOB_STATE_PENDING = 3
JOB_STATE_PENDING_HELD = 4
JOB_STATE_PROCESSING = 5
JOB_STATE_PROCESSING_STOPPED = 6
JOB_STATE_CANCELED = 7
JOB_STATE_ABORTED = 8
JOB_STATE_COMPLETED = 9

TERMINAL_JOB_STATES = frozenset(
    {JOB_STATE_CANCELED, JOB_STATE_ABORTED, JOB_STATE_COMPLETED}
)


class IppError(RuntimeError):
    """Raised when the IPP exchange fails (transport, status, parse)."""


@dataclass
class IppResponse:
    """Parsed IPP response.

    Attributes are flattened into a single dict keyed by attribute name. The
    value is the first occurrence; multi-valued attributes coalesce into a
    list. ``status_code`` is the IPP status (``0x0000`` = successful-ok).
    """

    status_code: int
    attrs: dict[str, Any]


def _enc_attr(value_tag: int, name: bytes, value: bytes) -> bytes:
    return struct.pack(">B H", value_tag, len(name)) + name + struct.pack(">H", len(value)) + value


def _enc_string_attr(value_tag: int, name: str, value: str) -> bytes:
    return _enc_attr(value_tag, name.encode("utf-8"), value.encode("utf-8"))


def _enc_integer_attr(name: str, value: int) -> bytes:
    return _enc_attr(TAG_INTEGER, name.encode("utf-8"), struct.pack(">i", value))


def _enc_keyword_set(name: str, values: list[str]) -> bytes:
    """Encode a 1setOf keyword attribute."""
    if not values:
        return b""
    out = _enc_string_attr(TAG_KEYWORD, name, values[0])
    for v in values[1:]:
        # Additional value: name-length = 0
        body = v.encode("utf-8")
        out += struct.pack(">B H", TAG_KEYWORD, 0) + struct.pack(">H", len(body)) + body
    return out


def _build_request(
    op_id: int,
    request_id: int,
    operation_attrs: bytes,
    job_attrs: bytes = b"",
    payload: bytes = b"",
) -> bytes:
    """Assemble the IPP wire format for a request."""
    parts = [
        IPP_VERSION,
        struct.pack(">H", op_id),
        struct.pack(">I", request_id),
        bytes([TAG_OPERATION_ATTRS]),
        operation_attrs,
    ]
    if job_attrs:
        parts.append(bytes([TAG_JOB_ATTRS]))
        parts.append(job_attrs)
    parts.append(bytes([TAG_END_OF_ATTRS]))
    parts.append(payload)
    return b"".join(parts)


def _common_op_attrs(uri: str, user: str) -> bytes:
    return (
        _enc_string_attr(TAG_CHARSET, "attributes-charset", "utf-8")
        + _enc_string_attr(TAG_NATURAL_LANGUAGE, "attributes-natural-language", "en")
        + _enc_string_attr(TAG_URI, "printer-uri", uri)
        + _enc_string_attr(TAG_NAME_WITHOUT_LANG, "requesting-user-name", user)
    )


def _parse_response(body: bytes) -> IppResponse:
    """Parse an IPP response. Multi-valued attributes coalesce into lists."""
    if len(body) < 8:
        raise IppError(f"IPP response truncated: {len(body)} bytes")
    # 2 bytes version, 2 bytes status, 4 bytes request-id
    _version, status_code, _request_id = struct.unpack(">HHI", body[:8])
    pos = 8
    attrs: dict[str, Any] = {}
    last_name: str | None = None
    group: int | None = None
    while pos < len(body):
        tag = body[pos]
        pos += 1
        # Group / delimiter tags are <= 0x0F
        if tag <= 0x0F:
            if tag == TAG_END_OF_ATTRS:
                break
            group = tag
            last_name = None
            continue
        # Value tag: name-len, name, value-len, value
        if pos + 2 > len(body):
            raise IppError("Truncated attribute header")
        (name_len,) = struct.unpack(">H", body[pos:pos + 2])
        pos += 2
        name = body[pos:pos + name_len].decode("utf-8") if name_len else None
        pos += name_len
        if pos + 2 > len(body):
            raise IppError("Truncated value-length")
        (value_len,) = struct.unpack(">H", body[pos:pos + 2])
        pos += 2
        raw_value = body[pos:pos + value_len]
        pos += value_len
        value = _decode_value(tag, raw_value)
        if name:
            last_name = name
            if name in attrs:
                # Promote to list (rare with this grouping; defensive)
                if isinstance(attrs[name], list):
                    attrs[name].append(value)
                else:
                    attrs[name] = [attrs[name], value]
            else:
                attrs[name] = value
        elif last_name is not None:
            # Additional value for previous attribute → coalesce to list
            existing = attrs[last_name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                attrs[last_name] = [existing, value]
        # Collection / member-name tags ignored — none of our queries return them.
        _ = group
    return IppResponse(status_code=status_code, attrs=attrs)


def _decode_value(tag: int, raw: bytes) -> Any:
    if tag in _INTEGER_TAGS:
        if len(raw) != 4:
            return None
        return struct.unpack(">i", raw)[0]
    if tag in _STRING_TAGS:
        return raw.decode("utf-8", errors="replace")
    if tag == TAG_NO_VALUE:
        return None
    if tag == TAG_UNSUPPORTED_VALUE:
        return None
    # Unknown / unhandled tag — return raw bytes for visibility in printer-attrs dump.
    return raw


def _post(uri: str, payload: bytes, timeout: float) -> bytes:
    """POST an IPP request and return the response body."""
    parsed = urllib.parse.urlparse(uri)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "ipps" else 631)
    path = parsed.path or "/"
    if parsed.scheme == "ipps":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, port, timeout=timeout
        )
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        try:
            conn.request(
                "POST",
                path,
                body=payload,
                headers={"Content-Type": "application/ipp"},
            )
            resp = conn.getresponse()
        except TimeoutError as exc:
            raise IppError(f"IPP transport timeout against {uri}") from exc
        except OSError as exc:
            raise IppError(f"IPP transport error against {uri}: {exc}") from exc
        if resp.status != 200:
            raise IppError(f"IPP HTTP {resp.status} from {uri}")
        return resp.read()
    finally:
        conn.close()


def submit_urf(
    uri: str,
    urf_path: str,
    job_name: str,
    user: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> int:
    """Send a Print-Job request with an Apple URF payload, return job-id.

    URF is the printer's ``document-format-preferred`` (probe in
    discussions/890). Switched from PWG-Raster after that pipeline rendered
    inverted (white on black) — see ``pdf.to_urf`` for the colorspace
    explanation.
    """
    with open(urf_path, "rb") as f:
        payload = f.read()
    op_attrs = (
        _common_op_attrs(uri, user)
        + _enc_string_attr(TAG_NAME_WITHOUT_LANG, "job-name", job_name)
        + _enc_string_attr(TAG_MIME_MEDIA_TYPE, "document-format", "image/urf")
    )
    req = _build_request(OP_PRINT_JOB, 1, op_attrs, payload=payload)
    resp = _parse_response(_post(uri, req, timeout))
    if resp.status_code >= 0x0400:
        raise IppError(f"Print-Job failed with status 0x{resp.status_code:04X}")
    job_id = resp.attrs.get("job-id")
    if not isinstance(job_id, int):
        raise IppError(f"Print-Job response missing job-id: {resp.attrs}")
    return job_id


def get_job_attrs(
    uri: str,
    job_id: int,
    user: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> dict[str, Any]:
    """Get-Job-Attributes for one job, return all attributes flat."""
    op_attrs = (
        _common_op_attrs(uri, user)
        + _enc_integer_attr("job-id", job_id)
        + _enc_keyword_set("requested-attributes", ["all"])
    )
    req = _build_request(OP_GET_JOB_ATTRIBUTES, 1, op_attrs)
    resp = _parse_response(_post(uri, req, timeout))
    if resp.status_code >= 0x0400:
        raise IppError(f"Get-Job-Attributes failed with status 0x{resp.status_code:04X}")
    return resp.attrs


def get_printer_attrs(
    uri: str,
    user: str,
    requested: list[str] | None = None,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> dict[str, Any]:
    """Get-Printer-Attributes, return all attributes flat."""
    op_attrs = _common_op_attrs(uri, user) + _enc_keyword_set(
        "requested-attributes", requested or ["all"]
    )
    req = _build_request(OP_GET_PRINTER_ATTRIBUTES, 1, op_attrs)
    resp = _parse_response(_post(uri, req, timeout))
    if resp.status_code >= 0x0400:
        raise IppError(
            f"Get-Printer-Attributes failed with status 0x{resp.status_code:04X}"
        )
    return resp.attrs


__all__ = [
    "IPP_VERSION",
    "IppError",
    "IppResponse",
    "JOB_STATE_COMPLETED",
    "JOB_STATE_PROCESSING",
    "TERMINAL_JOB_STATES",
    "get_job_attrs",
    "get_printer_attrs",
    "submit_urf",
]

