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
    BASELINE_PATH,
    BaselineMode,
    ProductSourcePolicyError,
    baseline_mode_for_authority,
    build_canonical_product_snapshot,
    evaluate_product_source,
    file_sha256,
    load_baseline_document,
    validate_baseline_document,
    validate_v3_product_snapshot,
)


class ProductSourceBaselinePolicyTests(unittest.TestCase):
    STAGE2B1_SHA = "6c41cb862ba065e474aa3a7f213209d1eacfef45"
    STAGE2B1_MERGE_SHA = "4c80d7b79f395bd1d93478043ba1ed25688c8547"
    CURRENT_SHA = "31d17e7c295849339a0d544d8347be6f92f3515a"
    PROTECTED_ROOTS = ("contracts", "services", "web")

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    @classmethod
    def _init_repo(cls, root: Path) -> None:
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        cls._git(root, "config", "user.name", "Snapshot Test")
        cls._git(root, "config", "user.email", "snapshot@example.com")

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        for commit_sha in (cls.STAGE2B1_SHA, cls.STAGE2B1_MERGE_SHA, cls.CURRENT_SHA):
            present = subprocess.run(
                ["git", "-C", str(ROOT), "cat-file", "-e", f"{commit_sha}^{{commit}}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if present.returncode != 0:
                subprocess.run(
                    ["git", "-C", str(ROOT), "fetch", "--no-tags", "origin", commit_sha],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

    def test_v3_registry_self_validates(self) -> None:
        document = load_baseline_document(ROOT)
        self.assertEqual(validate_baseline_document(document.payload), [])
        self.assertEqual(document.payload["schema_version"], 3)
        self.assertEqual(document.payload["snapshot_format"], "protected-git-tree@1")
        self.assertIsInstance(document.payload["entries"], dict)

    def test_product_source_and_control_plane_snapshot_are_identical(self) -> None:
        source = build_canonical_product_snapshot(ROOT, self.STAGE2B1_SHA, self.PROTECTED_ROOTS)
        current = build_canonical_product_snapshot(ROOT, self.CURRENT_SHA, self.PROTECTED_ROOTS)
        self.assertEqual(source["protected_snapshot_digest"], current["protected_snapshot_digest"])
        self.assertEqual(source["entries"], current["entries"])
        self.assertEqual(
            build_canonical_product_snapshot(ROOT, self.STAGE2B1_MERGE_SHA, self.PROTECTED_ROOTS)[
                "protected_snapshot_digest"
            ],
            source["protected_snapshot_digest"],
        )

    def test_tampered_entries_break_v3_preflight(self) -> None:
        snapshot = build_canonical_product_snapshot(ROOT, self.STAGE2B1_SHA, self.PROTECTED_ROOTS)
        path = next(iter(snapshot["entries"]))
        snapshot["entries"] = dict(snapshot["entries"])
        snapshot["entries"][path] = dict(snapshot["entries"][path])
        snapshot["entries"][path]["digest"] = "sha256:" + "0" * 64
        self.assertIn("v3_protected_snapshot_digest_mismatch", validate_v3_product_snapshot(snapshot))

    def test_v3_rejects_unknown_fields_paths_and_counts(self) -> None:
        snapshot = build_canonical_product_snapshot(ROOT, self.STAGE2B1_SHA, self.PROTECTED_ROOTS)
        path = next(iter(snapshot["entries"]))
        snapshot["unexpected"] = True
        snapshot["entry_count"] += 1
        snapshot["entries"] = dict(snapshot["entries"])
        record = snapshot["entries"].pop(path)
        snapshot["entries"]["../outside"] = record
        errors = validate_v3_product_snapshot(snapshot)
        self.assertIn("v3_unknown_field:unexpected", errors)
        self.assertIn("v3_entry_path_invalid:../outside", errors)
        self.assertIn("v3_entry_count_mismatch", errors)

    def test_v2_is_fail_closed(self) -> None:
        payload = {
            "schema_version": 2,
            "protected_roots": ["services"],
            "file_count": 0,
            "files": {},
            "generated_from": "git:" + "0" * 40,
        }
        self.assertIn("baseline_schema_invalid:v3_required", validate_baseline_document(payload))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / BASELINE_PATH
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ProductSourcePolicyError):
                load_baseline_document(root)

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
            expected = build_canonical_product_snapshot(root, commit_sha, ("services",))
            path.write_text("worktree mutation\n", encoding="utf-8")
            self._git(root, "add", "services/app.py")
            self.assertEqual(build_canonical_product_snapshot(root, commit_sha, ("services",)), expected)

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
            executable = build_canonical_product_snapshot(root, self._git(root, "rev-parse", "HEAD"), ("services",))
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            self._git(root, "add", "services/executable.sh")
            self._git(root, "commit", "-qm", "mode-change")
            plain = build_canonical_product_snapshot(root, self._git(root, "rev-parse", "HEAD"), ("services",))
        path_name = "services/executable.sh"
        self.assertEqual(executable["entries"][path_name]["mode"], "100755")
        self.assertEqual(plain["entries"][path_name]["mode"], "100644")
        self.assertNotEqual(executable["protected_snapshot_digest"], plain["protected_snapshot_digest"])

    def test_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._init_repo(root)
            target = root / "services/target.txt"
            target.parent.mkdir(parents=True)
            target.write_text("target\n", encoding="utf-8")
            (root / "services/link.txt").symlink_to("target.txt")
            self._git(root, "add", "services")
            self._git(root, "commit", "-qm", "symlink")
            with self.assertRaisesRegex(ProductSourcePolicyError, "unsupported_git_tree_mode"):
                build_canonical_product_snapshot(root, self._git(root, "rev-parse", "HEAD"), ("services",))

    def test_gitlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "parent"
            nested = Path(temporary) / "nested"
            parent.mkdir()
            nested.mkdir()
            self._init_repo(nested)
            (nested / "README.md").write_text("nested\n", encoding="utf-8")
            self._git(nested, "add", "README.md")
            self._git(nested, "commit", "-qm", "nested")
            nested_sha = self._git(nested, "rev-parse", "HEAD")
            self._init_repo(parent)
            (parent / "services").mkdir()
            self._git(parent, "update-index", "--add", "--cacheinfo", f"160000,{nested_sha},services/submodule")
            tree_sha = self._git(parent, "write-tree")
            commit_sha = subprocess.check_output(
                ["git", "-C", str(parent), "commit-tree", tree_sha, "-m", "gitlink"], text=True
            ).strip()
            with self.assertRaisesRegex(ProductSourcePolicyError, "unsupported_git_tree_mode"):
                build_canonical_product_snapshot(parent, commit_sha, ("services",))

    def test_missing_git_object_fails_closed(self) -> None:
        with self.assertRaises(ProductSourcePolicyError):
            build_canonical_product_snapshot(ROOT, "0" * 40, self.PROTECTED_ROOTS)

    def test_candidate_drift_reports_without_promoting_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._init_repo(root)
            source = root / "services/app.py"
            source.parent.mkdir(parents=True)
            source.write_text("one\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-qm", "accepted")
            accepted = self._git(root, "rev-parse", "HEAD")
            source.write_text("two\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-qm", "candidate")
            candidate = self._git(root, "rev-parse", "HEAD")
            registry = root / BASELINE_PATH
            registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps(build_canonical_product_snapshot(root, accepted, ("services",))), encoding="utf-8")
            result = evaluate_product_source(root, event_name="pull_request")
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["baseline_mode"], BaselineMode.PR_CANDIDATE.value)
            self.assertEqual(result["current_commit_sha"], candidate)
            self.assertTrue(result["drift_paths"])

    def test_default_branch_authority_is_strict(self) -> None:
        result = evaluate_product_source(ROOT, event_name="push")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["baseline_mode"], BaselineMode.ACCEPTED_REF.value)

    def test_feature_branch_push_is_candidate(self) -> None:
        mode = baseline_mode_for_authority(
            "historical-registry-baseline",
            event_name="push",
            ref_type="branch",
            ref_name="repair/example",
            default_branch="main",
        )
        self.assertEqual(mode, BaselineMode.PR_CANDIDATE)

    def test_single_policy_authority_consumers(self) -> None:
        for path in (
            ROOT / "skill-system/controller/project_compatibility.py",
            ROOT / "scripts/verify_product_source_baseline.py",
            ROOT / "scripts/github_repair_baseline_acceptance.py",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertIn("product_source_baseline_policy", source, path.as_posix())
            self.assertNotIn('"ls-files"', source, path.as_posix())


if __name__ == "__main__":
    unittest.main()
