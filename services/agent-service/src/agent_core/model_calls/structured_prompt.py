from __future__ import annotations

"""Cache-friendly message construction for isolated JSON verifiers."""

import json
from typing import Any, Iterable

from langchain_core.messages import HumanMessage, SystemMessage


_VERIFIER_OUTPUT_CONTRACTS: dict[str, dict[str, Any]] = {
    "turn_goal_granularity_inventory_verifier": {
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "outcome_spans", "reason_code"],
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["exact", "clarify"],
                },
                "outcome_spans": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Every item is copied verbatim as one literal contiguous substring "
                        "of USER_TEXT_UNTRUSTED."
                    ),
                },
                "reason_code": {"type": "string"},
            },
        },
        "rules": [
            "outcome_spans MUST be a JSON array of JSON strings; never return objects, maps or nested arrays there.",
            "When verdict is exact, outcome_spans MUST contain at least one item.",
            "Copy every outcome_spans item verbatim from USER_TEXT_UNTRUSTED using exact contiguous characters.",
            "Do not paraphrase, translate, normalize, summarize, prepend or append characters to an outcome_span.",
        ],
    },
}


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

    Verifiers that cross a model-to-authority boundary may also register a
    domain-neutral JSON output contract here.  The contract constrains transport
    shape only; it never supplies business semantics, candidate goals, tools or
    capability availability.
    """
    resolved_role = str(role)
    policy = {
        "role": resolved_role,
        "instruction": str(instruction),
        "DECISION_RULES": [str(rule) for rule in decision_rules],
    }
    output_contract = _VERIFIER_OUTPUT_CONTRACTS.get(resolved_role)
    if output_contract is not None:
        policy["OUTPUT_CONTRACT"] = output_contract
    request = dict(payload)
    if format_repair:
        request["FORMAT_REPAIR"] = str(format_repair)
    return [
        SystemMessage(content=json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        HumanMessage(content=json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    ]


__all__ = ["structured_verifier_messages"]
