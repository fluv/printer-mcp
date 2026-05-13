"""Prometheus metrics for tool calls."""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry()

tool_calls_total = Counter(
    "printer_mcp_tool_calls_total",
    "Total MCP tool calls",
    labelnames=("tool", "outcome"),
    registry=REGISTRY,
)

tool_call_duration_seconds = Histogram(
    "printer_mcp_tool_call_duration_seconds",
    "MCP tool call duration",
    labelnames=("tool",),
    registry=REGISTRY,
)


@contextmanager
def track_tool(name: str) -> Iterator[None]:
    """Record an MCP tool invocation in Prometheus.

    Sets the outcome label to ``error`` by default; flips to ``ok`` only if the
    wrapped block completes without raising. Duration is observed in either
    case so the histogram reflects real wall time, including failures.
    """
    start = time.monotonic()
    outcome = "error"
    try:
        yield
        outcome = "ok"
    finally:
        tool_calls_total.labels(tool=name, outcome=outcome).inc()
        tool_call_duration_seconds.labels(tool=name).observe(time.monotonic() - start)
