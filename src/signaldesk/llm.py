"""Claude calls with a database cache and hard budgets.

- Cached: the key is a hash of the full request, so a rerun of the same question with the same passages
  never pays twice, and evaluation numbers reproduce.
- Budgeted: at most SIGNALDESK_MAX_CALLS_PER_DAY uncached calls per day (default 300). Hitting the cap
  raises BudgetExceeded; it never silently degrades.
- Structured: the response is JSON constrained by a schema (output_config.format).
- Refusal-aware: server-side fallback is on, and a final refusal is surfaced, not parsed as an answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

MODEL = os.environ.get("SIGNALDESK_MODEL", "claude-opus-5-5")
MAX_CALLS_PER_DAY = int(os.environ.get("SIGNALDESK_MAX_CALLS_PER_DAY", "300"))


class BudgetExceeded(RuntimeError):
    pass


class Refused(RuntimeError):
    pass


@dataclass
class LLMResult:
    data: dict
    cached: bool
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None


# A transport takes the request kwargs and returns (json_text, stop_reason, in_tokens, out_tokens).
Transport = Callable[[dict], tuple[str, str, int | None, int | None]]


def claude_transport(request: dict) -> tuple[str, str, int | None, int | None]:
    import anthropic

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY, or an `ant auth login` profile
    resp = client.beta.messages.create(
        **request,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # a declined request is retried on Anthropic's recommended model
    )
    if resp.stop_reason == "refusal":
        cat = resp.stop_details.category if resp.stop_details else None
        raise Refused(f"model declined (category={cat})")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return text, resp.stop_reason, resp.usage.input_tokens, resp.usage.output_tokens


def request_key(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


def call_json(
    conn: psycopg.Connection,
    *,
    purpose: str,
    system: str,
    user: str,
    schema: dict,
    effort: str = "medium",
    max_tokens: int = 16000,
    transport: Transport | None = None,
) -> LLMResult:
    request = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    }
    key = request_key(request)
    row = conn.execute(
        "SELECT response, input_tokens, output_tokens, latency_ms FROM llm_calls WHERE key = %s", (key,)
    ).fetchone()
    if row:
        return LLMResult(row[0], True, row[1], row[2], row[3])

    used = conn.execute(
        "SELECT count(*) FROM llm_calls WHERE created_at > now() - interval '1 day'"
    ).fetchone()[0]
    if used >= MAX_CALLS_PER_DAY:
        raise BudgetExceeded(
            f"{used} model calls in the last 24h (cap {MAX_CALLS_PER_DAY}); "
            "raise SIGNALDESK_MAX_CALLS_PER_DAY to continue"
        )

    t0 = time.perf_counter()
    stop, tin, tout = "error", None, None
    try:
        text, stop, tin, tout = (transport or claude_transport)(request)
        if stop == "max_tokens":
            raise RuntimeError("response truncated at max_tokens")
        data = json.loads(text)
    except Exception as e:
        # Failed calls still cost money, so they count toward the budget. The unique suffix keeps them
        # out of the cache: a retry calls the model again instead of replaying the failure.
        _record(conn, f"{key}:failed:{time.time_ns()}", purpose, request, {"error": repr(e)}, stop, tin, tout,
                int((time.perf_counter() - t0) * 1000))
        raise
    latency = int((time.perf_counter() - t0) * 1000)
    _record(conn, key, purpose, request, data, stop, tin, tout, latency)
    return LLMResult(data, False, tin, tout, latency)


def _record(conn, key, purpose, request, response, stop, tin, tout, latency) -> None:
    conn.execute(
        """INSERT INTO llm_calls (key, model, purpose, request, response, stop_reason, input_tokens,
               output_tokens, latency_ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (key) DO NOTHING""",
        (key, MODEL, purpose, json.dumps(request), json.dumps(response), stop, tin, tout, latency),
    )
