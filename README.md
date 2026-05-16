printer-mcp
===========

MCP server that compiles LaTeX, submits to a Brother HL-L2865DW via IPP, and
streams per-page PNG renders back to the model as each physical sheet ejects.

Design notes: [fluv/claude discussions/890](https://github.com/fluv/claude/discussions/890).
Implementation tracker: [fluv/claude#892](https://github.com/fluv/claude/issues/892).

Status
------

`v1.1.0` — production pipeline. LaTeX → PDF (latexmk) → URF
(ghostscript `urfgray`) → IPP (hand-rolled client) → polled `job-impressions-completed`
per physical sheet. Two tools (`print_latex`, `watch_page`) plus four
resources (`printer://status`, `printer://capabilities`,
`printer://jobs/<id>`, `printer://history`). The shipped skill
(`.claude/skills/printer/SKILL.md`) biases the model toward narrating each
page as it arrives.

Tools
-----

- `print_latex(source, copies)` — compiles LaTeX, submits via IPP,
  blocks until page 1 is physically out (~14s cold fuser, ~5s warm),
  returns `{ job_id, total_pages, first_page_seconds }` plus a PNG of
  page 1 inline. LaTeX compile failures return the texlive log tail
  without submitting.
- `watch_page(job_id)` — blocks until `job-impressions-completed`
  advances, returns the next page's PNG. After the final page, returns
  a text-only completion payload.

Resources
---------

- `printer://status` — printer-state, marker levels, paper, queued jobs.
- `printer://capabilities` — model, supported formats, ppm, resolutions.
- `printer://jobs/<id>` — our pod-local record + live IPP attributes.
- `printer://history` — all jobs submitted since this pod started.

Job history is in-memory only — pod restart wipes it. Persistent
storage was considered and rejected during design (discussions/890); the
cost of an accidental re-print is one sheet of paper, well under the cost
of carrying SQLite + a PVC.

Configuration
-------------

Environment variables (see `src/printer_mcp/config.py`):

| Var | Default | Purpose |
|---|---|---|
| `PRINTER_MCP_URI` | `ipp://192.168.1.251/ipp/print` | Target printer IPP URI |
| `PRINTER_MCP_RASTER_DPI` | `600` | URF render resolution; HL-L2865DW native is 600dpi. Legacy `PRINTER_MCP_PWG_DPI` honoured as fallback. |
| `PRINTER_MCP_FIRST_PAGE_TIMEOUT` | `60` | Max wait for page 1 (cold fuser ~14s) |
| `PRINTER_MCP_NEXT_PAGE_TIMEOUT` | `60` | Max wait for subsequent pages (~1.7s warm) |
| `PRINTER_MCP_POLL_INTERVAL` | `0.5` | Get-Job-Attributes poll cadence |
| `PRINTER_MCP_USER` | `printer-mcp` | requesting-user-name on IPP operations |

Physical setup
--------------

The printer's rear straight-through paper path should be enabled for
correct page order (face-up output). The default face-down top tray
reverses the read order. This is a physical setting on the unit — the
MCP doesn't change the output-bin selection at the IPP layer.

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

The protocol-layer tests speak real MCP JSON-RPC over httpx's ASGI
transport against the FastMCP app, with all subprocess-driven dependencies
(latex, pdf, ipp) monkeypatched to deterministic fakes. End-to-end against
the physical printer is a manual integration step.
