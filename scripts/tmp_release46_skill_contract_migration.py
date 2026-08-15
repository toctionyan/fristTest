#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SKILL_TEST_PATH = "skill-system/tests/test_wp08_new_release_attempt4_repair.py"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_skill_contract(root: Path) -> None:
    path = root / SKILL_TEST_PATH
    old = '''    def test_bounded_repair_forwards_exact_runtime_result_without_oracle_material(self) -> None:\n        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")\n        start = source.index("def _declare_with_bounded_production_repair")\n        end = source.index("def _identity_failure_reason", start)\n        helper = source[start:end]\n        self.assertIn("_validate_with_production_goal_contract", helper)\n        self.assertIn("except RuntimeError as exc", helper)\n        self.assertIn("isinstance(exc, _ProductionGoalDeclarationRejected)", helper)\n        self.assertIn("content=json.dumps(result, ensure_ascii=False, default=str)", helper)\n        self.assertIn('name="declare_turn_goals"', helper)\n        self.assertIn("for attempt in range(1, 3)", helper)\n        self.assertNotIn("goal_oracle", helper)\n        self.assertNotIn("_match_oracle", helper)\n        self.assertNotIn("expected_effect", helper)\n'''
    new = '''    def test_bounded_repair_forwards_runtime_writer_projection_without_oracle_material(self) -> None:\n        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")\n        start = source.index("def _declare_with_bounded_production_repair")\n        end = source.index("def _identity_failure_reason", start)\n        helper = source[start:end]\n        adapter_start = source.index("def _semantic_writer_rejection_tool_message")\n        adapter_end = source.index("class _ProductionGoalDeclarationRejected", adapter_start)\n        adapter = source[adapter_start:adapter_end]\n        self.assertIn("_validate_with_production_goal_contract", helper)\n        self.assertIn("except RuntimeError as exc", helper)\n        self.assertIn("isinstance(exc, _ProductionGoalDeclarationRejected)", helper)\n        self.assertIn("_semantic_writer_rejection_tool_message(", helper)\n        self.assertIn("result=result", helper)\n        self.assertNotIn("content=json.dumps(result, ensure_ascii=False, default=str)", helper)\n        self.assertIn("_semantic_writer_declaration_result_projection(result)", adapter)\n        self.assertIn("content=json.dumps(projected, ensure_ascii=False, default=str)", adapter)\n        self.assertIn('name="declare_turn_goals"', adapter)\n        self.assertIn("for attempt in range(1, 3)", helper)\n        self.assertNotIn("goal_oracle", helper)\n        self.assertNotIn("_match_oracle", helper)\n        self.assertNotIn("expected_effect", helper)\n        self.assertNotIn("goal_oracle", adapter)\n        self.assertNotIn("_match_oracle", adapter)\n        self.assertNotIn("expected_effect", adapter)\n'''
    replace_once(path, old, new, "Skill certification writer projection contract")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    patch_skill_contract(Path(args.workspace).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
