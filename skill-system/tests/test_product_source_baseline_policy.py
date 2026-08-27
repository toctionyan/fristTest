from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system/controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from product_source_baseline_policy import (  # type: ignore
    BaselineMode,
    ProductSourcePolicyError,
    SnapshotSource,
    baseline_mode_for_authority,
    build_canonical_product_snapshot,
    evaluate_binding,
    file_sha256,
    load_baseline_document,
    validate_v3_product_snapshot,
    validate_baseline_document,
)


class ProductSourceBaselinePolicyMatrixTests(unittest.TestCase):
    @staticmethod
    def _workspace() -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "services/app.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
        return temporary, root, file_sha256(source)

    @staticmethod
    def _baseline(files: dict[str, str]) -> dict[str, object]:
        return {
            "schema_version": 2,
            "protected_roots": ["services", "web", "contracts"],
            "file_count": len(files),
            "files": files,
            "generated_from": "git:" + "0" * 40,
        }

    def test_pr_candidate_drift_is_pre_acceptance(self) -> None:
        temporary, root, digest = self._workspace()
        with temporary:
            result = evaluate_binding(
                root,
                expected={"services/app.py": "f" * 64},
                protected_roots=("services", "web", "contracts"),
                mode=BaselineMode.PR_CANDIDATE,
                source=SnapshotSource.OFFLINE_PACKAGE,
            )
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.drift_paths, ("services/app.py",))
        self.assertNotEqual(digest, "f" * 64)

    def test_accepted_ref_same_drift_fails(self) -> None:
        temporary, root, _ = self._workspace()
        with temporary:
            result = evaluate_binding(
                root,
                expected={"services/app.py": "f" * 64},
                protected_roots=("services", "web", "contracts"),
                mode=BaselineMode.ACCEPTED_REF,
                source=SnapshotSource.OFFLINE_PACKAGE,
            )
        self.assertEqual(result.status, "FAIL")
        self.assertIn("protected_baseline_drift", result.errors)

    def test_accepted_ref_exact_snapshot_passes(self) -> None:
        temporary, root, digest = self._workspace()
        with temporary:
            result = evaluate_binding(
                root,
                expected={"services/app.py": digest},
                protected_roots=("services", "web", "contracts"),
                mode=BaselineMode.ACCEPTED_REF,
                source=SnapshotSource.OFFLINE_PACKAGE,
            )
        self.assertEqual(result.status, "PASS")

    def test_baseline_acceptance_exposes_drift_without_pre_accepting_it(self) -> None:
        temporary, root, _ = self._workspace()
        with temporary:
            result = evaluate_binding(
                root,
                expected={"services/app.py": "f" * 64},
                protected_roots=("services", "web", "contracts"),
                mode=BaselineMode.BASELINE_ACCEPTANCE,
                source=SnapshotSource.OFFLINE_PACKAGE,
            )
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.drift_paths, ("services/app.py",))

    def test_feature_branch_push_is_candidate_not_accepted_ref(self) -> None:
        mode = baseline_mode_for_authority(
            "historical-registry-baseline",
            event_name="push",
            ref_type="branch",
            ref_name="repair/issue167-a1-semantic-goal-oracle-20260817",
            default_branch="main",
        )
        self.assertEqual(mode, BaselineMode.PR_CANDIDATE)

    def test_feature_branch_push_uses_actual_github_event_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            event_path = Path(temporary) / "event.json"
            event_path.write_text(
                json.dumps({"repository": {"default_branch": "main"}}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "GITHUB_EVENT_NAME": "push",
                    "GITHUB_REF_TYPE": "branch",
                    "GITHUB_REF_NAME": "repair/issue167-a1-semantic-goal-oracle-20260817",
                    "GITHUB_EVENT_PATH": str(event_path),
                },
                clear=False,
            ):
                mode = baseline_mode_for_authority("historical-registry-baseline")
        self.assertEqual(mode, BaselineMode.PR_CANDIDATE)

    def test_default_branch_push_remains_accepted_ref(self) -> None:
        mode = baseline_mode_for_authority(
            "historical-registry-baseline",
            event_name="push",
            ref_type="branch",
            ref_name="main",
            default_branch="main",
        )
        self.assertEqual(mode, BaselineMode.ACCEPTED_REF)

    def test_tag_push_remains_fail_closed_as_accepted_ref(self) -> None:
        mode = baseline_mode_for_authority(
            "historical-registry-baseline",
            event_name="push",
            ref_type="tag",
            ref_name="v1.2.3",
            default_branch="main",
        )
        self.assertEqual(mode, BaselineMode.ACCEPTED_REF)

    def test_missing_push_ref_identity_remains_fail_closed(self) -> None:
        mode = baseline_mode_for_authority(
            "historical-registry-baseline",
            event_name="push",
            ref_type="",
            ref_name="",
            default_branch="",
        )
        self.assertEqual(mode, BaselineMode.ACCEPTED_REF)

    def test_missing_empty_protected_root_is_allowed(self) -> None:
        temporary, root, digest = self._workspace()
        with temporary:
            result = evaluate_binding(
                root,
                expected={"services/app.py": digest},
                protected_roots=("services", "web"),
                mode=BaselineMode.ACCEPTED_REF,
                source=SnapshotSource.OFFLINE_PACKAGE,
            )
        self.assertEqual(result.status, "PASS")

    def test_missing_protected_root_with_recorded_file_fails(self) -> None:
        temporary, root, digest = self._workspace()
        with temporary:
            result = evaluate_binding(
                root,
                expected={
                    "services/app.py": digest,
                    "web/index.html": "0" * 64,
                },
                protected_roots=("services", "web"),
                mode=BaselineMode.PR_CANDIDATE,
                source=SnapshotSource.OFFLINE_PACKAGE,
            )
        self.assertEqual(result.status, "FAIL")
        self.assertIn("protected_root_missing:web", result.errors)

    def test_invalid_baseline_document_fails_before_lifecycle_policy(self) -> None:
        payload = self._baseline({"services/app.py": "0" * 64})
        payload["file_count"] = 2
        self.assertIn("baseline_file_count_mismatch", validate_baseline_document(payload))

    def test_baseline_loader_rejects_tampering(self) -> None:
        temporary, root, _ = self._workspace()
        with temporary:
            path = root / "skill-system/registry/product-source-baseline.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._baseline({"services/app.py": "0" * 64})
            payload["generated_from"] = "not-a-git-binding"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ProductSourcePolicyError):
                load_baseline_document(root)


class ProductSourceBaselineSingleAuthorityTests(unittest.TestCase):
    def test_consumers_do_not_reimplement_lifecycle_or_git_snapshot_policy(self) -> None:
        consumers = [
            ROOT / "skill-system/controller/project_compatibility.py",
            ROOT / "scripts/verify_product_source_baseline.py",
            ROOT / "scripts/github_repair_baseline_acceptance.py",
            ROOT / "skill-system/tests/test_product_source_baseline_binding.py",
        ]
        for path in consumers:
            text = path.read_text(encoding="utf-8")
            self.assertIn("product_source_baseline_policy", text, path.as_posix())
            self.assertNotIn("GITHUB_EVENT_NAME", text, path.as_posix())
            self.assertNotIn('"ls-files"', text, path.as_posix())
            self.assertNotIn("PROTECTED_NAMES", text, path.as_posix())


class CanonicalProductSnapshotTests(unittest.TestCase):
    STAGE2B1_SHA = "6c41cb862ba065e474aa3a7f213209d1eacfef45"
    STAGE2B1_MERGE_SHA = "4c80d7b79f395bd1d93478043ba1ed25688c8547"
    PROTECTED_ROOTS = ("services", "web", "contracts")

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
        ).strip()

    @classmethod
    def _init_repo(cls, root: Path) -> None:
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        cls._git(root, "config", "user.name", "Snapshot Test")
        cls._git(root, "config", "user.email", "snapshot@example.com")

    def test_stage2b1_commits_have_same_protected_snapshot_digest(self) -> None:
        first = build_canonical_product_snapshot(
            ROOT, self.STAGE2B1_SHA, self.PROTECTED_ROOTS
        )
        second = build_canonical_product_snapshot(
            ROOT, self.STAGE2B1_MERGE_SHA, self.PROTECTED_ROOTS
        )
        self.assertEqual(
            first["protected_snapshot_digest"],
            second["protected_snapshot_digest"],
        )
        self.assertEqual(first["entries"], second["entries"])
        self.assertEqual(validate_v3_product_snapshot(first), [])
        self.assertEqual(validate_v3_product_snapshot(second), [])

    def test_tampering_entries_breaks_v3_preflight(self) -> None:
        snapshot = build_canonical_product_snapshot(
            ROOT, self.STAGE2B1_SHA, self.PROTECTED_ROOTS
        )
        snapshot["entries"] = list(snapshot["entries"])
        snapshot["entries"][0] = dict(snapshot["entries"][0])
        snapshot["entries"][0]["digest"] = "sha256:" + "0" * 64
        errors = validate_v3_product_snapshot(snapshot)
        self.assertIn("v3_protected_snapshot_digest_mismatch", errors)

    def test_v3_preflight_rejects_unknown_path_and_count(self) -> None:
        snapshot = build_canonical_product_snapshot(
            ROOT, self.STAGE2B1_SHA, self.PROTECTED_ROOTS
        )
        snapshot["unexpected"] = True
        snapshot["entry_count"] = snapshot["entry_count"] + 1
        snapshot["entries"] = list(snapshot["entries"])
        snapshot["entries"][0] = dict(snapshot["entries"][0])
        snapshot["entries"][0]["path"] = "../outside"
        errors = validate_v3_product_snapshot(snapshot)
        self.assertIn("v3_unknown_field:unexpected", errors)
        self.assertIn("v3_entry_path_invalid:0", errors)
        self.assertIn("v3_entry_count_mismatch", errors)

    def test_snapshot_ignores_worktree_and_index_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._init_repo(root)
            path = root / "services/app.py"
            path.parent.mkdir(parents=True)
            path.write_text("committed\n", encoding="utf-8")
            self._git(root, "add", "services/app.py")
            self._git(root, "commit", "-qm", "initial")
            commit_sha = self._git(root, "rev-parse", "HEAD")
            expected = build_canonical_product_snapshot(
                root, commit_sha, ("services",)
            )
            path.write_text("worktree mutation\n", encoding="utf-8")
            self._git(root, "add", "services/app.py")
            actual = build_canonical_product_snapshot(
                root, commit_sha, ("services",)
            )
        self.assertEqual(actual, expected)

    def test_file_mode_is_part_of_snapshot_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._init_repo(root)
            path = root / "services/executable.sh"
            path.parent.mkdir(parents=True)
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            self._git(root, "add", "services/executable.sh")
            self._git(root, "commit", "-qm", "initial")
            commit_sha = self._git(root, "rev-parse", "HEAD")
            executable_snapshot = build_canonical_product_snapshot(
                root, commit_sha, ("services",)
            )
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            self._git(root, "add", "services/executable.sh")
            self._git(root, "commit", "-qm", "mode-change")
            non_executable_snapshot = build_canonical_product_snapshot(
                root, self._git(root, "rev-parse", "HEAD"), ("services",)
            )
        self.assertEqual(executable_snapshot["entries"][0]["mode"], "100755")
        self.assertEqual(non_executable_snapshot["entries"][0]["mode"], "100644")
        self.assertEqual(
            executable_snapshot["entries"][0]["digest"],
            non_executable_snapshot["entries"][0]["digest"],
        )
        self.assertNotEqual(
            executable_snapshot["protected_snapshot_digest"],
            non_executable_snapshot["protected_snapshot_digest"],
        )
        self.assertEqual(validate_v3_product_snapshot(executable_snapshot), [])
        self.assertEqual(validate_v3_product_snapshot(non_executable_snapshot), [])

    def test_symlink_is_rejected_from_git_object_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._init_repo(root)
            target = root / "services/target.txt"
            target.parent.mkdir(parents=True)
            target.write_text("target\n", encoding="utf-8")
            (root / "services/link.txt").symlink_to("target.txt")
            self._git(root, "add", "services")
            self._git(root, "commit", "-qm", "symlink")
            commit_sha = self._git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(
                ProductSourcePolicyError, "unsupported_git_tree_mode"
            ):
                build_canonical_product_snapshot(root, commit_sha, ("services",))

    def test_gitlink_is_rejected_from_git_object_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "parent"
            nested = Path(temporary) / "nested"
            parent.mkdir()
            nested.mkdir()
            self._init_repo(nested)
            nested_file = nested / "README.md"
            nested_file.write_text("nested\n", encoding="utf-8")
            self._git(nested, "add", "README.md")
            self._git(nested, "commit", "-qm", "nested")
            nested_sha = self._git(nested, "rev-parse", "HEAD")

            self._init_repo(parent)
            (parent / "services").mkdir()
            self._git(
                parent,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{nested_sha},services/submodule",
            )
            tree_sha = self._git(parent, "write-tree")
            commit_sha = subprocess.check_output(
                ["git", "-C", str(parent), "commit-tree", tree_sha, "-m", "gitlink"],
                text=True,
            ).strip()
            with self.assertRaisesRegex(
                ProductSourcePolicyError, "unsupported_git_tree_mode"
            ):
                build_canonical_product_snapshot(parent, commit_sha, ("services",))


if __name__ == "__main__":
    unittest.main()
