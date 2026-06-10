"""Tracing for dj: trace() spans + per-call cost/usage to Langfuse.

Inlined from the retired agent-core substrate (ADR-0004). If Langfuse keys are
set, spans and generations are recorded; if not, everything no-ops so the agent
runs the same with or without observability. We log generations ourselves
because dj.llm talks to the Anthropic SDK directly.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import time
from collections.abc import Iterator
from typing import Any

_ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))

_active_span: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "active_span", default=None
)


@contextlib.contextmanager
def trace(name: str, **metadata: Any) -> Iterator[dict[str, Any]]:
    """Span context manager. Records duration, cost, and metadata.

    Usage:
        with trace("curate-track", path=path) as span:
            span["result"] = do_work()
    """
    span: dict[str, Any] = {
        "name": name,
        "metadata": dict(metadata),
        "generations": [],
        "cost_usd": 0.0,
    }
    token = _active_span.set(span)
    start = time.perf_counter()
    try:
        yield span
    finally:
        _active_span.reset(token)
        span["duration_s"] = round(time.perf_counter() - start, 3)
        if _ENABLED:
            _emit(span)


def log_generation(*, model: str, usage: dict[str, int], cost: float) -> None:
    """Attach one model call's usage + cost to the active span (if any)."""
    span = _active_span.get()
    if span is not None:
        span["generations"].append({"model": model, **usage, "cost_usd": round(cost, 6)})
        span["cost_usd"] = round(span["cost_usd"] + cost, 6)


def _emit(span: dict[str, Any]) -> None:  # pragma: no cover - thin I/O wrapper
    try:
        from langfuse import Langfuse

        lf = Langfuse()
        lf.trace(
            name=span["name"],
            metadata={
                **span.get("metadata", {}),
                "duration_s": span.get("duration_s"),
                "cost_usd": span.get("cost_usd"),
                "generations": span.get("generations"),
            },
        )
    except Exception:
        # Never let observability break the run.
        pass


def enabled() -> bool:
    return _ENABLED
