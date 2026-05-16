"""LaTeX → PDF compilation via ``latexmk``.

The point of choosing LaTeX as the input format (discussions/890) is that the
quirks produce typographically beautiful output with enough room for endearing
mistakes when Claude gets it wrong. Compilation failures are returned as
structured errors carrying the texlive log so the caller can surface them to
the model rather than silently submitting an empty job.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_COMPILE_TIMEOUT_S = 60.0


@dataclass
class CompileResult:
    pdf_path: Path
    workdir: Path
    log_tail: str


class CompileError(RuntimeError):
    """LaTeX compilation failed; ``log_tail`` carries the relevant texlive output."""

    def __init__(self, message: str, log_tail: str) -> None:
        super().__init__(message)
        self.log_tail = log_tail


def compile_latex(
    source: str,
    workdir: Path,
    timeout_s: float = DEFAULT_COMPILE_TIMEOUT_S,
) -> CompileResult:
    """Compile ``source`` to a PDF inside ``workdir``.

    Workdir is owned by the caller — typically a per-job tempdir that survives
    only as long as the job state. ``latexmk`` is used over a bare ``pdflatex``
    call because it handles multi-pass compilation (references, citations,
    ToC) and stops cleanly on terminal errors.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    tex_path = workdir / "job.tex"
    tex_path.write_text(source, encoding="utf-8")

    latexmk = shutil.which("latexmk")
    if latexmk is None:
        raise CompileError(
            "latexmk not found on PATH; image missing texlive packages?",
            log_tail="",
        )

    cmd = [
        latexmk,
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        f"-output-directory={workdir}",
        str(tex_path),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=workdir,
        timeout=timeout_s,
    )
    log_path = workdir / "job.log"
    log_tail = ""
    if log_path.exists():
        log_tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-40:])
    else:
        log_tail = proc.stderr or proc.stdout

    pdf_path = workdir / "job.pdf"
    if proc.returncode != 0 or not pdf_path.exists():
        raise CompileError(
            f"latexmk exited with code {proc.returncode}",
            log_tail=log_tail,
        )
    return CompileResult(pdf_path=pdf_path, workdir=workdir, log_tail=log_tail)
