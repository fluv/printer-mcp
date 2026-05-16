FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src

RUN pip wheel --no-cache-dir --wheel-dir /wheels .


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# texlive — LaTeX compilation toolchain. texlive-pictures bundles tikz,
#           latexmk drives multi-pass compilation.
# poppler-utils — pdftoppm (per-page PNG render) and pdfinfo (page count).
# ghostscript — PDF → URF via the urfgray device (discussions/890; switched
#               from pwgraster after that pipeline rendered inverted).
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-recommended \
        texlive-fonts-recommended \
        texlive-pictures \
        latexmk \
        poppler-utils \
        ghostscript \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels printer-mcp \
    && rm -rf /wheels

RUN useradd -m -u 1000 -s /bin/sh app
USER app
WORKDIR /home/app

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "printer_mcp"]
