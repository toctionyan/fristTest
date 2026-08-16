from __future__ import annotations

import argparse
import json
from pathlib import Path

CHANGE_ID = "repair-stage4-2-dependency-obligation-evidence-pipeline"
BRIDGE = Path("services/agent-service/src/agent_core/goal_graph/dependency_alignment.py")
PLANNER = Path("services/agent-service/src/agent_core/lifecycle/goal_planning.py")

NEW_AUTHORITY_DETAILS = r'''def alignment_dependency_authority_details(
    ledger: dict[str, Any] | None,
    *,
    goals: list[dict[str, Any]],
) -> dict[str, Any]:
    current = deepcopy(ledger) if isinstance(ledger, dict) else make_dependency_proof_ledger()
    goal_ids = [
        str(goal.get("goal_id") or "")
        for goal in goals
        if isinstance(goal, dict) and str(goal.get("goal_id") or "")
    ]
    declared_edges = [
        {
            "dependent_goal_id": str(goal.get("goal_id") or ""),
            "requires_result_of_goal_id": str(prerequisite),
        }
        for goal in goals
        if isinstance(goal, dict)
        for prerequisite in list(goal.get("depends_on") or [])
        if str(goal.get("goal_id") or "") and str(prerequisite)
    ]
    diff = dependency_graph_diff(
        current,
        goal_ids=goal_ids,
        declared_edges=declared_edges,
    )

    # Keep a diagnostic projection of complete pair observations separate from
    # dependency authority.  Stage 4.2 deliberately leaves a pair GROUNDED when
    # target/counterfactual evidence is absent, but a complete candidate-blind
    # pair table is still safe evidence for declaration-repair feedback.  This
    # projection never makes ``dependency_authority_complete`` true and is not
    # consumed by the reducer.
    expected_pairs = {
        tuple(sorted((goal_ids[left], goal_ids[right])))
        for left in range(len(goal_ids))
        for right in range(left + 1, len(goal_ids))
    }
    observed_pairs: set[tuple[str, str]] = set()
    observed_edges: set[tuple[str, str]] = set()
    observed_decisions: list[dict[str, str]] = []
    for state in (current.get("states") or {}).values():
        if not isinstance(state, dict):
            continue
        goal_a = _text(state.get("goal_a_id"), limit=200)
        goal_b = _text(state.get("goal_b_id"), limit=200)
        relation = _text(state.get("relation"), limit=80).casefold()
        maturity = _text(state.get("maturity"), limit=40).upper()
        if not goal_a or not goal_b:
            continue
        pair = tuple(sorted((goal_a, goal_b)))
        if (
            pair not in expected_pairs
            or maturity not in {"GROUNDED", "AUTHORITATIVE"}
            or relation not in {"independent", "a_depends_on_b", "b_depends_on_a"}
        ):
            continue
        observed_pairs.add(pair)
        observed_decisions.append({
            "goal_a_id": goal_a,
            "goal_b_id": goal_b,
            "relation": relation,
        })
        if relation == "a_depends_on_b":
            observed_edges.add((goal_a, goal_b))
        elif relation == "b_depends_on_a":
            observed_edges.add((goal_b, goal_a))

    declared_edge_set = {
        (
            str(row.get("dependent_goal_id") or ""),
            str(row.get("requires_result_of_goal_id") or ""),
        )
        for row in declared_edges
    }
    observation_complete = observed_pairs == expected_pairs
    observation_graph_match = bool(
        observation_complete and observed_edges == declared_edge_set
    )
    return {
        "dependency_maturity_authority": "deterministic_dependency_proof_reducer",
        "dependency_authority_complete": bool(diff.get("repairable")),
        "dependency_authority_graph_match": diff.get("reason_code") == "DEPENDENCY_GRAPH_MATCH",
        "dependency_authority_edges": list(diff.get("authoritative_edges") or []),
        "dependency_authority_missing_edges": list(diff.get("missing_edges") or []),
        "dependency_authority_extra_edges": list(diff.get("extra_edges") or []),
        "dependency_authority_unresolved_pairs": list(diff.get("unresolved_pairs") or []),
        "dependency_authority_graph_proof_digest": diff.get("graph_proof_digest"),
        "dependency_authority_ledger_digest": current.get("ledger_digest"),
        "dependency_observation_complete": observation_complete,
        "dependency_observed_graph_match": observation_graph_match,
        "dependency_observed_edges": [
            {
                "dependent_goal_id": dependent,
                "requires_result_of_goal_id": prerequisite,
            }
            for dependent, prerequisite in sorted(observed_edges)
        ],
        "dependency_observed_pair_decisions": sorted(
            observed_decisions,
            key=lambda row: (
                str(row["goal_a_id"]),
                str(row["goal_b_id"]),
            ),
        ),
        "dependency_observation_authority_effect": False,
    }
'''

OBS_HELPER = r'''

def _dependency_observation_mismatch_ready(details: dict[str, Any]) -> bool:
    """Allow pair-complete observation evidence to drive redeclaration only.

    This is intentionally weaker than dependency authority.  It exists so a
    candidate-blind, pair-complete mismatch can tell the semantic writer which
    declared dependency relation to repair even while target/counterfactual
    obligations remain UNKNOWN.  It must never be used for execution or graph
    authority.
    """

    return bool(
        isinstance(details, dict)
        and details.get("dependency_maturity_authority")
        == "deterministic_dependency_proof_reducer"
        and details.get("dependency_observation_complete") is True
        and details.get("dependency_observed_graph_match") is False
        and details.get("dependency_observation_authority_effect") is False
    )
'''

OLD_GROUNDED = '''    grounded_dependency_mismatch = (
        verdict == "incomplete"
        and reason_code == "goal_alignment_dependency_graph_mismatch"
        and details.get("dependency_authority") == "independent_goal_alignment"
        and details.get("dependency_proof_complete") is True
        and details.get("dependency_graph_match") is False
        and details.get("dependency_maturity_authority") == "deterministic_dependency_proof_reducer"
        and details.get("dependency_authority_complete") is True
        and details.get("dependency_authority_graph_match") is False
    )
'''
NEW_GROUNDED = '''    grounded_dependency_mismatch = (
        verdict == "incomplete"
        and reason_code == "goal_alignment_dependency_graph_mismatch"
        and details.get("dependency_authority") == "independent_goal_alignment"
        and details.get("dependency_proof_complete") is True
        and details.get("dependency_graph_match") is False
        and (
            (
                details.get("dependency_maturity_authority")
                == "deterministic_dependency_proof_reducer"
                and details.get("dependency_authority_complete") is True
                and details.get("dependency_authority_graph_match") is False
            )
            or _dependency_observation_mismatch_ready(details)
        )
    )
'''

OLD_FEEDBACK_AUTH = '''    if not (
        details.get("dependency_authority") == "independent_goal_alignment"
        and details.get("dependency_proof_complete") is True
        and details.get("dependency_graph_match") is False
        and details.get("dependency_maturity_authority") == "deterministic_dependency_proof_reducer"
        and details.get("dependency_authority_complete") is True
        and details.get("dependency_authority_graph_match") is False
    ):
        return {}
'''
NEW_FEEDBACK_AUTH = '''    if not (
        details.get("dependency_authority") == "independent_goal_alignment"
        and details.get("dependency_proof_complete") is True
        and details.get("dependency_graph_match") is False
        and (
            (
                details.get("dependency_maturity_authority")
                == "deterministic_dependency_proof_reducer"
                and details.get("dependency_authority_complete") is True
                and details.get("dependency_authority_graph_match") is False
            )
            or _dependency_observation_mismatch_ready(details)
        )
    ):
        return {}
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    active = json.loads((root / "governance/active-change.json").read_text(encoding="utf-8"))
    if active.get("change_id") != CHANGE_ID:
        raise SystemExit("wrong active change")
    if active.get("status") != "implementing" or active.get("writer_role") != "product-implementer":
        raise SystemExit("Stage 4.2 writer permit inactive")
    for path in (BRIDGE, PLANNER):
        if path.as_posix() not in list(active.get("allowed_paths") or []):
            raise SystemExit(f"outside active ChangePermit: {path}")

    bridge_path = root / BRIDGE
    bridge = bridge_path.read_text(encoding="utf-8")
    start = bridge.index("def alignment_dependency_authority_details(\n")
    end = bridge.index("\n\ndef dependency_authority_closed_and_matching", start)
    bridge = bridge[:start] + NEW_AUTHORITY_DETAILS.rstrip() + bridge[end:]
    bridge_path.write_text(bridge, encoding="utf-8")

    planner_path = root / PLANNER
    planner = planner_path.read_text(encoding="utf-8")
    helper_marker = "\n\ndef _as_alignment_verdict(\n"
    if "def _dependency_observation_mismatch_ready" not in planner:
        if planner.count(helper_marker) != 1:
            raise SystemExit("planner helper insertion marker mismatch")
        planner = planner.replace(helper_marker, OBS_HELPER + helper_marker, 1)
    planner = replace_once(planner, OLD_GROUNDED, NEW_GROUNDED, "grounded dependency mismatch")
    planner = replace_once(planner, OLD_FEEDBACK_AUTH, NEW_FEEDBACK_AUTH, "repair feedback authority guard")
    planner_path.write_text(planner, encoding="utf-8")
    print(BRIDGE.as_posix())
    print(PLANNER.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
