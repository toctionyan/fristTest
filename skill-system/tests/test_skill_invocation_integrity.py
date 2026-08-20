from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

import skill_invocation_cli as invocation_cli  # type: ignore
from host_conformance import verify as verify_host_conformance  # type: ignore
from scope_guard import bootstrap_command_allowed  # type: ignore
from skill_invocation import (  # type: ignore
    ACTIVE_INDEX,
    SKILL_INVOCATION_INDEX_SCHEMA,
    SkillInvocationError,
    build_receipt,
    find_active_receipt,
    require_change_scope_invocation,
    require_invocation,
    validate_receipt,
    write_receipt,
)

ROOT = Path(__file__).resolve().parents[2]


class SkillInvocationIntegrityTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="skill-invocation-"))
        for name in (
            "change-scope",
            "adversarial-review",
            "task-execution-status",
            "product-code-governance",
        ):
            skill = root / f"skill-system/skills/{name}/SKILL.md"
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text(f"---\nname: {name}\ndescription: test\n---\n\n# {name}\n", encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
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
        with self.assertRaisesRegex(SkillInvocationError, "active index is missing"):
            require_change_scope_invocation(workspace, change_id="change-123")

    def test_wrong_selected_skill_cannot_build_pass_receipt(self) -> None:
        workspace = self.workspace()
        with self.assertRaisesRegex(SkillInvocationError, "does not match"):
            self.receipt(workspace, selected_skill="adversarial-review")

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
        with self.assertRaisesRegex(SkillInvocationError, "active Skill invocation receipt is missing"):
            require_change_scope_invocation(workspace, change_id="change-999")

    def test_response_binding_is_a_distinct_stronger_requirement(self) -> None:
        workspace = self.workspace()
        payload = self.receipt(workspace)
        validate_receipt(workspace, payload)
        with self.assertRaisesRegex(SkillInvocationError, "deterministic response"):
            validate_receipt(workspace, payload, require_response_bound=True)
        bound = self.receipt(workspace, invocation_id="change-123-response", response_bound=True)
        self.assertTrue(validate_receipt(workspace, bound, require_response_bound=True)["output"]["response_bound"])

    def test_response_bind_command_promotes_loaded_skill_to_response_bound_receipt(self) -> None:
        workspace = self.workspace()
        source = self.receipt(
            workspace,
            invocation_id="diagnosis-load-1",
            request_class="DIAGNOSIS",
            required_skill="product-code-governance",
            selected_skill="product-code-governance",
            entrypoint="skill-system/skills/product-code-governance/SKILL.md",
            output_content="diagnosis governance context",
            change_id=None,
        )
        write_receipt(workspace, source)
        args = argparse.Namespace(
            receipt=None,
            request_class="DIAGNOSIS",
            skill="product-code-governance",
            change_id=None,
            task_id=None,
            invocation_id="diagnosis-response-1",
            response="final diagnosis based on the loaded Skill",
            response_file=None,
            evidence_ref=None,
        )
        with mock.patch.object(invocation_cli, "ROOT", workspace):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(invocation_cli.cmd_bind_response(args), 0)
        bound = require_invocation(
            workspace,
            request_class="DIAGNOSIS",
            skill="product-code-governance",
            require_response_bound=True,
        )
        self.assertEqual(bound["invocation_id"], "diagnosis-response-1")
        self.assertEqual(bound["output"]["schema"], "host-response@1")
        self.assertIn(source["receipt_fingerprint_sha256"], bound["output"]["evidence_ref"])

    def test_response_bind_rejects_workspace_escape_for_response_file(self) -> None:
        workspace = self.workspace()
        with mock.patch.object(invocation_cli, "ROOT", workspace):
            with self.assertRaisesRegex(SkillInvocationError, "inside the workspace"):
                invocation_cli._workspace_path("../outside-response.txt")

    def test_loading_another_skill_does_not_evict_change_scope_receipt(self) -> None:
        workspace = self.workspace()
        write_receipt(workspace, self.receipt(workspace))
        review = self.receipt(
            workspace,
            invocation_id="change-123-adversarial",
            request_class="ADVERSARIAL_REVIEW",
            required_skill="adversarial-review",
            selected_skill="adversarial-review",
            entrypoint="skill-system/skills/adversarial-review/SKILL.md",
            output_content="adversarial context",
        )
        write_receipt(workspace, review)

        change_scope = require_change_scope_invocation(workspace, change_id="change-123")
        adversarial = require_invocation(
            workspace,
            request_class="ADVERSARIAL_REVIEW",
            skill="adversarial-review",
            change_id="change-123",
        )
        self.assertEqual(change_scope["invocation_id"], "change-123-load-1")
        self.assertEqual(adversarial["invocation_id"], "change-123-adversarial")

        index = json.loads((workspace / ACTIVE_INDEX).read_text(encoding="utf-8"))
        self.assertEqual(index["schema"], SKILL_INVOCATION_INDEX_SCHEMA)
        self.assertEqual(len(index["entries"]), 2)

    def test_reloading_same_skill_subject_replaces_only_that_active_key(self) -> None:
        workspace = self.workspace()
        first = self.receipt(workspace)
        second = self.receipt(workspace, invocation_id="change-123-load-2", output_content="new context")
        write_receipt(workspace, first)
        write_receipt(workspace, second)
        path, active = find_active_receipt(
            workspace,
            request_class="CHANGE_SCOPE",
            skill="change-scope",
            change_id="change-123",
        )
        self.assertEqual(active["invocation_id"], "change-123-load-2")
        self.assertTrue(path.name.endswith("change-123-load-2.json"))
        self.assertTrue((workspace / ".quality/skill-invocations/change-123-load-1.json").is_file())

    def test_index_fingerprint_tampering_fails_closed(self) -> None:
        workspace = self.workspace()
        write_receipt(workspace, self.receipt(workspace))
        index_path = workspace / ACTIVE_INDEX
        index = json.loads(index_path.read_text(encoding="utf-8"))
        key = next(iter(index["entries"]))
        index["entries"][key]["receipt_fingerprint_sha256"] = "0" * 64
        index_path.write_text(json.dumps(index), encoding="utf-8")
        with self.assertRaisesRegex(SkillInvocationError, "index fingerprint mismatch"):
            require_change_scope_invocation(workspace, change_id="change-123")

    def test_skill_invocation_commands_are_bootstrap_safe(self) -> None:
        for command in (
            "python3 -B skillctl.py skill-load --skill change-scope --request-class CHANGE_SCOPE --invocation-id x --change-id x",
            "python3 -B skillctl.py skill-response-bind --request-class CHANGE_SCOPE --skill change-scope --change-id x --invocation-id y --response done",
            "python3 -B skillctl.py skill-invocation-verify --request-class CHANGE_SCOPE --skill change-scope --change-id x",
            "python3 -B skillctl.py task-status-project --task-run task.json --invocation-id x",
        ):
            with self.subTest(command=command):
                self.assertTrue(bootstrap_command_allowed(command))

    def test_repository_exposes_status_skill_and_runtime_invocation_guards(self) -> None:
        canonical = (ROOT / "skill-system/skills/task-execution-status/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("task-status-project", canonical)
        self.assertIn("execution-progress@1", canonical)
        self.assertIn("skill-response-bind", (ROOT / "skillctl.py").read_text(encoding="utf-8"))
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
