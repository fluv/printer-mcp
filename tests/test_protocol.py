"""Protocol-layer tests for the v1 MCP server.

All subprocess-driven dependencies (latex, pdf, ipp) are monkeypatched —
these tests verify orchestration (tools/list schema, content shape,
watch_page sequencing, resource bodies) without touching a printer or
shelling out to ghostscript or latexmk. End-to-end is a manual integration
step against the real printer; the unit checks here catch wire-format and
glue regressions.

The HTTP-transport tests speak real MCP JSON-RPC over httpx's ASGI
transport — that's the layer where schema inference, custom routes, and
lifespan registration converge.

The direct-call tests use ``mcp.call_tool()`` rather than going via HTTP
because in stateless streamable-HTTP mode FastMCP's ``tools/call`` response
doesn't close cleanly under httpx's ASGI transport (verification-stub
session, preserved here). ``mcp.call_tool()`` exercises the same
schema-inference and validation path while skipping the transport framing
that's the blocker.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from printer_mcp import server
from printer_mcp.jobs import Job
from printer_mcp.latex import CompileError, CompileResult
from printer_mcp.server import JOBS, mcp

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

# 1×1 transparent PNG — small fixture for "what page_to_png returns".
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkqOf/DwACvgGOdoaeAgAAAABJRU5ErkJggg=="
)


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    """MCP streamable-HTTP responds either as JSON or as a single SSE event."""
    ct = response.headers.get("content-type", "")
    if "application/json" in ct:
        return response.json()
    if "text/event-stream" in ct:
        for line in response.text.splitlines():
            if line.startswith("data:"):
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
def _reset_job_store() -> None:
    """Module-level JobStore persists across tests; clear before each."""
    JOBS._jobs.clear()


@pytest.fixture
def fake_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Replace the latex/pdf/ipp subprocesses with deterministic fakes.

    Returns a state dict the test can mutate to control how impressions
    advance: ``advance_to`` is the integer value the next Get-Job-Attributes
    call will return for ``job-impressions-completed``. Tests can flip this
    between calls to simulate per-sheet eject pacing or terminal failures.
    """
    state: dict[str, Any] = {
        "submitted_job_id": 100,
        "advance_to": 1,
        "total_pages": 3,
        "job_state": 5,  # processing
        "raise_compile_error": False,
        "raise_ipp_error": False,
    }

    pdf_target = tmp_path / "job.pdf"
    pdf_target.write_bytes(b"%PDF-fake")

    def fake_compile(source: str, workdir: Path, **kwargs: Any) -> CompileResult:
        if state["raise_compile_error"]:
            raise CompileError("syntax error in source", log_tail="! Undefined control sequence.")
        workdir.mkdir(parents=True, exist_ok=True)
        target = workdir / "job.pdf"
        target.write_bytes(b"%PDF-fake")
        return CompileResult(pdf_path=target, workdir=workdir, log_tail="")

    def fake_page_count(pdf: Path) -> int:
        return state["total_pages"]

    def fake_to_pwg(pdf: Path, pwg: Path, dpi: int, page_pixels: tuple[int, int]) -> None:
        pwg.write_bytes(b"RaS2-fake")

    def fake_page_to_png(pdf: Path, page: int, dpi: int = 100) -> bytes:
        return _TINY_PNG

    def fake_submit(uri: str, pwg_path: str, job_name: str, user: str, **_: Any) -> int:
        if state["raise_ipp_error"]:
            from printer_mcp.ipp import IppError
            raise IppError("simulated IPP failure")
        return state["submitted_job_id"]

    def fake_get_job_attrs(uri: str, job_id: int, user: str, **_: Any) -> dict[str, Any]:
        return {
            "job-id": job_id,
            "job-state": state["job_state"],
            "job-state-reasons": "job-printing",
            "job-impressions-completed": state["advance_to"],
        }

    def fake_get_printer_attrs(uri: str, user: str, **_: Any) -> dict[str, Any]:
        return {
            "printer-state": 3,
            "printer-state-reasons": "none",
            "printer-make-and-model": "Brother HL-L2865DW",
            "pages-per-minute": 34,
            "document-format-supported": ["image/pwg-raster", "image/urf"],
            "media-ready": "iso_a4_210x297mm",
            "marker-levels": 47,
            "marker-names": "BK",
            "marker-types": "toner",
            "output-bin-supported": ["mailbox-1"],
        }

    # Server imports each pipeline function into its own namespace — patch
    # there, not in the source module.
    monkeypatch.setattr(server, "compile_latex", fake_compile)
    monkeypatch.setattr(server, "page_count", fake_page_count)
    monkeypatch.setattr(server, "to_pwg", fake_to_pwg)
    monkeypatch.setattr(server, "page_to_png", fake_page_to_png)
    monkeypatch.setattr(server, "submit_pwg", fake_submit)
    monkeypatch.setattr(server, "get_job_attrs", fake_get_job_attrs)
    monkeypatch.setattr(server, "get_printer_attrs", fake_get_printer_attrs)
    # Speed up the poll loop in tests — production default is 0.5s.
    monkeypatch.setattr(server.CONFIG, "poll_interval_s", 0.001)
    return state


# ─── Health / metrics ─────────────────────────────────────────────────────────


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
    assert "printer_mcp_tool_calls_total" in r.text


# ─── Schema ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tools_list_advertises_print_latex_and_watch_page(
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
    assert tool_names >= {"print_latex", "watch_page"}
    schemas = {t["name"]: t["inputSchema"] for t in body["result"]["tools"]}
    assert "source" in schemas["print_latex"]["properties"]
    assert "copies" in schemas["print_latex"]["properties"]
    assert "job_id" in schemas["watch_page"]["properties"]
    assert "source" in schemas["print_latex"]["required"]
    assert "job_id" in schemas["watch_page"]["required"]
    assert "copies" not in schemas["print_latex"].get("required", [])
    # v1: job_id is an integer (IPP job-id), not a string.
    assert schemas["watch_page"]["properties"]["job_id"]["type"] == "integer"


# ─── Tool helpers ────────────────────────────────────────────────────────────


def _content_types(blocks: Any) -> list[str]:
    return [getattr(b, "type", None) or b["type"] for b in blocks]


def _content_text(block: Any) -> str:
    return getattr(block, "text", None) or block["text"]


def _content_image_mime(block: Any) -> str:
    return getattr(block, "mimeType", None) or block["mimeType"]


# ─── print_latex ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_print_latex_returns_text_plus_image_and_records_job(
    fake_pipeline: dict[str, Any],
) -> None:
    blocks = await mcp.call_tool(
        "print_latex", {"source": "\\documentclass{article}\\begin{document}x\\end{document}"}
    )
    assert _content_types(blocks) == ["text", "image"]
    payload = json.loads(_content_text(blocks[0]))
    assert payload["job_id"] == 100
    assert payload["total_pages"] == 3
    assert payload["copies"] == 1
    assert _content_image_mime(blocks[1]) == "image/png"

    # Job recorded in the store.
    job = JOBS.get(100)
    assert job is not None
    assert job.pages_seen == 1
    assert job.total_pages == 3


@pytest.mark.asyncio
async def test_print_latex_compile_failure_returns_log_tail_only(
    fake_pipeline: dict[str, Any],
) -> None:
    fake_pipeline["raise_compile_error"] = True
    blocks = await mcp.call_tool("print_latex", {"source": "\\broken"})
    assert _content_types(blocks) == ["text"]
    text = _content_text(blocks[0])
    assert "LaTeX compile failed" in text
    assert "Undefined control sequence" in text
    # No job was recorded — submission never happened.
    assert JOBS.all() == []


# ─── watch_page ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watch_page_walks_pages_in_order(fake_pipeline: dict[str, Any]) -> None:
    # Page 1 via print_latex.
    await mcp.call_tool("print_latex", {"source": "x"})

    fake_pipeline["advance_to"] = 2
    second = await mcp.call_tool("watch_page", {"job_id": 100})
    fake_pipeline["advance_to"] = 3
    third = await mcp.call_tool("watch_page", {"job_id": 100})

    # Both return text+image with correct page numbers.
    assert _content_types(second) == ["text", "image"]
    assert _content_types(third) == ["text", "image"]
    assert json.loads(_content_text(second[0]))["page"] == 2
    assert json.loads(_content_text(third[0]))["page"] == 3

    # Fourth call after the last page → text-only completion.
    fourth = await mcp.call_tool("watch_page", {"job_id": 100})
    assert _content_types(fourth) == ["text"]
    payload = json.loads(_content_text(fourth[0]))
    assert payload["status"] == "complete"
    assert payload["pages_seen"] == 3


@pytest.mark.asyncio
async def test_watch_page_unknown_job_returns_error_text(
    fake_pipeline: dict[str, Any],
) -> None:
    blocks = await mcp.call_tool("watch_page", {"job_id": 999})
    assert _content_types(blocks) == ["text"]
    assert "unknown job_id=999" in _content_text(blocks[0])


@pytest.mark.asyncio
async def test_watch_page_handles_premature_terminal_state(
    fake_pipeline: dict[str, Any],
) -> None:
    """Job aborted before all pages emerge — surface state, don't hang."""
    await mcp.call_tool("print_latex", {"source": "x"})
    # Printer aborts after page 1; impressions doesn't advance.
    fake_pipeline["job_state"] = 8  # aborted
    blocks = await mcp.call_tool("watch_page", {"job_id": 100})
    assert _content_types(blocks) == ["text"]
    assert "ended before page" in _content_text(blocks[0])
    # Stored terminal state reflects the abort.
    job = JOBS.get(100)
    assert job is not None
    assert job.terminal_state == "8"


# ─── Resources ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_resource_filters_to_status_fields(
    fake_pipeline: dict[str, Any],
) -> None:
    contents = await mcp.read_resource("printer://status")
    body = json.loads(_content_text_from_resource(contents))
    assert "printer-state" in body
    assert "marker-levels" in body
    assert "media-ready" in body
    # Capability-only fields should not appear here.
    assert "operations-supported" not in body
    assert "pages-per-minute" not in body


@pytest.mark.asyncio
async def test_capabilities_resource_includes_format_and_model(
    fake_pipeline: dict[str, Any],
) -> None:
    contents = await mcp.read_resource("printer://capabilities")
    body = json.loads(_content_text_from_resource(contents))
    assert body["printer-make-and-model"] == "Brother HL-L2865DW"
    assert "image/pwg-raster" in body["document-format-supported"]
    assert body["pages-per-minute"] == 34
    # Status-only fields should not appear.
    assert "printer-state" not in body


@pytest.mark.asyncio
async def test_history_resource_lists_jobs_newest_first(
    fake_pipeline: dict[str, Any],
) -> None:
    # Insert two jobs at distinct timestamps.
    from pathlib import Path as _P
    JOBS.add(
        Job(
            job_id=201,
            job_name="older",
            submitted_at=1000.0,
            workdir=_P("/tmp/x"),
            source_length=10,
            total_pages=1,
            copies=1,
        )
    )
    JOBS.add(
        Job(
            job_id=202,
            job_name="newer",
            submitted_at=2000.0,
            workdir=_P("/tmp/x"),
            source_length=10,
            total_pages=1,
            copies=1,
        )
    )
    contents = await mcp.read_resource("printer://history")
    body = json.loads(_content_text_from_resource(contents))
    assert [j["job_id"] for j in body] == [202, 201]


@pytest.mark.asyncio
async def test_job_resource_returns_record_and_live_attrs(
    fake_pipeline: dict[str, Any],
) -> None:
    await mcp.call_tool("print_latex", {"source": "x"})
    contents = await mcp.read_resource("printer://jobs/100")
    body = json.loads(_content_text_from_resource(contents))
    assert body["record"]["job_id"] == 100
    assert body["record"]["total_pages"] == 3
    assert body["live"]["job-state"] == 5


def _content_text_from_resource(contents: Any) -> str:
    """Extract the text body from a FastMCP read_resource response.

    FastMCP returns a list of ``ReadResourceContents`` records carrying
    ``content`` and ``mime_type``. Older versions returned tuples; newer
    versions return objects. Handle both.
    """
    first = contents[0] if isinstance(contents, (list, tuple)) else contents
    return getattr(first, "content", None) or first[0]
