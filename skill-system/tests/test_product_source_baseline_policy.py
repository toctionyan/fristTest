from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system/controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from product_source_baseline_policy import (  # type: ignore
    BaselineMode,
    ProductSourcePolicyError,
    SnapshotSource,
    evaluate_binding,
    file_sha256,
    load_baseline_document,
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


if __name__ == "__main__":
    unittest.main()
