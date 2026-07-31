from __future__ import annotations

from pathlib import Path

from tests.support.paths import workspace_root


REQUIRED_CURRENT_MARKERS = (
    "frozen_semantic_contract",
    "goal_records",
    "goal_blockers",
    "frozen_plan_definition",
    "plan_run",
    "MatchProof",
    "ExecutionPermit",
    "RuntimeOutcome",
)


def test_current_architecture_document_is_the_only_current_owner() -> None:
    root = workspace_root(__file__)
    docs = root / "docs" / "architecture"
    current = docs / "CURRENT_ARCHITECTURE.md"
    assert current.is_file(), "missing unique current architecture entrypoint"
    current_text = current.read_text(encoding="utf-8")
    assert "唯一当前架构权威" in current_text
    for marker in REQUIRED_CURRENT_MARKERS:
        assert marker in current_text, marker

    target_text = (docs / "TARGET_ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "CURRENT_ARCHITECTURE.md" in target_text
    assert "→ TurnGoalPlan（本 user turn 的目标声明）" not in target_text
    assert "当前 `WorkflowPlan` 只在一个 user turn 内存在" not in target_text

    for legacy_name in ("TURN_GOAL_PLAN_RECORD.md", "WORKFLOW_PLAN_RECORD.md"):
        legacy_text = (docs / legacy_name).read_text(encoding="utf-8")
        header = "\n".join(legacy_text.splitlines()[:12])
        assert "SUPERSEDED" in header, legacy_name
        assert "CURRENT_ARCHITECTURE.md" in header, legacy_name

    overview = (docs / "overview.md").read_text(encoding="utf-8")
    assert "CURRENT_ARCHITECTURE.md" in overview
