"""FastMCP verification-stub server.

The v0.1.0 scaffold raised ``NotImplementedError`` from both tools, which
prevented testing the open question from fluv/claude discussions/890 about
how claude.ai surfaces ``ImageContent`` and per-tool-call thinking blocks
chained inside one assistant turn. This module replaces the stubs with
known-content responses so that question can be answered before the v1
LaTeX/IPP pipeline is built.

Behaviour:

- ``print_latex`` ignores its arguments and returns a text result describing
  a fake job (``stub-job-1``) plus a synthetic PNG of "page 1".
- ``watch_page`` looks up the named job in a module-local counter, returns
  the next synthetic page PNG, and reports "no more pages" once the
  three-page fake job is exhausted.

When v1 lands these tools regain their real implementations and
``_stub_pages.py`` is deleted in the same change.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Annotated

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from . import __version__
from ._stub_pages import STUB_PAGES
from .metrics import REGISTRY, track_tool

SERVER_NAME = "printer-mcp"
VERSION = __version__
STUB_JOB_ID = "stub-job-1"
STUB_TOTAL_PAGES = len(STUB_PAGES)

# Stateless streamable-HTTP mode — see fluv/claude discussions/890 for why we
# don't keep MCP session state between calls. Each request carries everything
# the server needs.
#
# DNS-rebinding protection defaults off: the service sits behind kube +
# Tailscale and isn't reachable on the public internet, and FastMCP's default
# allowlist (localhost / 127.0.0.1) would reject legitimate cluster traffic.
# Override via MCP_DNS_REBINDING_PROTECTION=on if the deployment model ever
# changes to a public-facing ingress.
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

# How many pages of the fake job each known job_id has already revealed.
# Module-level so it survives across requests; protected by a lock because
# concurrent requests in stateless mode each get their own worker. Reset to
# zero on pod restart, which is fine — the verification is a manual loop and
# session-scoped.
_PAGES_SHOWN: dict[str, int] = {}
_PAGES_LOCK = threading.Lock()


def _image_for(page_index: int) -> Image:
    """Return the FastMCP ``Image`` for a zero-indexed page slot."""
    return Image(data=STUB_PAGES[page_index], format="png")


@mcp.tool(structured_output=False)
def print_latex(
    source: Annotated[str, Field(description="LaTeX source to compile and print.")],
    copies: Annotated[int, Field(description="Number of copies (default 1).", ge=1)] = 1,
) -> tuple[str, Image]:
    """Submit a print job and return page 1 inline (verification stub).

    The real implementation will compile the supplied LaTeX, submit it via
    IPP, and block until page 1 has physically left the printer. This stub
    ignores the source entirely and always returns the synthetic ``stub-job-1``
    fixture so the claude.ai rendering of ``ImageContent`` can be checked.
    """
    with track_tool("print_latex"):
        with _PAGES_LOCK:
            _PAGES_SHOWN[STUB_JOB_ID] = 1  # page 1 has been "revealed"
        payload = {
            "job_id": STUB_JOB_ID,
            "total_pages": STUB_TOTAL_PAGES,
            "stub": True,
            "note": (
                "verification-stub: ignores `source` and `copies`. Call "
                "watch_page(job_id) repeatedly to reveal pages 2 and 3."
            ),
            "received": {"copies": copies, "source_length": len(source)},
        }
        return json.dumps(payload), _image_for(0)


@mcp.tool(structured_output=False)
def watch_page(
    job_id: Annotated[str, Field(description="Job id returned by print_latex.")],
) -> tuple[str, Image] | str:
    """Reveal the next page of the named job (verification stub).

    Sequential calls walk through pages 2 and 3 of the synthetic fixture. A
    fourth call returns a plain-text "no more pages" message with no image,
    which is itself a useful claude.ai surface check.
    """
    with track_tool("watch_page"):
        with _PAGES_LOCK:
            shown = _PAGES_SHOWN.get(job_id, 0)
            if shown >= STUB_TOTAL_PAGES:
                return json.dumps(
                    {
                        "job_id": job_id,
                        "status": "complete",
                        "pages_revealed": shown,
                        "total_pages": STUB_TOTAL_PAGES,
                        "stub": True,
                    }
                )
            next_index = shown  # zero-based slot of the next page to reveal
            _PAGES_SHOWN[job_id] = shown + 1

        payload = {
            "job_id": job_id,
            "page": next_index + 1,
            "total_pages": STUB_TOTAL_PAGES,
            "stub": True,
        }
        return json.dumps(payload), _image_for(next_index)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": SERVER_NAME, "version": VERSION})


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics(_request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        generate_latest(REGISTRY).decode(),
        media_type=CONTENT_TYPE_LATEST,
    )


# FastMCP's streamable_http_app() returns a Starlette app with /mcp wired up
# plus a lifespan that initializes the session manager — using it directly
# keeps lifespan propagation working without a manual passthrough.
app = mcp.streamable_http_app()
