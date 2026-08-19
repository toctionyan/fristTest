from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from host_conformance import verify as verify_host_conformance  # type: ignore
from scope_guard import bootstrap_command_allowed  # type: ignore
from skill_invocation import (  # type: ignore
    SkillInvocationError,
    build_receipt,
    require_change_scope_invocation,
    validate_receipt,
    write_receipt,
)

ROOT = Path(__file__).resolve().parents[2]


class SkillInvocationIntegrityTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="skill-invocation-"))
        skill = root / "skill-system/skills/change-scope/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: change-scope\ndescription: test\n---\n\n# Change Scope\n", encoding="utf-8")
        entrypoint = root / "skill-system/skills/change-scope/SKILL.md"
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        self.assertTrue(entrypoint.is_file())
        return root

    def receipt(self, workspace: Path, **overrides):
        values = {
            "invocation_id": "change-123-load-1",
            "request_class": "CHANGE_SCOPE",
            "required_skill": "change-scope",
            "selected_skill": "change-scope",
            "entrypoint": "skill-system/skills/change-scope/SKILL.md",
            "output_schema": "skill-context@1",
            "output_content": "canonical skill context",
            "output_evidence_ref": "stdout:skill_context",
            "change_id": "change-123",
            "response_bound": False,
        }
        values.update(overrides)
        return build_receipt(workspace, **values)

    def test_valid_change_scope_receipt_is_bound_to_contract_and_current_skill_digest(self) -> None:
        workspace = self.workspace()
        payload = self.receipt(workspace)
        path = write_receipt(workspace, payload)
        self.assertTrue(path.is_file())
        validated = require_change_scope_invocation(workspace, change_id="change-123")
        self.assertEqual(validated["selected_skill"], "change-scope")
        self.assertEqual(validated["subject"]["change_id"], "change-123")

    def test_static_skill_presence_without_runtime_receipt_fails_closed(self) -> None:
        workspace = self.workspace()
        self.assertTrue((workspace / "skill-system/skills/change-scope/SKILL.md").is_file())
        with self.assertRaisesRegex(SkillInvocationError, "receipt is missing"):
            require_change_scope_invocation(workspace, change_id="change-123")

    def test_wrong_selected_skill_cannot_build_pass_receipt(self) -> None:
        workspace = self.workspace()
        other = workspace / "skill-system/skills/other/SKILL.md"
        other.parent.mkdir(parents=True)
        other.write_text("---\nname: other\ndescription: test\n---\n", encoding="utf-8")
        with self.assertRaisesRegex(SkillInvocationError, "does not match"):
            self.receipt(workspace, selected_skill="other")

    def test_stale_receipt_is_rejected_after_canonical_skill_changes(self) -> None:
        workspace = self.workspace()
        payload = self.receipt(workspace)
        write_receipt(workspace, payload)
        skill = workspace / "skill-system/skills/change-scope/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
        with self.assertRaisesRegex(SkillInvocationError, "stale"):
            require_change_scope_invocation(workspace, change_id="change-123")

    def test_receipt_for_another_change_cannot_authorize_write(self) -> None:
        workspace = self.workspace()
        write_receipt(workspace, self.receipt(workspace))
        with self.assertRaisesRegex(SkillInvocationError, "change_id"):
            require_change_scope_invocation(workspace, change_id="change-999")

    def test_response_binding_is_a_distinct_stronger_requirement(self) -> None:
        workspace = self.workspace()
        payload = self.receipt(workspace)
        validate_receipt(workspace, payload)
        with self.assertRaisesRegex(SkillInvocationError, "deterministic response"):
            validate_receipt(workspace, payload, require_response_bound=True)
        bound = self.receipt(workspace, invocation_id="change-123-response", response_bound=True)
        self.assertTrue(validate_receipt(workspace, bound, require_response_bound=True)["output"]["response_bound"])

    def test_skill_invocation_commands_are_bootstrap_safe(self) -> None:
        for command in (
            "python3 -B skillctl.py skill-load --skill change-scope --request-class CHANGE_SCOPE --invocation-id x --change-id x",
            "python3 -B skillctl.py skill-invocation-verify --skill change-scope",
            "python3 -B skillctl.py task-status-project --task-run task.json --invocation-id x",
        ):
            with self.subTest(command=command):
                self.assertTrue(bootstrap_command_allowed(command))

    def test_repository_exposes_status_skill_and_runtime_invocation_guards(self) -> None:
        canonical = (ROOT / "skill-system/skills/task-execution-status/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("task-status-project", canonical)
        self.assertIn("execution-progress@1", canonical)
        for adapter in (
            ROOT / ".agents/skills/task-execution-status/SKILL.md",
            ROOT / ".claude/skills/task-execution-status/SKILL.md",
        ):
            self.assertTrue(adapter.is_file())
            self.assertIn("skill-system/skills/task-execution-status/SKILL.md", adapter.read_text(encoding="utf-8"))
        errors = verify_host_conformance()
        self.assertFalse([error for error in errors if error.startswith("runtime_invocation_guard_missing:")], errors)
        self.assertNotIn("missing_canonical_status_skill", errors)
        self.assertFalse([error for error in errors if error.startswith("missing_skillctl_invocation_command:")], errors)

    def test_external_chatgpt_boundary_is_documented_without_false_interception_claim(self) -> None:
        doc = (ROOT / "docs/architecture/HOST_SKILL_INVOCATION_INTEGRITY.md").read_text(encoding="utf-8")
        self.assertIn("Host integration unverified", doc)
        self.assertIn("cannot intercept the ChatGPT product harness", doc)
        self.assertIn("no repository receipt may be fabricated", doc)


if __name__ == "__main__":
    unittest.main()
