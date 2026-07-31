
from __future__ import annotations

from typing import Any


def evaluate_progress(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    previous_failed = {str(v) for v in previous.get("failed_gate_ids") or []}
    previous_skipped = {str(v) for v in previous.get("upstream_skipped_gate_ids") or []}
    current_failed = {str(v) for v in current.get("failed_gate_ids") or []}
    resolved = previous_failed - current_failed
    new = current_failed - previous_failed
    newly_exposed = new & previous_skipped
    unexpected_new = new - previous_skipped
    still_failed = previous_failed & current_failed

    if not previous_failed and previous.get("last_failure_count") is None:
        improved = True
        reason = "comparison-baseline-established"
    elif resolved and not unexpected_new:
        improved = True
        reason = "resolved-root-or-exposed-downstream"
    elif len(current_failed) < len(previous_failed) and not unexpected_new:
        improved = True
        reason = "fewer-known-root-failures"
    else:
        improved = False
        reason = "no-measurable-root-progress"

    return {
        "improved": improved,
        "reason": reason,
        "resolved_gate_ids": sorted(resolved),
        "new_gate_ids": sorted(new),
        "newly_exposed_downstream_gate_ids": sorted(newly_exposed),
        "unexpected_new_gate_ids": sorted(unexpected_new),
        "still_failed_gate_ids": sorted(still_failed),
    }
