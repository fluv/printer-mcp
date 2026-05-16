"""FastMCP server for printer-mcp v1.

Orchestrates the pipeline drafted in discussions/890:

    LaTeX source ─► latexmk ─► PDF ─► gs pwgraster ─► PWG ─► IPP Print-Job
                                                                │
                                                                ▼
        ◄──── per-sheet ────  Get-Job-Attributes polling on `job-impressions-completed`

Two tools (``print_latex``, ``watch_page``) and four resources
(``printer://status``, ``printer://capabilities``, ``printer://jobs/<id>``,
``printer://history``) make up the surface. Blocking behaviour: ``print_latex``
holds the request open until the first sheet is physically out (~14s cold);
``watch_page`` holds open until the next sheet ejects (~1.7s warm). Both
respect a configurable timeout and surface IPP errors as tool errors rather
than silent hangs.

The server is stateless across requests at the MCP layer but maintains a
pod-lifetime in-memory job table (``JobStore``) so ``watch_page`` and the
``printer://jobs/<id>`` resource can answer for jobs submitted earlier in the
same pod lifetime. Pod restart wipes history — explicit choice per the
design discussion; the alternative (SQLite + PVC) was judged complexity not
warranted by the use case.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from . import __version__
from .config import load_config
from .ipp import (
    TERMINAL_JOB_STATES,
    IppError,
    get_job_attrs,
    get_printer_attrs,
    submit_pwg,
)
from .jobs import Job, JobStore, now
from .latex import CompileError, compile_latex
from .metrics import REGISTRY, track_tool
from .pdf import page_count, page_to_png, to_pwg

log = logging.getLogger(__name__)

SERVER_NAME = "printer-mcp"
VERSION = __version__

CONFIG = load_config()
JOBS = JobStore()

# DNS-rebinding off by default — see discussions/890 § Implementation notes.
_DNS_REBINDING_ON = os.environ.get("MCP_DNS_REBINDING_PROTECTION", "off").strip().lower() in (
    "on", "1", "true", "yes",
)

mcp = FastMCP(
    SERVER_NAME,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=_DNS_REBINDING_ON,
    ),
)


# ─── Status / capability split ────────────────────────────────────────────────
#
# Get-Printer-Attributes returns ~80 fields in one shot; the two resources
# slice it into "what's the printer doing right now" vs "what is it capable of".
# Tested empirically against the HL-L2865DW (discussions/890 probe).

_STATUS_FIELDS = frozenset({
    "printer-state",
    "printer-state-reasons",
    "printer-state-message",
    "printer-is-accepting-jobs",
    "queued-job-count",
    "marker-names",
    "marker-types",
    "marker-levels",
    "marker-low-levels",
    "marker-high-levels",
    "marker-colors",
    "media-ready",
    "printer-input-tray",
    "printer-output-tray",
    "printer-up-time",
    "printer-current-time",
})

_CAPABILITY_FIELDS = frozenset({
    "printer-make-and-model",
    "printer-name",
    "printer-uri-supported",
    "printer-info",
    "printer-location",
    "printer-firmware-version",
    "ipp-versions-supported",
    "operations-supported",
    "document-format-supported",
    "document-format-preferred",
    "document-format-default",
    "pages-per-minute",
    "pages-per-minute-color",
    "color-supported",
    "sides-supported",
    "media-supported",
    "media-default",
    "media-source-supported",
    "output-bin-supported",
    "output-bin-default",
    "printer-resolution-supported",
    "printer-resolution-default",
    "compression-supported",
    "finishings-supported",
    "copies-supported",
})


def _filter_attrs(attrs: dict[str, Any], wanted: frozenset[str]) -> dict[str, Any]:
    return {k: _jsonify(v) for k, v in attrs.items() if k in wanted}


def _jsonify(value: Any) -> Any:
    """Coerce IPP attribute values to JSON-friendly types."""
    if isinstance(value, bytes):
        # Unknown-tag raw bytes — render as hex so they show up usefully in resources.
        return value.hex()
    if isinstance(value, list):
        return [_jsonify(v) for v in value]
    return value


# ─── Pipeline glue ────────────────────────────────────────────────────────────


def _new_workdir() -> Path:
    base = Path(tempfile.gettempdir()) / "printer-mcp"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"job-{uuid.uuid4().hex[:8]}-", dir=base))


def _block_for_impressions(job_id: int, target: int, timeout_s: float) -> dict[str, Any]:
    """Poll Get-Job-Attributes until impressions reach target or job ends.

    Returns the most recent attrs dict. Raises IppError if the IPP transport
    breaks; raises TimeoutError on timeout. The caller distinguishes "reached
    target" from "job ended before target" by checking ``job-state`` against
    ``TERMINAL_JOB_STATES`` in the returned attrs.
    """
    deadline = time.monotonic() + timeout_s
    attrs: dict[str, Any] = {}
    while time.monotonic() < deadline:
        attrs = get_job_attrs(CONFIG.printer_uri, job_id, CONFIG.requesting_user_name)
        impressions = attrs.get("job-impressions-completed", 0)
        state = attrs.get("job-state")
        if isinstance(impressions, int) and impressions >= target:
            return attrs
        if state in TERMINAL_JOB_STATES:
            return attrs
        time.sleep(CONFIG.poll_interval_s)
    raise TimeoutError(
        f"job {job_id}: timed out waiting for impressions >= {target} "
        f"({CONFIG.poll_interval_s:.1f}s poll, {timeout_s:.0f}s budget)"
    )


# ─── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool(structured_output=False)
def print_latex(
    source: Annotated[str, Field(description="LaTeX source to compile and print.")],
    copies: Annotated[int, Field(description="Number of copies (default 1).", ge=1)] = 1,
) -> tuple[str, Image] | str:
    """Compile LaTeX, submit to the printer, block until page 1 is out.

    On success returns a JSON text payload (``job_id``, ``total_pages``,
    timing) alongside a PNG of page 1 rendered from the same PDF that was
    submitted — that image is the model's first look at what came out, which
    arrives at roughly the same moment Douglas sees the physical sheet emerge.

    On compile failure returns the LaTeX log tail as plain text and submits
    nothing. On IPP failure raises so the tool reports an error rather than
    pretending success.
    """
    with track_tool("print_latex"):
        workdir = _new_workdir()
        # The workdir is only preserved once the job is recorded in JOBS —
        # watch_page needs the PDF to render subsequent pages. Until that
        # point any exit path cleans up; flip ``preserve_workdir`` only after
        # JOBS.add succeeds.
        preserve_workdir = False
        try:
            try:
                compiled = compile_latex(source, workdir)
            except CompileError as exc:
                log.info("LaTeX compile failed: %s", exc)
                return f"LaTeX compile failed: {exc}\n\n--- log tail ---\n{exc.log_tail}"

            total_pages = page_count(compiled.pdf_path)
            pwg_path = workdir / "job.pwg"
            to_pwg(compiled.pdf_path, pwg_path, CONFIG.pwg_dpi, CONFIG.page_size)

            job_name = f"printer-mcp/{uuid.uuid4().hex[:6]}"
            submit_t = now()
            job_id = submit_pwg(
                CONFIG.printer_uri,
                str(pwg_path),
                job_name=job_name,
                user=CONFIG.requesting_user_name,
            )
            JOBS.add(
                Job(
                    job_id=job_id,
                    job_name=job_name,
                    submitted_at=submit_t,
                    workdir=workdir,
                    source_length=len(source),
                    total_pages=total_pages,
                    copies=copies,
                )
            )
            preserve_workdir = True
            log.info(
                "submitted job-id=%d total-pages=%d job-name=%s",
                job_id, total_pages, job_name,
            )

            # Block until the first physical sheet is out.
            attrs = _block_for_impressions(
                job_id, target=1, timeout_s=CONFIG.first_page_timeout_s
            )
            state = attrs.get("job-state")
            impressions = attrs.get("job-impressions-completed", 0)
            JOBS.update(job_id, pages_seen=impressions)
            if state in TERMINAL_JOB_STATES and impressions < 1:
                reasons = attrs.get("job-state-reasons", "")
                JOBS.update(
                    job_id,
                    terminal_state=str(state),
                    last_error=f"job ended before any page printed: {reasons}",
                    completed_at=now(),
                )
                return (
                    f"job {job_id} ended before any page printed "
                    f"(state={state}, reasons={reasons})"
                )

            png = page_to_png(compiled.pdf_path, 1)
            elapsed = now() - submit_t
            payload = {
                "job_id": job_id,
                "total_pages": total_pages,
                "copies": copies,
                "job_name": job_name,
                "first_page_seconds": round(elapsed, 2),
                "note": (
                    f"Page 1 is out. Call watch_page({job_id}) to reveal "
                    f"page 2{' onwards' if total_pages > 2 else ''}."
                    if total_pages > 1
                    else "Single-page job; nothing further to watch."
                ),
            }
            return json.dumps(payload), Image(data=png, format="png")
        finally:
            # Workdir is cleaned up on any exit path that didn't successfully
            # register the job — compile error, IPP failure, mid-flight raise.
            # Once preserve_workdir flips, the lifecycle is owned by the job
            # record and watch_page can find the PDF.
            if not preserve_workdir:
                shutil.rmtree(workdir, ignore_errors=True)


@mcp.tool(structured_output=False)
def watch_page(
    job_id: Annotated[
        int, Field(description="Job id returned by print_latex.")
    ],
) -> tuple[str, Image] | str:
    """Block until the next page of the named job emerges, return its image.

    Sequential calls walk forward through the job. Once all pages have been
    revealed (or the job ends), returns a plain-text completion payload with
    no image — the model is meant to stop calling.
    """
    with track_tool("watch_page"):
        job = JOBS.get(job_id)
        if job is None:
            return f"unknown job_id={job_id}; was it submitted in this pod's lifetime?"
        if job.terminal_state is not None and job.pages_seen >= job.total_pages:
            return json.dumps({
                "job_id": job_id,
                "status": "complete",
                "pages_seen": job.pages_seen,
                "total_pages": job.total_pages,
                "terminal_state": job.terminal_state,
            })

        target = job.pages_seen + 1
        if target > job.total_pages:
            JOBS.update(job_id, completed_at=now(), terminal_state="completed")
            return json.dumps({
                "job_id": job_id,
                "status": "complete",
                "pages_seen": job.pages_seen,
                "total_pages": job.total_pages,
            })

        attrs = _block_for_impressions(
            job_id, target=target, timeout_s=CONFIG.next_page_timeout_s
        )
        impressions = attrs.get("job-impressions-completed", job.pages_seen)
        state = attrs.get("job-state")
        # We may have advanced past target if the printer was very fast — clamp
        # to total_pages so we don't try to render a non-existent page.
        impressions = min(impressions, job.total_pages)
        JOBS.update(job_id, pages_seen=impressions)

        if impressions < target and state in TERMINAL_JOB_STATES:
            reasons = attrs.get("job-state-reasons", "")
            JOBS.update(
                job_id,
                terminal_state=str(state),
                last_error=f"job ended at page {impressions} before {target}: {reasons}",
                completed_at=now(),
            )
            return (
                f"job {job_id} ended before page {target} "
                f"(state={state}, reasons={reasons}, pages_seen={impressions})"
            )

        png = page_to_png(job.workdir / "job.pdf", target)
        if impressions >= job.total_pages or state in TERMINAL_JOB_STATES:
            JOBS.update(job_id, terminal_state="completed", completed_at=now())
        payload = {
            "job_id": job_id,
            "page": target,
            "total_pages": job.total_pages,
            "pages_seen": impressions,
        }
        return json.dumps(payload), Image(data=png, format="png")


# ─── Resources ────────────────────────────────────────────────────────────────


@mcp.resource("printer://status")
def resource_status() -> str:
    """Current printer state, marker levels, paper, output tray."""
    attrs = get_printer_attrs(CONFIG.printer_uri, CONFIG.requesting_user_name)
    return json.dumps(_filter_attrs(attrs, _STATUS_FIELDS), indent=2, sort_keys=True)


@mcp.resource("printer://capabilities")
def resource_capabilities() -> str:
    """Model, supported formats, ppm, output bins, resolutions."""
    attrs = get_printer_attrs(CONFIG.printer_uri, CONFIG.requesting_user_name)
    return json.dumps(_filter_attrs(attrs, _CAPABILITY_FIELDS), indent=2, sort_keys=True)


@mcp.resource("printer://jobs/{job_id}")
def resource_job(job_id: str) -> str:
    """Per-job state: our record + live IPP attributes if the printer still has them."""
    try:
        ipp_id = int(job_id)
    except ValueError:
        return json.dumps({"error": f"invalid job_id: {job_id!r}"})
    job = JOBS.get(ipp_id)
    record = None
    if job is not None:
        record = {
            "job_id": job.job_id,
            "job_name": job.job_name,
            "submitted_at": job.submitted_at,
            "completed_at": job.completed_at,
            "total_pages": job.total_pages,
            "pages_seen": job.pages_seen,
            "copies": job.copies,
            "source_length": job.source_length,
            "terminal_state": job.terminal_state,
            "last_error": job.last_error,
        }
    live: dict[str, Any] | None
    try:
        live = {
            k: _jsonify(v)
            for k, v in get_job_attrs(
                CONFIG.printer_uri, ipp_id, CONFIG.requesting_user_name
            ).items()
            if k in {
                "job-state",
                "job-state-reasons",
                "job-impressions-completed",
                "job-name",
                "time-at-creation",
                "time-at-completed",
            }
        }
    except IppError as exc:
        live = {"error": str(exc)}
    return json.dumps({"record": record, "live": live}, indent=2, sort_keys=True)


@mcp.resource("printer://history")
def resource_history() -> str:
    """All jobs submitted since this pod started, newest first."""
    return json.dumps(
        [
            {
                "job_id": j.job_id,
                "job_name": j.job_name,
                "submitted_at": j.submitted_at,
                "completed_at": j.completed_at,
                "total_pages": j.total_pages,
                "pages_seen": j.pages_seen,
                "terminal_state": j.terminal_state,
                "last_error": j.last_error,
            }
            for j in JOBS.all()
        ],
        indent=2,
        sort_keys=True,
    )


# ─── Health / metrics ─────────────────────────────────────────────────────────


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": SERVER_NAME, "version": VERSION})


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics(_request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        generate_latest(REGISTRY).decode(),
        media_type=CONTENT_TYPE_LATEST,
    )


app = mcp.streamable_http_app()
