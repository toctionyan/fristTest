from __future__ import annotations

"""Typed discourse-set projections derived from customer-visible ResultRefs.

These projections help the semantic planner reason about phrases such as
"刚才两个" or "它们" without turning recency into an automatic target
selection policy.  They are read-only context metadata:

* only already validated customer-visible ResultRefs are included;
* every group is a contiguous prefix of discourse recency;
* Runtime still validates the exact handles proposed by a capability call;
* no group may be dispatched as a business target by itself.
"""

from typing import Any, Iterable


REFERENT_SET_VERSION = "visible-referent-sets@1"


def _clean_refs(refs: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = [dict(row) for row in list(refs or []) if isinstance(row, dict)]
    rows = [
        row
        for row in rows
        if str(row.get("result_ref") or "")
        and int(row.get("discourse_recency_rank") or 0) > 0
    ]
    rows.sort(
        key=lambda row: (
            int(row.get("discourse_recency_rank") or 0),
            -int(row.get("source_turn") or 0),
            str(row.get("result_ref") or ""),
        )
    )
    return rows


def _unique(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _group_row(*, kind: str, refs: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = [int(row.get("discourse_recency_rank") or 0) for row in refs]
    result_refs = _unique(row.get("result_ref") for row in refs)
    member_handles = _unique(
        member
        for row in refs
        for member in list(row.get("member_handles") or [])
    )
    member_labels = _unique(
        label
        for row in refs
        for label in list(row.get("member_labels") or [])
    )
    source_turns = sorted(
        {int(row.get("source_turn") or 0) for row in refs}, reverse=True
    )
    return {
        "referent_set_id": f"referent-set:{kind}:{'-'.join(map(str, ranks))}",
        "kind": kind,
        "result_refs": result_refs,
        "member_handles": member_handles,
        "member_labels": member_labels,
        "source_turns": source_turns,
        "discourse_recency_ranks": ranks,
        "contiguous_from_latest": ranks == list(range(1, len(ranks) + 1)),
        "result_count": len(result_refs),
        "member_count": len(member_handles),
        "singular_reference_is_ambiguous": len(result_refs) != 1
        or len(member_handles) != 1,
        "dispatchable": False,
        "selection_policy": "model_proposes_exact_refs_runtime_validates",
    }


def build_visible_referent_sets(
    refs: Iterable[dict[str, Any]] | None,
    *,
    max_recent_group_size: int = 4,
) -> dict[str, Any]:
    """Project bounded, deterministic discourse groups from visible refs.

    ``recent_contiguous_groups`` deliberately contains only prefixes starting
    at recency rank 1.  It therefore cannot be used to skip a more recent
    topic while pretending to refer to "the previous two".  The capability
    gate independently re-proves the selected handles before dispatch.
    """

    rows = _clean_refs(refs)
    latest_turn = max((int(row.get("source_turn") or 0) for row in rows), default=0)
    latest_rows = [
        row for row in rows if int(row.get("source_turn") or 0) == latest_turn
    ]
    bound = max(2, min(max(2, int(max_recent_group_size)), len(rows))) if rows else 0
    groups = [
        _group_row(kind=f"recent-{size}", refs=rows[:size])
        for size in range(2, bound + 1)
    ]
    return {
        "version": REFERENT_SET_VERSION,
        "authority": "read_only_discourse_projection",
        "runtime_auto_select_target": False,
        "latest_visible_turn_set": (
            _group_row(kind="latest-visible-turn", refs=latest_rows)
            if latest_rows
            else None
        ),
        "recent_contiguous_groups": groups,
        "available_result_ref_count": len(rows),
        "max_recent_group_size": int(max_recent_group_size),
    }


__all__ = ["REFERENT_SET_VERSION", "build_visible_referent_sets"]
