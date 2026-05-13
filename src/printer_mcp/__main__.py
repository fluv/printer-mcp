"""Entry point — run the ASGI app under uvicorn."""

import logging
import os

import uvicorn

from .server import SERVER_NAME, VERSION


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger(__name__).info(
        "starting %s v%s on :%d", SERVER_NAME, VERSION, port
    )
    uvicorn.run(
        "printer_mcp.server:app",
        host="0.0.0.0",
        port=port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
