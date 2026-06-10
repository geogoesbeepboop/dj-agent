"""Model access for dj: ask for a *tier*, get text back. Anthropic-only.

Inlined from the retired `agent-core` substrate (see that repo's ADR-0004 — dj
was its one real consumer, so the ~2 functions dj actually used now live here).
We talk to the Anthropic SDK directly; a tier (cheap/mid/hard) maps to a model
id so the agents never hardcode one. Anthropic prompt caching is on by default
for the (large, reused) system block. We own the retry loop and disable the
SDK's, so there's no double-retry.

Deliberately Anthropic-only: dj routes every LLM step to Claude. To add an
OpenAI-compatible / local backend later, branch on the model id (the way
agent-core did) and add an OpenAI client — it's ~20 lines.
"""

from __future__ import annotations

import os
import time
from typing import Any

from dotenv import load_dotenv

from dj.tracing import log_generation

load_dotenv()

Message = dict[str, str]
Usage = dict[str, int]

# tier -> model id (override via env). dj routes everything to Claude.
_MODELS = {
    "cheap": os.getenv("DJ_MODEL_CHEAP", "claude-haiku-4-5"),
    "mid": os.getenv("DJ_MODEL_MID", "claude-sonnet-4-6"),
    "hard": os.getenv("DJ_MODEL_HARD", "claude-opus-4-8"),
}

# USD per 1M tokens (input, output, cache_read, cache_write) — best-effort cost
# logging for traces. Anthropic only; an unpriced model logs $0.
_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30, 3.75),
    "claude-opus-4-8": (15.00, 75.00, 1.50, 18.75),
}

_MAX_RETRIES = int(os.getenv("DJ_MAX_RETRIES", "3"))
_RETRY_BASE_DELAY = float(os.getenv("DJ_RETRY_BASE_DELAY", "1.0"))


class TransientLLMError(Exception):
    """A retryable failure (rate limit, overload, timeout, connection, 5xx)."""


def complete(
    messages: list[Message],
    tier: str = "mid",
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    cache: bool = True,
    **kwargs: Any,
) -> str:
    """Run a chat completion at the requested tier; return the text.

    messages: OpenAI-style [{"role": "system"|"user"|"assistant", "content": str}].
    tier: cheap/mid/hard (maps to a Claude model) or a raw model id.
    Retries transient failures with exponential backoff; logs usage + best-effort
    cost to the active trace span (if any).
    """
    model = _MODELS.get(tier, tier)
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            text, usage = _anthropic_complete(model, messages, temperature, max_tokens, cache, kwargs)
            log_generation(model=model, usage=usage, cost=_cost(model, usage))
            return text
        except TransientLLMError as exc:
            last_exc = exc
            if attempt >= _MAX_RETRIES:
                break
            time.sleep(_RETRY_BASE_DELAY * (2**attempt))
    assert last_exc is not None
    raise last_exc


def _anthropic_messages(
    messages: list[Message], cache: bool
) -> tuple[list[dict[str, Any]] | None, list[dict[str, str]]]:
    """Split OpenAI-style messages into an Anthropic (system block, conversation).

    System turns merge into one text block; with caching on it carries a
    cache_control breakpoint so the stable prefix is billed at the cache rate.
    """
    system_parts: list[str] = []
    convo: list[dict[str, str]] = []
    for m in messages:
        if m.get("role") == "system":
            system_parts.append(m["content"])
        else:
            convo.append({"role": m["role"], "content": m["content"]})
    system_param: list[dict[str, Any]] | None = None
    if system_parts:
        block: dict[str, Any] = {"type": "text", "text": "\n\n".join(system_parts)}
        if cache:
            block["cache_control"] = {"type": "ephemeral"}
        system_param = [block]
    return system_param, convo


def _anthropic_complete(
    model: str, messages: list[Message], temperature: float, max_tokens: int, cache: bool, kwargs: dict[str, Any]
) -> tuple[str, Usage]:
    from anthropic import (
        APIConnectionError,
        APIError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    system_param, convo = _anthropic_messages(messages, cache)
    call: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": convo,
        **kwargs,
    }
    if system_param is not None:
        call["system"] = system_param
    try:
        resp = _client().messages.create(**call)
    except (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError) as exc:
        raise TransientLLMError(str(exc)) from exc
    except APIError as exc:
        status = getattr(exc, "status_code", None)
        if status and status >= 500:
            raise TransientLLMError(str(exc)) from exc
        raise

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    u = resp.usage
    usage: Usage = {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }
    return text, usage


_ANTHROPIC: Any = None


def _client() -> Any:
    global _ANTHROPIC
    if _ANTHROPIC is None:
        from anthropic import Anthropic

        # max_retries=0: we own the retry loop in complete() — don't double-retry.
        _ANTHROPIC = Anthropic(max_retries=0)
    return _ANTHROPIC


def _cost(model: str, usage: Usage) -> float:
    """Estimate USD cost from token usage (0 for unpriced models)."""
    p = _PRICES.get(model)
    if p is None:
        return 0.0
    inp, out, cr, cw = p
    return (
        usage["input_tokens"] * inp
        + usage["output_tokens"] * out
        + usage.get("cache_read", 0) * cr
        + usage.get("cache_write", 0) * cw
    ) / 1_000_000
