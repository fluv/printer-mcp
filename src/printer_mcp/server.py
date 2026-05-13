"""FastMCP server with stub tools, plus /healthz and /metrics as custom routes."""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from . import __version__
from .metrics import REGISTRY, track_tool

SERVER_NAME = "printer-mcp"
VERSION = __version__

# Stateless streamable-HTTP mode — see fluv/claude discussions/890 for why we
# don't keep MCP session state between calls. Each request carries everything
# the server needs.
#
# DNS-rebinding protection is disabled: the service sits behind kube + Tailscale
# (see kube manifests for the printer-mcp deployment) and isn't reachable on
# the public internet. The default allowlist (localhost/127.0.0.1) would reject
# legitimate traffic from the cluster's ingress hostnames.
mcp = FastMCP(
    SERVER_NAME,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

_STUB_MESSAGE = (
    "printer-mcp v0.1.0 is a scaffold. Tool implementations land separately — "
    "see fluv/claude#892 for the roadmap."
)


@mcp.tool()
def print_latex(
    source: Annotated[str, Field(description="LaTeX source to compile and print.")],
    copies: Annotated[int, Field(description="Number of copies (default 1).", ge=1)] = 1,
) -> dict:
    """Compile LaTeX, submit via IPP, block until page 1 is physically out.

    Returns ``{ job_id, total_pages }`` plus a PNG of page 1 inline. The PNG
    is the only way to see what came out — there is no pre-print preview by
    design.
    """
    with track_tool("print_latex"):
        raise NotImplementedError(_STUB_MESSAGE)


@mcp.tool()
def watch_page(
    job_id: Annotated[str, Field(description="Job id returned by print_latex.")],
) -> dict:
    """Block until the next page of the given job is physically out of the printer.

    Returns the PNG render of that page. Call repeatedly to cover remaining
    pages; the call order determines page order.
    """
    with track_tool("watch_page"):
        raise NotImplementedError(_STUB_MESSAGE)


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
