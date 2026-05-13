"""Protocol-layer round-trip tests.

These hit the real ASGI app via httpx's ASGI transport and speak real
MCP JSON-RPC over streamable HTTP. Calling the tool functions directly
would bypass the schema-inference path in FastMCP, which is exactly the
layer most likely to break silently.
"""

import json
from typing import Any

import httpx
import pytest

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    """MCP streamable-HTTP returns either JSON or SSE depending on negotiation."""
    ct = response.headers.get("content-type", "")
    if "application/json" in ct:
        return response.json()
    if "text/event-stream" in ct:
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line.removeprefix("data: "))
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
    assert tool_names == {"print_latex", "watch_page"}
    schemas = {t["name"]: t["inputSchema"] for t in body["result"]["tools"]}
    assert "source" in schemas["print_latex"]["properties"]
    assert "copies" in schemas["print_latex"]["properties"]
    assert "job_id" in schemas["watch_page"]["properties"]
    # Required-field inference: source and job_id required; copies has a default.
    assert "source" in schemas["print_latex"]["required"]
    assert "job_id" in schemas["watch_page"]["required"]
    assert "copies" not in schemas["print_latex"].get("required", [])


# Deliberately no tools/call test at the scaffold stage: in stateless streamable-HTTP
# mode FastMCP opens an SSE response for tools/call that doesn't close cleanly under
# httpx's ASGI transport, which hangs the test. The tools/list assertion above already
# validates the schema-inference path the protocol-layer test exists to cover. A
# proper tools/call test lands with the real implementation, where streaming response
# semantics are part of the design (per-page reveal via `watch_page`) and worth
# testing in their own right.
