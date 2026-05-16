"""Environment-driven configuration.

The printer URI, render resolution, and timeouts are all overridable so the
same image can run against a different printer, against a test fixture, or
against a CUPS shim during development without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Runtime configuration resolved from environment variables."""

    printer_uri: str
    """Full IPP URI of the target printer, e.g. ``ipp://192.168.1.251/ipp/print``."""

    pwg_dpi: int
    """Resolution for PDF→PWG-Raster conversion. The HL-L2865DW reports
    600dpi as its native resolution; submitting at a lower DPI causes the
    printer to upsample internally with a quality cost. See discussions/890."""

    page_size: tuple[int, int]
    """PWG-Raster output pixel dimensions ``(width, height)``. Defaults to A4
    at ``pwg_dpi``."""

    first_page_timeout_s: float
    """Seconds to wait for ``job-impressions-completed >= 1`` after submitting
    a job. The probe measured ~14s cold-fuser warmup on the HL-L2865DW, so the
    default leaves headroom for a stuck job to fail visibly."""

    next_page_timeout_s: float
    """Seconds to wait for ``job-impressions-completed`` to advance during
    ``watch_page``. Inter-page interval at 34ppm is ~1.8s; the default is
    generous so a paper jam fails the call rather than hanging forever."""

    poll_interval_s: float
    """How often to poll Get-Job-Attributes while blocking on a counter."""

    requesting_user_name: str
    """Sent as ``requesting-user-name`` on every IPP operation. Cosmetic but
    shows up in the printer's job log."""


def _a4_pixels(dpi: int) -> tuple[int, int]:
    # A4 = 8.27" × 11.69"
    return round(8.2677 * dpi), round(11.6929 * dpi)


def load_config() -> Config:
    """Build a Config from environment variables, with sensible defaults."""
    dpi = int(os.environ.get("PRINTER_MCP_PWG_DPI", "600"))
    return Config(
        printer_uri=os.environ.get("PRINTER_MCP_URI", "ipp://192.168.1.251/ipp/print"),
        pwg_dpi=dpi,
        page_size=_a4_pixels(dpi),
        first_page_timeout_s=float(os.environ.get("PRINTER_MCP_FIRST_PAGE_TIMEOUT", "60")),
        next_page_timeout_s=float(os.environ.get("PRINTER_MCP_NEXT_PAGE_TIMEOUT", "60")),
        poll_interval_s=float(os.environ.get("PRINTER_MCP_POLL_INTERVAL", "0.5")),
        requesting_user_name=os.environ.get("PRINTER_MCP_USER", "printer-mcp"),
    )
