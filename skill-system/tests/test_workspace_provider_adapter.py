from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from capability_registry import CapabilityBinding  # type: ignore
from workflow_graph_contract import WorkflowStepSpec  # type: ignore
from workspace_provider_adapter import StructuredWorkspaceProviderAdapter  # type: ignore


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class WorkspaceProviderAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="workspace-provider-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        self.adapter = StructuredWorkspaceProviderAdapter(
            workspace=self.root,
            allowed_path_patterns=("src/**", "tests/**"),
        )

    @staticmethod
    def binding() -> CapabilityBinding:
        return CapabilityBinding(
            capability_id="workspace.write",
            provider_id="local.workspace",
            provider_type="executor",
            activation_key="local.workspace",
            mutates=True,
            external_wait=False,
        )

    @staticmethod
    def step() -> WorkflowStepSpec:
        return WorkflowStepSpec(
            step_id="apply-patch",
            step_type="executor",
            use="workspace.write",
            routes={"green": "END", "blocked": "BLOCKED_UNRECOVERABLE"},
            max_attempts=2,
        )

    def invoke(self, operations):
        return self.adapter.invoke(
            binding=self.binding(),
            step=self.step(),
            state={
                "task_id": "task-workspace",
                "step_attempts": {},
                "target_ref": {
                    "workspace_requests": {
                        "apply-patch": {
                            "schema": "workflow-workspace-mutation-request@1",
                            "capability_id": "workspace.write",
                            "operations": operations,
                        }
                    }
                },
            },
        )

    def test_create_replace_delete_transaction_has_exact_digest_receipt(self) -> None:
        (self.root / "src/existing.py").write_text("old\n", encoding="utf-8")
        (self.root / "tests/obsolete.py").write_text("remove\n", encoding="utf-8")
        result = self.invoke(
            [
                {
                    "operation": "create",
                    "path": "src/new.py",
                    "content": "new\n",
                    "content_sha256": digest("new\n"),
                },
                {
                    "operation": "replace",
                    "path": "src/existing.py",
                    "expected_sha256": digest("old\n"),
                    "content": "updated\n",
                    "content_sha256": digest("updated\n"),
                },
                {
                    "operation": "delete",
                    "path": "tests/obsolete.py",
                    "expected_sha256": digest("remove\n"),
                },
            ]
        )

        self.assertEqual(result.outcome, "green")
        self.assertEqual((self.root / "src/new.py").read_text(), "new\n")
        self.assertEqual((self.root / "src/existing.py").read_text(), "updated\n")
        self.assertFalse((self.root / "tests/obsolete.py").exists())
        operations = result.payload["operations"]
        self.assertEqual([row["path"] for row in operations], [
            "src/new.py", "src/existing.py", "tests/obsolete.py"
        ])
        self.assertEqual(operations[0]["after_sha256"], digest("new\n"))
        self.assertEqual(operations[2]["after_sha256"], None)
        self.assertFalse(result.payload["authority_effect"])
        self.assertFalse(result.payload["merge_allowed"])

    def test_traversal_absolute_protected_duplicate_stale_and_symlink_fail_closed(self) -> None:
        (self.root / "src/existing.py").write_text("old\n", encoding="utf-8")
        outside = self.root.parent / f"{self.root.name}-outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        os.symlink(outside, self.root / "src/link.py")
        cases = {
            "traversal": [{
                "operation": "create", "path": "../escape.py",
                "content": "x", "content_sha256": digest("x")
            }],
            "absolute": [{
                "operation": "create", "path": "/tmp/escape.py",
                "content": "x", "content_sha256": digest("x")
            }],
            "protected": [{
                "operation": "create", "path": ".quality/fake.json",
                "content": "x", "content_sha256": digest("x")
            }],
            "duplicate": [
                {"operation": "replace", "path": "src/existing.py", "expected_sha256": digest("old\n"), "content": "a", "content_sha256": digest("a")},
                {"operation": "delete", "path": "src/existing.py", "expected_sha256": digest("old\n")},
            ],
            "stale": [{
                "operation": "replace", "path": "src/existing.py",
                "expected_sha256": digest("wrong"), "content": "x", "content_sha256": digest("x")
            }],
            "symlink": [{
                "operation": "replace", "path": "src/link.py",
                "expected_sha256": digest("outside\n"), "content": "x", "content_sha256": digest("x")
            }],
        }
        for name, operations in cases.items():
            with self.subTest(name=name):
                result = self.invoke(operations)
                self.assertEqual(result.outcome, "blocked")
        self.assertEqual((self.root / "src/existing.py").read_text(), "old\n")
        self.assertEqual(outside.read_text(), "outside\n")

    def test_mid_transaction_failure_rolls_back_prior_write(self) -> None:
        original_atomic_write = self.adapter._atomic_write
        calls = 0

        def fail_second(path, content, *, mode=0o644):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second-write failure")
            return original_atomic_write(path, content, mode=mode)

        with mock.patch.object(self.adapter, "_atomic_write", side_effect=fail_second):
            result = self.invoke(
                [
                    {"operation": "create", "path": "src/first.py", "content": "one", "content_sha256": digest("one")},
                    {"operation": "create", "path": "src/second.py", "content": "two", "content_sha256": digest("two")},
                ]
            )
        self.assertEqual(result.outcome, "blocked")
        self.assertIn("rolled back", result.payload["error"])
        self.assertFalse((self.root / "src/first.py").exists())
        self.assertFalse((self.root / "src/second.py").exists())


if __name__ == "__main__":
    unittest.main()
