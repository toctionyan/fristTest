#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

TEST_PATH = "skill-system/tests/test_wp08_attempt7_dependency_counterfactual_repair.py"
SOURCE_PATHS = (
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
    "services/agent-service/scripts/verify_preprod_conversation_smoke.py",
)


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_goal_planning(root: Path) -> None:
    path = root / SOURCE_PATHS[0]
    replace_once(
        path,
        "        for attempt in range(3):\n",
        "        for attempt in range(4):\n"
        "            if (\n"
        "                attempt >= 3\n"
        '                and verifier_repair_kind != "candidate_blind_dependency_positive_edge_counterfactual"\n'
        "            ):\n"
        "                break\n",
        "alignment verifier bounded fourth slot",
    )
    old_anchor = '''                    if effect_collision_risk["risk"]:\n                        prompt["REQUESTED_EFFECT_COLLISION_RISK"] = effect_collision_risk\n                    continue\n            normalized_semantic_reason = (\n'''
    new_anchor = '''                    if effect_collision_risk["risk"]:\n                        prompt["REQUESTED_EFFECT_COLLISION_RISK"] = effect_collision_risk\n                    continue\n            if (\n                blind_dependency_audit\n                and verifier_repair_kind == "candidate_blind_dependency_positive_edge_adjudication"\n                and verdict.exact\n                and isinstance(verdict.details, dict)\n                and verdict.details.get("dependency_proof_complete") is True\n                and verdict.details.get("dependency_graph_match") is True\n                and bool(list(verdict.details.get("dependency_edges") or []))\n                and any(\n                    str(edge.get("basis_kind") or "").strip() != "result_reference"\n                    for edge in list(verdict.details.get("dependency_edges") or [])\n                    if isinstance(edge, dict)\n                )\n                and attempt < 3\n            ):\n                # Explicit result_reference edges have already survived the strict\n                # literal-basis validator plus the candidate-blind adversarial call.\n                # The extra slot is reserved for result_condition/result_value_input\n                # claims, where execution-support dataflow is most easily confused\n                # with semantic result consumption. Runtime remains proof validator.\n                verifier_repair_kind = "candidate_blind_dependency_positive_edge_counterfactual"\n                verifier_repair = (\n                    "Perform a final counterfactual result-dependency audit from USER_TEXT only. For each positive non-reference edge, imagine the "\n                    "earlier Goal has produced no result payload at all: no returned fields, no status/value, no selected member, and no "\n                    "answer text. Keep the complete literal USER_TEXT available. Ask whether the later user-visible business outcome is "\n                    "still fully specified by literal wording or same-turn zero-anaphora target/scope already present in USER_TEXT. If yes, "\n                    "the pair is independent even when execution would still need a stable-ID/artifact lookup, eligibility/preflight read, "\n                    "Draft prerequisite, form value, transaction setup, or another implementation support step. Those mechanics do not consume "\n                    "the earlier Goal result. Retain a positive edge only when removing the earlier result payload makes the later outcome's "\n                    "condition or value input semantically unavailable because the dependent wording itself consumes that result as "\n                    "result_condition or result_value_input. True result_reference edges were already validated earlier and must still be returned "\n                    "unchanged in the complete dependency_decisions array. Sequencing words, shared topic, shared object, and an omitted repeated "\n                    "target do not create a dependency. Do not infer tool order, capability needs, IDs, Draft mechanics, or business-state facts. "\n                    "Return one dependency_decisions row for every unordered Goal pair using the strict candidate-blind contract. Do not see, "\n                    "reconstruct, or preserve Planner depends_on merely because a previous verifier retained it."\n                )\n                prompt = {\n                    "USER_TEXT_UNTRUSTED": user_text,\n                    "DECLARED_GOALS": _dependency_adjudication_goal_projection(goals),\n                    "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n                    "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                }\n                continue\n            normalized_semantic_reason = (\n'''
    replace_once(path, old_anchor, new_anchor, "positive edge counterfactual adjudication")


def patch_semantic_smoke(root: Path) -> None:
    path = root / SOURCE_PATHS[1]
    old_comment = '''        # Each accepted declaration is checked by both independent model validators:\n        # alignment owns the complete grounded dependency graph, while candidate-blind\n        # granularity owns only outcome decomposition. A rejected declaration may be repaired\n        # once through the same protected path. Each verifier remains capped at two calls;\n        # granularity's second call is decomposition-only self-audit (never dependency\n        # re-judgment), so the existing worst-case envelope remains\n        # 12 * 2 * (1 declaration + 2 alignment + 2 granularity) = 120.\n        with model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes") as calls:\n'''
    new_comment = '''        # Each accepted declaration is checked by both independent model validators:\n        # alignment owns the complete grounded dependency graph, while candidate-blind\n        # granularity owns only outcome decomposition. A rejected declaration may be repaired\n        # once through the same protected path. Alignment normally closes earlier, but a\n        # positive non-reference dependency edge may spend a fourth and final candidate-blind\n        # counterfactual slot; granularity remains capped at two calls. The fail-closed worst-case\n        # envelope is therefore 12 * 2 * (1 declaration + 4 alignment + 2 granularity) = 168.\n        with model_call_scope(max_calls=168, scope="preprod_semantic_goal_prototypes") as calls:\n'''
    replace_once(path, old_comment, new_comment, "semantic certification model-call envelope")


def write_tests(root: Path) -> None:
    path = root / TEST_PATH
    if path.exists():
        raise SystemExit(f"test path already exists: {TEST_PATH}")
    template = Path(__file__).with_name("tmp_wp08_attempt7_dependency_counterfactual_test_template.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")


def patch(root: Path) -> None:
    patch_goal_planning(root)
    patch_semantic_smoke(root)
    write_tests(root)


def baseline(root: Path, product_sha: str) -> None:
    path = root / "skill-system/registry/product-source-baseline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, dict):
        raise SystemExit("protected baseline files map is missing")
    updated: list[str] = []
    for rel in SOURCE_PATHS:
        if rel not in files:
            raise SystemExit(f"protected baseline does not own {rel}")
        files[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        updated.append(rel)
    payload["file_count"] = len(files)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["generated_from"] = "git:" + product_sha
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"updated": updated, "file_count": len(files)}, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    patch_parser = sub.add_parser("patch")
    patch_parser.add_argument("--workspace", required=True)
    baseline_parser = sub.add_parser("baseline")
    baseline_parser.add_argument("--workspace", required=True)
    baseline_parser.add_argument("--product-sha", required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.command == "patch":
        patch(root)
    else:
        baseline(root, str(args.product_sha))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
