from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SKILL_SYSTEM = Path(__file__).resolve().parents[1]
CONTROLLER = SKILL_SYSTEM / "controller"
if str(SKILL_SYSTEM) not in sys.path:
    sys.path.insert(0, str(SKILL_SYSTEM))
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from runtime import HarnessRuntimeEngine, HarnessRuntimeStatus  # type: ignore  # noqa: E402
from workflow_registry import load_workflow_registry  # type: ignore  # noqa: E402


ROOT = SKILL_SYSTEM.parent
FULL_DEVELOPMENT = SKILL_SYSTEM / "registry" / "full-development-workflow.json"
ACTIVE_SKILLS = SKILL_SYSTEM / "registry" / "active-skills.json"


class HarnessFullDevelopmentFoundationTest(unittest.TestCase):
    def test_runtime_cannot_claim_taskrun_completion(self) -> None:
        self.assertNotIn("COMPLETED", {status.value for status in HarnessRuntimeStatus})

        engine = HarnessRuntimeEngine()
        started = engine.start(task_id="task-1", workflow_id="harness-full-dev")
        ended = engine.end_flow(started, evidence_ref="file:evidence/flow-ended.json")

        self.assertEqual(started.status, HarnessRuntimeStatus.RUNNING)
        self.assertEqual(ended.status, HarnessRuntimeStatus.FLOW_ENDED)
        self.assertEqual(ended.completion_authority, "TaskRun")
        self.assertFalse(ended.authority_effect)

    def test_runtime_transitions_are_immutable_and_evidence_is_deduplicated(self) -> None:
        engine = HarnessRuntimeEngine()
        started = engine.start(task_id="task-1", workflow_id="harness-full-dev")
        moved = engine.move(started, step="diagnose")
        waiting = engine.wait_external(moved, evidence_ref="event:ci-1")
        repeated = engine.wait_external(waiting, evidence_ref="event:ci-1")

        self.assertIsNone(started.current_step)
        self.assertEqual(moved.current_step, "diagnose")
        self.assertEqual(started.status, HarnessRuntimeStatus.RUNNING)
        self.assertEqual(waiting.status, HarnessRuntimeStatus.WAITING_EXTERNAL)
        self.assertEqual(repeated.evidence_refs, ("event:ci-1",))

    def test_full_development_manifest_reuses_live_skills_and_workflows(self) -> None:
        payload = json.loads(FULL_DEVELOPMENT.read_text(encoding="utf-8"))
        workflows = load_workflow_registry(ROOT)
        skill_rows = json.loads(ACTIVE_SKILLS.read_text(encoding="utf-8"))["skills"]
        active_skills = {
            str(row["name"])
            for row in skill_rows
            if isinstance(row, dict) and row.get("status") == "active"
        }

        self.assertEqual(payload["schema"], "full-development-workflow@1")
        self.assertEqual(payload["workflow_id"], "harness-full-dev")
        self.assertEqual(payload["completion_authority"], "TaskRun")

        for step in payload["steps"].values():
            if step["type"] == "skill":
                self.assertIn(step["use"], active_skills)
            elif step["type"] == "workflow":
                self.assertIn(step["use"], workflows)

    def test_publication_child_owns_post_merge_validation_exactly_once(self) -> None:
        payload = json.loads(FULL_DEVELOPMENT.read_text(encoding="utf-8"))
        steps = payload["steps"]
        publication = load_workflow_registry(ROOT)["publication-e2e"]

        self.assertEqual(steps["publication-e2e"]["next"], "END")
        self.assertNotIn("post-merge-validation", steps)
        self.assertIsNotNone(publication.graph)
        uses = [step.use for step in publication.graph.steps.values()]
        self.assertEqual(uses.count("publication.post_merge.validation.wait"), 1)


if __name__ == "__main__":
    unittest.main()
