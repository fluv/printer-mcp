"""Loader for the three synthetic page PNGs used by the verification stub.

The PNGs themselves live in ``stub_pages/`` as binary files so they don't
balloon the source diff. Each page is a ~A4-aspect rectangle with a large
numeral, a distinguishing background colour, and an explanation of why the
image exists, so a claude.ai user can tell which call produced which image.

Generated once via Pillow: 600×850 RGB canvas per page, DejaVuSans-Bold for
the numeral, distinct background per page (warm white / pale green / pale
blue), bordered rectangle, then ``Image.save(..., format="PNG", optimize=True)``.
This module is deleted by v1 — the real implementation renders pages via
``pdftoppm`` from the compiled PDF.
"""

from importlib.resources import files

_PAGE_COUNT = 3

STUB_PAGES: list[bytes] = [
    (files(__package__) / "stub_pages" / f"page-{i}.png").read_bytes()
    for i in range(1, _PAGE_COUNT + 1)
]
"""Decoded PNG bytes for each page. Read once at import time."""
