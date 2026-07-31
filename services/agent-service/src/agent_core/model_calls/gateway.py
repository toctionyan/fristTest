from __future__ import annotations

"""One bounded, observable gateway for every model invocation.

LoopBudget limits agent-loop iterations.  ModelCallBudget is intentionally
separate: verification, RAG rewriting and answer-release checks can invoke a
model without increasing the loop step.  All calls share one trace and hard
total, while planner, verifier and support lanes have independent caps so a
valid long workflow cannot consume the safety verifier's reserved capacity.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import os
import re
from time import perf_counter
from typing import Any, Iterator


ENVIRONMENTAL_MODEL_FAILURE_CATEGORIES = frozenset({
    "http_401",
    "http_402",
    "http_403",
    "http_429",
    "timeout",
    "connection",
})


def classify_model_failure(error: Any, *, error_type: str | None = None) -> str:
    """Classify provider failures without copying their message into evidence."""
    type_name = str(error_type or getattr(error, "__class__", type(error)).__name__ or "")
    status_code = getattr(error, "status_code", None)
    try:
        status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status = None
    text = f"{type_name} {error!s}".lower()
    if status is None:
        match = re.search(r"(?:error code|status(?: code)?|http)[^0-9]{0,6}([45]\d\d)\b", text)
        if match is None:
            match = re.search(r"\b([45]\d\d)\b", text)
        status = int(match.group(1)) if match else None
    if status is not None:
        return f"http_{status}"
    if "authentication" in text or "unauthorized" in text or "invalid api key" in text:
        return "http_401"
    if "permissiondenied" in text or "forbidden" in text:
        return "http_403"
    if "insufficient balance" in text or "insufficient quota" in text:
        return "http_402"
    if "ratelimit" in text or "rate limit" in text:
        return "http_429"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "connection" in text or "connecterror" in text:
        return "connection"
    for marker in ("protocol", "validation", "budget"):
        if marker in text:
            return marker
    return "unclassified"


def is_environmental_model_failure(error: Any, *, error_type: str | None = None) -> bool:
    return classify_model_failure(error, error_type=error_type) in ENVIRONMENTAL_MODEL_FAILURE_CATEGORIES


def is_environmental_model_failure_category(category: str) -> bool:
    return str(category or "") in ENVIRONMENTAL_MODEL_FAILURE_CATEGORIES


class ModelCallBudgetExceeded(RuntimeError):
    """Raised before an additional model call can consume unbounded budget."""


@dataclass
class ModelCallLedger:
    max_calls: int
    scope: str = "request"
    max_calls_by_lane: dict[str, int] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def used_calls(self) -> int:
        return len(self.records)

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.used_calls)

    @property
    def used_calls_by_lane(self) -> dict[str, int]:
        counts = {name: 0 for name in self.max_calls_by_lane}
        for record in self.records:
            lane = str(record.get("lane") or "support")
            counts[lane] = counts.get(lane, 0) + 1
        return counts

    def summary(self) -> dict[str, Any]:
        usage_keys = (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        )
        usage = {
            key: sum(int(record.get(key) or 0) for record in self.records)
            for key in usage_keys
        }
        cache_total = usage["prompt_cache_hit_tokens"] + usage["prompt_cache_miss_tokens"]
        usage["prompt_cache_hit_rate"] = (
            round(usage["prompt_cache_hit_tokens"] / cache_total, 4)
            if cache_total else 0.0
        )
        usage["calls_with_usage"] = sum(
            1 for record in self.records if "total_tokens" in record
        )
        return {
            "scope": self.scope,
            "max_calls": self.max_calls,
            "used_calls": self.used_calls,
            "remaining_calls": self.remaining_calls,
            "max_calls_by_lane": dict(self.max_calls_by_lane),
            "used_calls_by_lane": self.used_calls_by_lane,
            "remaining_calls_by_lane": {
                lane: max(0, maximum - self.used_calls_by_lane.get(lane, 0))
                for lane, maximum in self.max_calls_by_lane.items()
            },
            "token_usage": usage,
        }


_LEDGER: ContextVar[ModelCallLedger | None] = ContextVar("model_call_ledger", default=None)


def _configured_max_calls(value: int | None = None) -> int:
    if value is not None:
        return max(1, int(value))
    raw = os.getenv("MODEL_CALL_MAX_PER_TURN", "18")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 18


def _configured_lane_calls() -> dict[str, int]:
    defaults = {"planner": 8, "verifier": 8, "support": 2}
    env_names = {
        "planner": "MODEL_CALL_MAX_PLANNER_PER_TURN",
        "verifier": "MODEL_CALL_MAX_VERIFIER_PER_TURN",
        "support": "MODEL_CALL_MAX_SUPPORT_PER_TURN",
    }
    configured: dict[str, int] = {}
    for lane, default in defaults.items():
        raw = (os.getenv(env_names[lane]) or str(default)).strip()
        try:
            configured[lane] = max(0, int(raw))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{env_names[lane]} must be a non-negative integer") from exc
    return configured


def _lane_for_purpose(purpose: str) -> str:
    name = str(purpose or "")
    if name == "agent_loop":
        return "planner"
    if name in {
        "semantic_capability_verifier",
        "turn_goal_alignment_verifier",
        "answer_release_alignment",
    }:
        return "verifier"
    return "support"


@contextmanager
def model_call_scope(*, max_calls: int | None = None, scope: str = "request") -> Iterator[ModelCallLedger]:
    """Share one traced budget with reserved lanes across a complete request."""
    existing = _LEDGER.get()
    if existing is not None:
        # Nested components must consume the parent request budget; creating a
        # fresh local budget would hide expensive verifier/RAG calls.
        yield existing
        return
    maximum = _configured_max_calls(max_calls)
    lanes = {} if max_calls is not None else _configured_lane_calls()
    if lanes and sum(lanes.values()) != maximum:
        raise RuntimeError(
            "MODEL_CALL_MAX_PER_TURN must equal the sum of planner, verifier and support lane budgets"
        )
    ledger = ModelCallLedger(max_calls=maximum, scope=scope, max_calls_by_lane=lanes)
    token = _LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _LEDGER.reset(token)


def current_model_call_ledger() -> ModelCallLedger | None:
    return _LEDGER.get()


def _model_label(model: Any) -> str:
    for attr in ("model_name", "model", "deployment_name", "name"):
        value = getattr(model, attr, None)
        if value:
            return str(value)
    return model.__class__.__name__


def _usage_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _model_usage(response: Any) -> dict[str, Any]:
    """Extract provider usage without retaining prompt or response content."""
    metadata = getattr(response, "response_metadata", None)
    raw_usage = metadata.get("token_usage") if isinstance(metadata, dict) else None
    if not isinstance(raw_usage, dict) and isinstance(response, dict):
        raw_usage = response.get("usage")
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}

    normalized = getattr(response, "usage_metadata", None)
    normalized = normalized if isinstance(normalized, dict) else {}
    prompt_tokens = _usage_int(raw_usage.get("prompt_tokens"))
    completion_tokens = _usage_int(raw_usage.get("completion_tokens"))
    total_tokens = _usage_int(raw_usage.get("total_tokens"))
    if prompt_tokens is None:
        prompt_tokens = _usage_int(normalized.get("input_tokens"))
    if completion_tokens is None:
        completion_tokens = _usage_int(normalized.get("output_tokens"))
    if total_tokens is None:
        total_tokens = _usage_int(normalized.get("total_tokens"))

    prompt_details = raw_usage.get("prompt_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    cache_hit = _usage_int(raw_usage.get("prompt_cache_hit_tokens"))
    if cache_hit is None:
        cache_hit = _usage_int(prompt_details.get("cached_tokens"))
    cache_miss = _usage_int(raw_usage.get("prompt_cache_miss_tokens"))
    if cache_miss is None and cache_hit is not None and prompt_tokens is not None:
        cache_miss = max(0, prompt_tokens - cache_hit)

    usage: dict[str, Any] = {}
    for key, value in (
        ("prompt_tokens", prompt_tokens),
        ("completion_tokens", completion_tokens),
        ("total_tokens", total_tokens),
        ("prompt_cache_hit_tokens", cache_hit),
        ("prompt_cache_miss_tokens", cache_miss),
    ):
        if value is not None:
            usage[key] = value
    if cache_hit is not None and cache_miss is not None:
        cache_total = cache_hit + cache_miss
        usage["prompt_cache_hit_rate"] = round(cache_hit / cache_total, 4) if cache_total else 0.0
    return usage


def _provider_response_metadata(response: Any) -> dict[str, Any]:
    """Extract safe provider identity/termination metadata for certification traces."""
    metadata = getattr(response, "response_metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    provider_model = ""
    for value in (
        metadata.get("model_name"),
        metadata.get("model"),
        metadata.get("model_id"),
        getattr(response, "model_name", None),
        getattr(response, "model", None),
    ):
        if str(value or "").strip():
            provider_model = str(value).strip()
            break
    finish_reason = str(
        metadata.get("finish_reason")
        or metadata.get("stop_reason")
        or ""
    ).strip()
    result: dict[str, Any] = {}
    if provider_model:
        result["provider_model"] = provider_model
    if finish_reason:
        result["finish_reason"] = finish_reason
    return result


def invoke_model(
    *,
    purpose: str,
    model: Any,
    payload: Any,
    state: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Invoke a model under the active request budget and emit safe trace data.

    Input content is intentionally not copied into the trace: prompts and user
    text are already captured by the existing debug/audit projection where
    permitted.  This gateway records only execution metadata needed for cost,
    latency and bounded-execution audits.
    """
    ledger = _LEDGER.get()
    local_scope = False
    if ledger is None:
        state_budget = state.get("model_call_budget") if isinstance(state, dict) else None
        configured = state_budget.get("max_calls") if isinstance(state_budget, dict) else None
        ledger = ModelCallLedger(max_calls=_configured_max_calls(configured), scope="component")
        local_scope = True
    if ledger.used_calls >= ledger.max_calls:
        raise ModelCallBudgetExceeded(
            f"model call budget exhausted ({ledger.used_calls}/{ledger.max_calls}) for {purpose}"
        )
    lane = _lane_for_purpose(purpose)
    lane_maximum = ledger.max_calls_by_lane.get(lane)
    lane_used = ledger.used_calls_by_lane.get(lane, 0)
    if lane_maximum is not None and lane_used >= lane_maximum:
        raise ModelCallBudgetExceeded(
            f"model call {lane} lane exhausted ({lane_used}/{lane_maximum}) for {purpose}"
        )

    started = perf_counter()
    record: dict[str, Any] = {
        "purpose": str(purpose),
        "model": _model_label(model),
        "sequence": ledger.used_calls + 1,
        "scope": ledger.scope,
        "lane": lane,
    }
    try:
        response = model.invoke(payload)
        record.update({
            "status": "ok",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            **_provider_response_metadata(response),
            **_model_usage(response),
        })
        return response, dict(record)
    except Exception as exc:
        record.update({
            "status": "error",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "error_type": exc.__class__.__name__,
        })
        raise
    finally:
        ledger.records.append(dict(record))
        # When called outside an explicit request scope, preserve no mutable
        # global state; callers still receive this record for debug output.
        if local_scope:
            pass


__all__ = [
    "ENVIRONMENTAL_MODEL_FAILURE_CATEGORIES",
    "classify_model_failure",
    "is_environmental_model_failure",
    "is_environmental_model_failure_category",
    "ModelCallBudgetExceeded",
    "ModelCallLedger",
    "current_model_call_ledger",
    "invoke_model",
    "model_call_scope",
]
