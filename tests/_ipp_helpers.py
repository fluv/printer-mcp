"""Test-only helpers for the IPP wire format.

Lives in tests/ rather than the production package so the shipped wheel
doesn't carry encoder helpers it never uses at runtime. Imported by
``test_ipp.py`` to construct deterministic response payloads without
duplicating the encoding logic.
"""

import io
import struct
from typing import Any

from printer_mcp.ipp import (
    _INTEGER_TAGS,
    _STRING_TAGS,
    IPP_VERSION,
    TAG_END_OF_ATTRS,
    TAG_NO_VALUE,
)


def build_response(
    status_code: int, attrs: list[tuple[int, int, str, Any]]
) -> bytes:
    """Build an IPP response payload from a list of (group, value-tag, name, value) tuples."""
    buf = io.BytesIO()
    buf.write(IPP_VERSION)
    buf.write(struct.pack(">H", status_code))
    buf.write(struct.pack(">I", 1))
    current_group: int | None = None
    for group, tag, name, value in attrs:
        if group != current_group:
            buf.write(bytes([group]))
            current_group = group
        encoded_value: bytes
        if tag in _INTEGER_TAGS:
            encoded_value = struct.pack(">i", value)
        elif tag in _STRING_TAGS:
            encoded_value = str(value).encode("utf-8")
        elif tag == TAG_NO_VALUE:
            encoded_value = b""
        else:
            encoded_value = value if isinstance(value, bytes) else str(value).encode("utf-8")
        name_bytes = name.encode("utf-8")
        buf.write(struct.pack(">B H", tag, len(name_bytes)))
        buf.write(name_bytes)
        buf.write(struct.pack(">H", len(encoded_value)))
        buf.write(encoded_value)
    buf.write(bytes([TAG_END_OF_ATTRS]))
    return buf.getvalue()
