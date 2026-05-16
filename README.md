printer-mcp
===========

MCP server that compiles LaTeX, submits to a Brother HL-L2865DW via IPP, and
streams per-page PNG renders back to the model as the printer announces each
page complete.

Design notes: [fluv/claude discussions/890](https://github.com/fluv/claude/discussions/890).
Implementation tracker: [fluv/claude#892](https://github.com/fluv/claude/issues/892).

Status
------

`v0.2.0` — verification stub. `print_latex` and `watch_page` ignore their
arguments and return synthetic page PNGs, so the open question from
[discussions/890](https://github.com/fluv/claude/discussions/890#open-questions-on-client-support)
about claude.ai's rendering of `ImageContent` and chained tool-call thinking
blocks can be answered before the real LaTeX/IPP pipeline is written. The
v1 PR replaces both tools with the real implementations and deletes
`_stub_pages.py`.

The container still ships with `texlive-latex-recommended`,
`texlive-pictures` (for tikz), and `poppler-utils` (for `pdftoppm`) so the
LaTeX/PDF layer is in place ready for v1.

Tools
-----

- `print_latex(source, copies)` — in v1, compiles LaTeX, submits via IPP,
  blocks until page 1 is physically out, returns `{ job_id, total_pages }`
  plus a PNG of page 1 inline. In v0.2.0, returns the synthetic
  `stub-job-1` fixture instead.
- `watch_page(job_id)` — in v1, blocks until the next page is physically
  out and returns its PNG. In v0.2.0, reveals pages 2 and 3 of the
  synthetic fixture; a fourth call returns text-only "no more pages".

Run locally
-----------

```
pip install -e .[dev]
printer-mcp
```

Server listens on `:8080`. MCP endpoint at `/mcp`, healthcheck at `/healthz`,
metrics at `/metrics`.

Tests
-----

```
pytest -q
```

The test suite exercises the MCP protocol layer (JSON-RPC over the
streamable-HTTP transport against the ASGI app), rather than calling tool
functions directly. This catches schema-inference bugs that bypass unit-test
paths.
