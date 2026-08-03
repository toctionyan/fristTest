from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ALLOWED_PATHS = (
    "services/agent-service/src/agent_core/runtime/capability_gate.py",
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    "services/agent-service/tests/support/conversation_case_fixtures.py",
    "services/agent-service/tests/runtime/test_goal_coverage_runtime.py",
    "services/agent-service/tests/runtime/test_b17h_protected_environment_preflight.py",
    "services/agent-service/tests/runtime/test_b17i_production_execution_handoff.py",
)


def replace_once(source: str, old: str, new: str, *, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one preimage, found {count}")
    return source.replace(old, new, 1)


def update(root: Path, relative: str, transform) -> tuple[str, str]:
    path = root / relative
    before = path.read_text(encoding="utf-8")
    after = transform(before)
    if after == before:
        raise SystemExit(f"{relative}: transformation produced no change")
    path.write_text(after, encoding="utf-8")
    return hashlib.sha256(before.encode()).hexdigest(), hashlib.sha256(after.encode()).hexdigest()


def repair_capability_gate(source: str) -> str:
    latest_handles = '''    latest_handles = {
        str(ref.get("result_ref") or "")
        for ref in visible_refs
        if bool(ref.get("is_latest_visible_turn")) and str(ref.get("result_ref") or "")
    }
'''
    latest_with_scope = latest_handles + '''    latest_release_scope_keys = {
        (
            f"effect:{str(ref.get('source_effect_id') or '')}"
            if str(ref.get("source_effect_id") or "")
            else f"result:{str(ref.get('result_ref') or '')}"
        )
        for ref in visible_refs
        if bool(ref.get("is_latest_visible_turn")) and str(ref.get("result_ref") or "")
    }
'''
    source = replace_once(
        source,
        latest_handles,
        latest_with_scope,
        label="capability_gate latest release scopes",
    )
    source = replace_once(
        source,
        '''        len(latest_handles) > 1
        and mode in {"collection", "artifact"}
''',
        '''        len(latest_release_scope_keys) > 1
        and mode in {"collection", "artifact"}
''',
        label="capability_gate ambiguity predicate",
    )
    source = replace_once(
        source,
        '''            "latest_visible_result_count": len(latest_handles),
            "latest_visible_scope_ambiguous": len(latest_handles) > 1,
''',
        '''            "latest_visible_result_count": len(latest_handles),
            "latest_visible_scope_count": len(latest_release_scope_keys),
            "latest_visible_scope_ambiguous": len(latest_release_scope_keys) > 1,
''',
        label="capability_gate ambiguity evidence",
    )
    return source


def repair_dialogue_runtime(source: str) -> str:
    source = replace_once(
        source,
        '''    pending_goal_ids = {
        str(goal.get("goal_id") or "") for goal in pending_rows if str(goal.get("goal_id") or "")
    }
    completion_tools = {
''',
        '''    pending_goal_ids = {
        str(goal.get("goal_id") or "") for goal in pending_rows if str(goal.get("goal_id") or "")
    }
    clarification_goal_ids = {
        str(goal.get("goal_id") or "")
        for goal in pending_rows
        if str(goal.get("goal_type") or "") == "clarification" and str(goal.get("goal_id") or "")
    }
    completion_tools = {
''',
        label="dialogue clarification goal classification",
    )
    source = replace_once(
        source,
        '''        if isinstance(row, dict)
        and str(row.get("goal_id") or "") in pending_goal_ids
        and str(row.get("status") or "") in {"absent_proven", "completion_capability_absent"}
''',
        '''        if isinstance(row, dict)
        and str(row.get("goal_id") or "") in pending_goal_ids
        and str(row.get("goal_id") or "") not in clarification_goal_ids
        and str(row.get("status") or "") in {"absent_proven", "completion_capability_absent"}
''',
        label="dialogue clarification unsupported exclusion",
    )
    return source


def repair_fixture(source: str) -> str:
    return replace_once(
        source,
        '''    return mark_visible_result_refs(
        [*artifacts, *views, result],
        state=state,
        evidence_handles=[*handles, *(item["handle"] for item in views), result["handle"]],
    )
''',
        '''    visible_handles = [
        *handles,
        *[str(item["handle"]) for item in views],
        str(result["handle"]),
    ]
    return mark_visible_result_refs(
        [*artifacts, *views, result],
        state=state,
        evidence_handles=visible_handles,
        source_effect_by_handle={
            handle: "turn-plan:conversation-regression-fixture:effect:visible-bootstrap"
            for handle in visible_handles
        },
    )
''',
        label="conversation fixture visible release batch",
    )


def repair_goal_tests(source: str) -> str:
    replacements = (
        (
            '''                "description": "确认刚才开票的订单",
                "evidence_span": text,
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "expected_tools": [],
''',
            '''                "description": "确认刚才开票的订单",
                "evidence_span": text,
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "requested_effect": {
                    "domain": "conversation_history",
                    "operation": "recall_released_answer",
                    "object_type": "released_answer",
                    "raw_description": "确认刚才开票的订单",
                },
                "expected_tools": [],
''',
            "history recall requested effect",
        ),
        (
            '''                "description": "澄清具体退款订单",
                "evidence_span": text,
                "goal_type": "clarification",
                "required": True,
                "depends_on": [],
                "expected_tools": [],
''',
            '''                "description": "澄清具体退款订单",
                "evidence_span": text,
                "goal_type": "clarification",
                "required": True,
                "depends_on": [],
                "requested_effect": {
                    "domain": "refund",
                    "operation": "clarify_target",
                    "object_type": "order",
                    "raw_description": "澄清具体退款订单",
                },
                "expected_tools": [],
''',
            "clarification requested effect",
        ),
        (
            '''                "description": "查询当前集合中最贵的对象",
                "evidence_span": text,
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "expected_tools": [],
''',
            '''                "description": "查询当前集合中最贵的对象",
                "evidence_span": text,
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "requested_effect": {
                    "domain": "order",
                    "operation": "list",
                    "object_type": "order",
                    "raw_description": "查询当前集合中最贵的对象",
                },
                "expected_tools": [],
''',
            "pending query requested effect",
        ),
    )
    for old, new, label in replacements:
        source = replace_once(source, old, new, label=label)
    return source


def repair_b17h_test(source: str) -> str:
    source = replace_once(
        source,
        "import json\nfrom pathlib import Path\n",
        "import json\nimport os\nfrom pathlib import Path\n",
        label="b17h os import",
    )
    return replace_once(
        source,
        '''def test_local_cli_writes_sanitized_environment_block(tmp_path: Path) -> None:
    output = tmp_path / "preflight.json"
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--workspace-root", str(ROOT), "--output", str(output)],
        text=True,
''',
        '''def test_local_cli_writes_sanitized_environment_block(tmp_path: Path) -> None:
    output = tmp_path / "preflight.json"
    local_env = {
        key: value
        for key, value in os.environ.items()
        if key != "CI" and not key.startswith("GITHUB_")
    }
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--workspace-root", str(ROOT), "--output", str(output)],
        env=local_env,
        text=True,
''',
        label="b17h deterministic local environment",
    )


def repair_b17i_test(source: str) -> str:
    return replace_once(
        source,
        '''def _run(output: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    merged.update(env)
''',
        '''def _run(output: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = {
        key: value
        for key, value in os.environ.items()
        if key != "CI"
        and not key.startswith("GITHUB_")
        and not key.startswith("PRODUCTION_RELEASE_")
        and not key.startswith("RELEASE_INPUT_")
    }
    merged.update(env)
''',
        label="b17i deterministic subprocess environment",
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: b28_quick_gate_repair.py WORKSPACE")
    root = Path(sys.argv[1]).resolve()
    transforms = {
        ALLOWED_PATHS[0]: repair_capability_gate,
        ALLOWED_PATHS[1]: repair_dialogue_runtime,
        ALLOWED_PATHS[2]: repair_fixture,
        ALLOWED_PATHS[3]: repair_goal_tests,
        ALLOWED_PATHS[4]: repair_b17h_test,
        ALLOWED_PATHS[5]: repair_b17i_test,
    }
    results = {}
    for relative, transform in transforms.items():
        before, after = update(root, relative, transform)
        results[relative] = {"before_sha256": before, "after_sha256": after}
    print(json.dumps({"status": "PASS", "changed": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
