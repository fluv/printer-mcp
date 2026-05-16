"""Unit tests for the hand-rolled IPP client.

Tests cover the wire-format round-trip — request building isn't directly
exercised because it goes out over HTTP, but the parser is fed responses
built by the test helper (which uses the same encoding) so the format
contract is exercised end-to-end without touching a network.
"""

from __future__ import annotations

from printer_mcp.ipp import (
    JOB_STATE_COMPLETED,
    JOB_STATE_PROCESSING,
    TAG_INTEGER,
    TAG_JOB_ATTRS,
    TAG_KEYWORD,
    TAG_NO_VALUE,
    TAG_OPERATION_ATTRS,
    TAG_PRINTER_ATTRS,
    TAG_URI,
    TERMINAL_JOB_STATES,
    _parse_response,
)
from tests._ipp_helpers import build_response


def test_parse_recovers_string_and_integer_attributes() -> None:
    body = build_response(
        status_code=0x0000,
        attrs=[
            (TAG_OPERATION_ATTRS, TAG_URI, "printer-uri", "ipp://x/ipp/print"),
            (TAG_JOB_ATTRS, TAG_INTEGER, "job-id", 42),
            (TAG_JOB_ATTRS, TAG_INTEGER, "job-impressions-completed", 3),
        ],
    )
    resp = _parse_response(body)
    assert resp.status_code == 0
    assert resp.attrs["job-id"] == 42
    assert resp.attrs["job-impressions-completed"] == 3
    assert resp.attrs["printer-uri"] == "ipp://x/ipp/print"


def test_parse_handles_no_value_tag_as_none() -> None:
    # The HL-L2865DW returns ``job-impressions = no-value`` because it
    # doesn't pre-count pages — see discussions/890. Make sure the parser
    # doesn't choke on the empty value.
    body = build_response(
        status_code=0,
        attrs=[(TAG_JOB_ATTRS, TAG_NO_VALUE, "job-impressions", None)],
    )
    resp = _parse_response(body)
    assert resp.attrs["job-impressions"] is None


def test_terminal_state_set_matches_rfc_8011_values() -> None:
    # Sanity-check the constants against their RFC 8011 numeric values.
    # canceled=7, aborted=8, completed=9.
    assert TERMINAL_JOB_STATES == {7, 8, 9}
    assert JOB_STATE_PROCESSING == 5
    assert JOB_STATE_COMPLETED == 9


def test_parse_status_code_picks_up_failure() -> None:
    # 0x0400 = client-error-bad-request — anything ≥ 0x0400 is an error.
    body = build_response(status_code=0x0400, attrs=[])
    resp = _parse_response(body)
    assert resp.status_code == 0x0400
    assert resp.attrs == {}


def test_parse_multivalue_attribute_coalesces_to_list() -> None:
    # ``operations-supported`` is a 1setOf enum — multiple values share one
    # name. The encoder uses zero-length name for additional values.
    body = build_response(
        status_code=0,
        attrs=[
            (TAG_PRINTER_ATTRS, TAG_KEYWORD, "document-format-supported", "image/pwg-raster"),
            (TAG_PRINTER_ATTRS, TAG_KEYWORD, "", "image/urf"),
            (TAG_PRINTER_ATTRS, TAG_KEYWORD, "", "application/octet-stream"),
        ],
    )
    resp = _parse_response(body)
    formats = resp.attrs["document-format-supported"]
    assert isinstance(formats, list)
    assert formats == ["image/pwg-raster", "image/urf", "application/octet-stream"]
