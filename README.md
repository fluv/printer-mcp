printer-mcp
===========

MCP server that compiles LaTeX, submits to a Brother HL-L2865DW via IPP, and
streams per-page PNG renders back to the model as the printer announces each
page complete.

Design notes: [fluv/claude discussions/890](https://github.com/fluv/claude/discussions/890).
Implementation tracker: [fluv/claude#892](https://github.com/fluv/claude/issues/892).

Status
------

`v0.1.0` — scaffold only. `print_latex` and `watch_page` are stubs that raise
`NotImplementedError`. The container ships with `texlive-latex-recommended`,
`texlive-pictures` (for tikz), and `poppler-utils` (for `pdftoppm`) so the
LaTeX/PDF layer is in place ready for the implementation work.

Tools
-----

- `print_latex(source, copies)` — compiles LaTeX, submits via IPP, blocks
  until page 1 is physically out, returns `{ job_id, total_pages }` plus a
  PNG of page 1 inline.
- `watch_page(job_id)` — blocks until the next page is physically out and
  returns its PNG. Called repeatedly to cover remaining pages.

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
