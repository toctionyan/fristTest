from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from plugin_gateway import (  # type: ignore
    OPEN_MODE,
    SKILL_MODE,
    WORKFLOW_MODE,
    build_route,
    infer_structural_mode,
)
from plugin_registry import PluginRegistryError, resolve_skill_plugin  # type: ignore
from scope_guard import bootstrap_command_allowed  # type: ignore
from skill_invocation import require_invocation  # type: ignore
from workflow_runtime import FLOW_BLOCKED, FLOW_COMPLETE, run_workflow  # type: ignore
from workflow_spec import WorkflowSpecError, load_workflow_spec, validate_workflow_spec  # type: ignore


class PluggableSkillWorkflowTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="plugin-workflow-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        registry = root / "skill-system/registry"
        registry.mkdir(parents=True, exist_ok=True)
        skills = ["red-baseline-repair", "adversarial-review", "architecture-options"]
        for name in skills:
            path = root / f"skill-system/skills/{name}/SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"---\nname: {name}\ndescription: test\n---\n\n# {name}\n",
                encoding="utf-8",
            )
        (registry / "active-skills.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skills": [
                        {
                            "name": name,
                            "path": f"skill-system/skills/{name}/SKILL.md",
                            "status": "active",
                        }
                        for name in skills
                    ],
                }
            ),
            encoding="utf-8",
        )
        profiles = root / "skill-system/profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "product-quality-quick.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "product-quality-quick",
                    "description": "test executor",
                    "commands": [],
                }
            ),
            encoding="utf-8",
        )
        workflows = root / "skill-system/workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        workflow_payload = {
            "schema_version": 1,
            "id": "repair-and-verify",
            "description": "test",
            "start": "repair",
            "steps": {
                "repair": {
                    "type": "skill",
                    "use": "red-baseline-repair",
                    "max_visits": 3,
                    "transitions": {"PASS": "verify"},
                },
                "verify": {
                    "type": "executor",
                    "use": "profile:product-quality-quick",
                    "max_visits": 3,
                    "transitions": {"GREEN": "adversarial", "RED": "repair"},
                },
                "adversarial": {
                    "type": "skill",
                    "use": "adversarial-review",
                    "max_visits": 3,
                    "transitions": {"GREEN": "END", "RED": "repair"},
                },
            },
            "policy": {
                "taskrun_is_lifecycle_authority": True,
                "workflow_runtime_authority_effect": False,
                "max_visits_are_not_success": True,
            },
        }
        (workflows / "repair-and-verify.json").write_text(
            json.dumps(workflow_payload), encoding="utf-8"
        )
        (registry / "active-workflows.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "workflows": [
                        {
                            "name": "repair-and-verify",
                            "path": "skill-system/workflows/repair-and-verify.json",
                            "status": "active",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_structural_mode_uses_only_explicit_selectors(self) -> None:
        self.assertEqual(infer_structural_mode(), OPEN_MODE)
        self.assertEqual(infer_structural_mode(skill="architecture-options"), SKILL_MODE)
        self.assertEqual(
            infer_structural_mode(workflow="repair-and-verify"), WORKFLOW_MODE
        )

    def test_open_mode_never_auto_selects_a_skill(self) -> None:
        workspace = self.workspace()
        route = build_route(
            workspace,
            mode=infer_structural_mode(),
            payload="分析客服 Agent",
            target="customer-agent",
        )
        self.assertIsNone(route["selected_skill"])
        self.assertTrue(route["policy"]["open_mode_when_unspecified"])
        self.assertFalse(route["policy"]["automatic_skill_selection_allowed"])
        self.assertFalse((workspace / ".quality/skill-invocations").exists())

    def test_explicit_skill_resolves_exact_entry_and_writes_receipt(self) -> None:
        workspace = self.workspace()
        route = build_route(
            workspace,
            mode=infer_structural_mode(skill="architecture-options"),
            skill="architecture-options",
            payload="x",
            invocation_id="skill-1",
        )
        self.assertEqual(route["selected_skill"], "architecture-options")
        self.assertFalse(route["policy"]["fuzzy_skill_fallback_allowed"])
        require_invocation(
            workspace,
            request_class="EXPLICIT_SKILL",
            skill="architecture-options",
        )

    def test_unknown_skill_fails_closed(self) -> None:
        workspace = self.workspace()
        with self.assertRaisesRegex(PluginRegistryError, "active Skill not found"):
            resolve_skill_plugin(workspace, "architecture")

    def test_workflow_route_has_no_fake_top_level_skill_receipt(self) -> None:
        workspace = self.workspace()
        route = build_route(
            workspace,
            mode=infer_structural_mode(workflow="repair-and-verify"),
            workflow="repair-and-verify",
            payload="x",
        )
        self.assertEqual(route["selected_workflow"], "repair-and-verify")
        self.assertIsNone(route["selected_skill"])
        self.assertTrue(route["policy"]["taskrun_is_lifecycle_authority"])
        self.assertFalse((workspace / ".quality/skill-invocations").exists())

    def test_workflow_spec_rejects_unknown_skill(self) -> None:
        workspace = self.workspace()
        spec = load_workflow_spec(workspace, "repair-and-verify")
        spec["steps"]["repair"]["use"] = "missing-skill"
        with self.assertRaisesRegex(WorkflowSpecError, "invalid Skill"):
            validate_workflow_spec(workspace, spec)

    def test_workflow_spec_rejects_unregistered_executor(self) -> None:
        workspace = self.workspace()
        spec = load_workflow_spec(workspace, "repair-and-verify")
        spec["steps"]["verify"]["use"] = "quality-verify"
        with self.assertRaisesRegex(WorkflowSpecError, "profile:<profile>"):
            validate_workflow_spec(workspace, spec)

    def test_langgraph_runtime_loops_then_finishes_without_task_completion_claim(self) -> None:
        workspace = self.workspace()
        spec = load_workflow_spec(workspace, "repair-and-verify")
        counts = {"repair": 0, "verify": 0, "attack": 0}

        def repair(state, step):
            counts["repair"] += 1
            return {"outcome": "PASS"}

        def verify(state, step):
            counts["verify"] += 1
            return {"outcome": "RED" if counts["verify"] == 1 else "GREEN"}

        def attack(state, step):
            counts["attack"] += 1
            return {"outcome": "RED" if counts["attack"] == 1 else "GREEN"}

        result = run_workflow(
            spec,
            handlers={
                "skill:red-baseline-repair": repair,
                "executor:profile:product-quality-quick": verify,
                "skill:adversarial-review": attack,
            },
            initial_state={"task_id": "task-1"},
        )
        self.assertEqual(result["status"], FLOW_COMPLETE)
        self.assertFalse(result["authority_effect"])
        self.assertNotEqual(result["status"], "COMPLETED")
        self.assertEqual(result["visits"]["repair"], 3)

    def test_loop_budget_exhaustion_blocks(self) -> None:
        workspace = self.workspace()
        spec = {
            "schema_version": 1,
            "id": "bounded-loop",
            "description": "test",
            "start": "repair",
            "steps": {
                "repair": {
                    "type": "skill",
                    "use": "red-baseline-repair",
                    "max_visits": 2,
                    "transitions": {"RED": "repair", "BLOCKED": "END"},
                }
            },
            "policy": {
                "taskrun_is_lifecycle_authority": True,
                "workflow_runtime_authority_effect": False,
                "max_visits_are_not_success": True,
            },
        }
        result = run_workflow(
            validate_workflow_spec(workspace, spec),
            handlers={
                "skill:red-baseline-repair": lambda state, step: {"outcome": "RED"}
            },
        )
        self.assertEqual(result["status"], FLOW_BLOCKED)
        self.assertIn("visit budget exhausted", result["error"])

    def test_new_commands_are_bootstrap_safe(self) -> None:
        self.assertTrue(
            bootstrap_command_allowed("python3 -B skillctl.py invoke --payload x")
        )
        self.assertTrue(
            bootstrap_command_allowed(
                "python3 -B skillctl.py invoke --skill architecture-options --payload x --invocation-id x"
            )
        )
        self.assertTrue(
            bootstrap_command_allowed(
                "python3 -B skillctl.py workflow-validate --workflow repair-and-verify"
            )
        )


if __name__ == "__main__":
    unittest.main()
