"""PDF inspection and rendering.

Three operations off the compiled PDF:

- ``page_count`` — IPP returns ``job-impressions = no-value`` (discussions/890),
  so total pages must come from us, not the printer.
- ``to_pwg`` — Ghostscript's ``pwgraster`` device produces the wire format the
  HL-L2865DW expects. The probe confirmed ``image/pwg-raster`` is the only
  practical document-format the printer accepts. ``cups-filters`` was rejected
  as an alternative because ghostscript was sufficient on its own.
- ``page_to_png`` — Used for the inline page image returned by
  ``print_latex`` / ``watch_page``. ``pdftoppm`` from poppler-utils is the
  shortest path; we render at 100dpi which is enough for the model to read
  the page back.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

PREVIEW_DPI = 100


def page_count(pdf: Path) -> int:
    """Count the pages in ``pdf`` via ``pdfinfo`` (poppler-utils)."""
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo is None:
        raise RuntimeError("pdfinfo not found; image missing poppler-utils?")
    proc = subprocess.run(
        [pdfinfo, str(pdf)], capture_output=True, text=True, timeout=10
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pdfinfo failed: {proc.stderr.strip()}")
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError(f"pdfinfo returned no Pages line:\n{proc.stdout}")


def to_pwg(pdf: Path, pwg: Path, dpi: int, page_pixels: tuple[int, int]) -> None:
    """Render ``pdf`` to a PWG-Raster file at ``pwg``.

    ``dpi`` and ``page_pixels`` should be consistent with each other and with
    the printer's native resolution — discussions/890 settled on 600dpi after
    the probe showed the HL-L2865DW upsamples internally otherwise. The two
    values aren't derived from each other inside this function so the caller
    can override either independently (handy for tests and tiny-pixel probes).
    """
    gs = shutil.which("gs")
    if gs is None:
        raise RuntimeError("ghostscript (gs) not found")
    width, height = page_pixels
    cmd = [
        gs,
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        "-sDEVICE=pwgraster",
        f"-r{dpi}",
        f"-g{width}x{height}",
        "-dPDFFitPage",
        f"-sOutputFile={pwg}",
        str(pdf),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"ghostscript pwgraster failed: {proc.stderr.strip()}")
    if not pwg.exists():
        raise RuntimeError("ghostscript pwgraster produced no output")


def page_to_png(pdf: Path, page: int, dpi: int = PREVIEW_DPI) -> bytes:
    """Render a single PDF page to PNG bytes (1-indexed page number).

    Writes to a tempfile inside the PDF's parent directory and reads it back.
    The seemingly simpler ``pdftoppm ... -`` (stdout) path silently produces
    empty output in poppler 25.x — the trailing ``-`` is parsed as a literal
    output prefix and discarded. Round-tripping through disk avoids that gotcha
    at the cost of a single tempfile in the existing per-job workdir.
    """
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise RuntimeError("pdftoppm not found; image missing poppler-utils?")
    out_prefix = pdf.parent / f"page-{page}"
    out_path = pdf.parent / f"page-{page}.png"
    cmd = [
        pdftoppm,
        "-png",
        "-r", str(dpi),
        "-f", str(page),
        "-l", str(page),
        "-singlefile",
        str(pdf),
        str(out_prefix),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(
            f"pdftoppm failed: {proc.stderr.decode(errors='replace').strip()}"
        )
    if not out_path.exists():
        raise RuntimeError(f"pdftoppm produced no output at {out_path}")
    return out_path.read_bytes()
