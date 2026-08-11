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
)


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_goal_planning(root: Path) -> None:
    path = root / SOURCE_PATHS[0]
    old = '''                        verifier_repair = (
                            "Adversarially re-audit the complete current-turn dependency graph from USER_TEXT only. Start every unordered "
                            "Goal pair from independent after re-reading the whole USER_TEXT, and retain a positive edge only when a literal basis_span "
                            "inside the dependent Goal proves that the user-visible later outcome itself consumes the earlier current-turn Goal result "
                            "as a result_reference, result_condition or result_value_input. If the later outcome merely omits a repeated target while "
                            "an earlier literal phrase in the same USER_TEXT already names the reusable business object or scope, treat that as same-turn "
                            "zero-anaphora ellipsis and keep the outcomes independent. A lookup, stable-ID/artifact resolution, Draft prerequisite or "
                            "form/transaction input needed only to execute against that already literal target is support dataflow, not result_value_input. "
                            "Sequencing, shared topic/scope and repeated business object are not result dependencies. A positive edge requires literal "
                            "dependent wording that consumes the earlier outcome's result, not merely its business target. "
                            "Do not see or reconstruct Planner depends_on from tool needs. Return one dependency_decisions row for every unordered Goal pair together with the "
                            "normal requested-effect and scope audit fields. A true explicit result reference/condition/value dependency must still be retained. When "
                            "REQUESTED_EFFECT_COLLISION_RISK is supplied, also adversarially verify that each sibling's identical structured "
                            "requested_effect still denotes that sibling's own literal user-visible business effect; if one sibling has been "
                            "collapsed into a different lookup/action/object/effect, return incomplete with the smallest literal mismatch span."
                        )
'''
    new = '''                        verifier_repair = (
                            "Adversarially re-audit the complete current-turn dependency graph from USER_TEXT only. Start every unordered "
                            "Goal pair from independent after re-reading the whole USER_TEXT. For every proposed positive edge, perform this "
                            "counterfactual before retaining it: imagine the earlier Goal has produced no result payload at all—no returned fields, "
                            "status/value, selected member, or answer text—while the complete literal USER_TEXT remains available. If the later "
                            "user-visible business outcome is still fully specified by literal wording, a shared same-turn business target/scope, or "
                            "zero-anaphora ellipsis/omission of an already literal target, the pair is independent. A lookup, stable-ID/artifact resolution, "
                            "eligibility/preflight read, Draft prerequisite, form input, transaction setup, or other execution support needed to act "
                            "against that already specified target is support dataflow, not result_condition/result_value_input. Retain a positive edge "
                            "only when removing the earlier result payload makes the later outcome's target, condition, or value input semantically "
                            "unavailable because literal wording inside the dependent Goal actually consumes that earlier result as result_reference, "
                            "result_condition, or result_value_input. Explicit phrases that use/compare/act on that result, or a condition/value explicitly "
                            "derived from it, remain true dependencies. Sequencing words, shared topic/scope, repeated business object, and an omitted "
                            "repeated target do not. Do not see or reconstruct Planner depends_on from tool order, capability needs, IDs, Draft mechanics, "
                            "or business-state facts. Return one dependency_decisions row for every unordered Goal pair together with the normal "
                            "requested-effect and scope audit fields. When REQUESTED_EFFECT_COLLISION_RISK is supplied, also adversarially verify that each "
                            "sibling's identical structured requested_effect still denotes that sibling's own literal user-visible business effect; if "
                            "one sibling has been collapsed into a different lookup/action/object/effect, return incomplete with the smallest literal "
                            "mismatch span."
                        )
'''
    replace_once(path, old, new, "positive dependency adversarial counterfactual")


def write_tests(root: Path) -> None:
    path = root / TEST_PATH
    if path.exists():
        raise SystemExit(f"test path already exists: {TEST_PATH}")
    template = Path(__file__).with_name("tmp_wp08_attempt7_dependency_counterfactual_test_template.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")


def patch(root: Path) -> None:
    patch_goal_planning(root)
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
