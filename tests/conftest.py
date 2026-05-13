"""Shared fixtures.

FastMCP's `StreamableHTTPSessionManager.run()` may only be entered once per
instance, so the app lifespan has to span the whole test session rather than
being entered per-test. The httpx client comes along for the ride at session
scope so handlers see a single consistent ASGI transport.
"""

from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from asgi_lifespan import LifespanManager

from printer_mcp.server import app


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
