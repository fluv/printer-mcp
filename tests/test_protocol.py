"""Protocol-layer and direct-call tests.

The HTTP-transport tests speak real MCP JSON-RPC over httpx's ASGI transport
against the FastMCP streamable-HTTP app — that's the layer where schema
inference, custom routes, and lifespan registration converge, and the one
most likely to break silently.

The verification-stub tools/call tests use ``mcp.call_tool()`` directly
rather than going via HTTP, because in stateless streamable-HTTP mode
FastMCP's ``tools/call`` response doesn't close cleanly under httpx's ASGI
transport even with ``json_response=True``. ``mcp.call_tool()`` exercises
the same schema-inference and validation path as the HTTP handler but
skips the transport framing that's the actual blocker. v1 should revisit
this once the real per-page response semantics are in place.
"""

import json
from typing import Any

import httpx
import pytest

from printer_mcp.server import _PAGES_SHOWN, mcp

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    """MCP streamable-HTTP responds either as JSON or as a single SSE event."""
    ct = response.headers.get("content-type", "")
    if "application/json" in ct:
        return response.json()
    if "text/event-stream" in ct:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                # SSE spec allows zero or more spaces after `data:`; strip
                # leading whitespace rather than assuming `data: ` with one space.
                return json.loads(line[5:].lstrip())
        raise AssertionError(f"no data event in SSE body: {response.text!r}")
    raise AssertionError(f"unexpected content-type: {ct!r}")


async def _initialize(client: httpx.AsyncClient) -> str:
    init_response = await client.post(
        "/mcp",
        headers=MCP_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.1"},
            },
        },
    )
    assert init_response.status_code == 200, init_response.text
    session_id = init_response.headers.get("mcp-session-id", "")
    initialized = await client.post(
        "/mcp",
        headers={**MCP_HEADERS, **({"mcp-session-id": session_id} if session_id else {})},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert initialized.status_code in (200, 202), initialized.text
    return session_id


@pytest.fixture(autouse=True)
def _reset_stub_state() -> None:
    """Each test starts with no pages revealed.

    Module-level state in ``printer_mcp.server`` persists across tests within
    the session; without this reset a later test would inherit page counters
    from an earlier one. Autouse so a future test that calls the stub tools
    without remembering to request the fixture doesn't silently inherit state.
    """
    _PAGES_SHOWN.clear()


@pytest.mark.asyncio
async def test_healthz_responds_ok(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "printer-mcp"


@pytest.mark.asyncio
async def test_metrics_exposes_prometheus_text(client: httpx.AsyncClient) -> None:
    r = await client.get("/metrics")
    assert r.status_code == 200
    # Counter is defined even before any tool call so the metric name appears.
    assert "printer_mcp_tool_calls_total" in r.text


@pytest.mark.asyncio
async def test_tools_list_returns_both_stubs_with_schemas(
    client: httpx.AsyncClient,
) -> None:
    session_id = await _initialize(client)
    headers = {**MCP_HEADERS, **({"mcp-session-id": session_id} if session_id else {})}
    r = await client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    assert r.status_code == 200, r.text
    body = _parse_response(r)
    tool_names = {t["name"] for t in body["result"]["tools"]}
    # Superset rather than exact equality: adding new tools should add new
    # tests, not break the existing schema-inference check.
    assert tool_names >= {"print_latex", "watch_page"}
    schemas = {t["name"]: t["inputSchema"] for t in body["result"]["tools"]}
    assert "source" in schemas["print_latex"]["properties"]
    assert "copies" in schemas["print_latex"]["properties"]
    assert "job_id" in schemas["watch_page"]["properties"]
    # Required-field inference: source and job_id required; copies has a default.
    assert "source" in schemas["print_latex"]["required"]
    assert "job_id" in schemas["watch_page"]["required"]
    assert "copies" not in schemas["print_latex"].get("required", [])


def _content_types(blocks: Any) -> list[str]:
    return [getattr(b, "type", None) or b["type"] for b in blocks]


def _content_text(block: Any) -> str:
    return getattr(block, "text", None) or block["text"]


def _content_image_data(block: Any) -> str:
    return getattr(block, "data", None) or block["data"]


def _content_mime(block: Any) -> str:
    return getattr(block, "mimeType", None) or block["mimeType"]


@pytest.mark.asyncio
async def test_print_latex_returns_text_plus_image() -> None:
    blocks = await mcp.call_tool("print_latex", {"source": "\\documentclass{article}"})
    # Exactly one text block and one image block, in that order — verifies the
    # multi-content tuple return is wired up correctly.
    assert _content_types(blocks) == ["text", "image"]
    payload = json.loads(_content_text(blocks[0]))
    assert payload["job_id"] == "stub-job-1"
    assert payload["total_pages"] == 3
    assert payload["stub"] is True
    # Image block is base64 PNG.
    assert _content_mime(blocks[1]) == "image/png"
    assert _content_image_data(blocks[1]).startswith("iVBORw0KGgo")  # PNG magic in base64


@pytest.mark.asyncio
async def test_watch_page_walks_remaining_pages() -> None:
    """Three sequential calls: page 2, page 3, then complete."""
    # Prime page 1 via print_latex.
    await mcp.call_tool("print_latex", {"source": "x"})

    second = await mcp.call_tool("watch_page", {"job_id": "stub-job-1"})
    third = await mcp.call_tool("watch_page", {"job_id": "stub-job-1"})
    finished = await mcp.call_tool("watch_page", {"job_id": "stub-job-1"})

    assert _content_types(second) == ["text", "image"]
    assert _content_types(third) == ["text", "image"]
    # No more pages — text only, image deliberately absent.
    assert _content_types(finished) == ["text"]

    assert json.loads(_content_text(second[0]))["page"] == 2
    assert json.loads(_content_text(third[0]))["page"] == 3
    finished_payload = json.loads(_content_text(finished[0]))
    assert finished_payload["status"] == "complete"
    assert finished_payload["pages_revealed"] == 3


@pytest.mark.asyncio
async def test_watch_page_unknown_job_reveals_page_one() -> None:
    """An unknown job_id still returns an image — the stub treats any first call
    as "reveal page 1" so the surface test isn't blocked by id-management.
    """
    blocks = await mcp.call_tool("watch_page", {"job_id": "never-printed"})
    assert _content_types(blocks) == ["text", "image"]
    payload = json.loads(_content_text(blocks[0]))
    assert payload["page"] == 1
    assert payload["job_id"] == "never-printed"
