from __future__ import annotations

"""Cache-friendly message construction for isolated JSON verifiers."""

import json
from typing import Any, Iterable

from langchain_core.messages import HumanMessage, SystemMessage


def structured_verifier_messages(
    *,
    role: str,
    instruction: str,
    decision_rules: Iterable[str] = (),
    payload: dict[str, Any],
    format_repair: str | None = None,
) -> list[Any]:
    """Separate immutable verifier policy from per-request evidence.

    DeepSeek caches identical prefixes automatically.  A single JSON object with
    sorted dynamic keys put customer evidence before the instruction and made
    every verifier call a cache miss.  Two native messages preserve the same
    trust boundary while making the complete policy an identical first prefix.
    """
    policy = {
        "role": str(role),
        "instruction": str(instruction),
        "DECISION_RULES": [str(rule) for rule in decision_rules],
    }
    request = dict(payload)
    if format_repair:
        request["FORMAT_REPAIR"] = str(format_repair)
    return [
        SystemMessage(content=json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        HumanMessage(content=json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    ]


__all__ = ["structured_verifier_messages"]
