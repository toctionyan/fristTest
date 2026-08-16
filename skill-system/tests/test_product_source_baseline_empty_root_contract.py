from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PATH = ROOT / "scripts/github_repair_baseline_acceptance.py"
VERIFIER_PATH = ROOT / "scripts/verify_product_source_baseline.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ProductSourceBaselineEmptyRootContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.acceptance = _load("baseline_empty_root_acceptance", ACCEPTANCE_PATH)
        cls.verifier = _load("baseline_empty_root_verifier", VERIFIER_PATH)

    @staticmethod
    def _workspace() -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "services/example.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return temporary, root, digest

    def test_verifier_allows_missing_root_with_zero_recorded_files(self) -> None:
        temporary, root, digest = self._workspace()
        with temporary:
            baseline_path = root / self.verifier.BASELINE_PATH
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "protected_roots": ["services", "web"],
                        "file_count": 1,
                        "files": {"services/example.py": digest},
                        "generated_from": "git:unused-without-parent-binding",
                    }
                ),
                encoding="utf-8",
            )

            result = self.verifier.verify(root, require_parent_binding=False)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["current_file_count"], 1)
        self.assertFalse(result["production_closed"])

    def test_verifier_rejects_missing_root_with_recorded_file(self) -> None:
        temporary, root, digest = self._workspace()
        with temporary:
            baseline_path = root / self.verifier.BASELINE_PATH
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "protected_roots": ["services", "web"],
                        "file_count": 2,
                        "files": {
                            "services/example.py": digest,
                            "web/index.html": "0" * 64,
                        },
                        "generated_from": "git:unused-without-parent-binding",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                self.verifier.BaselineVerificationError,
                r"protected root is missing: web",
            ):
                self.verifier.verify(root, require_parent_binding=False)

    def test_acceptance_scanner_allows_missing_root_with_zero_recorded_files(self) -> None:
        temporary, root, digest = self._workspace()
        with temporary:
            recorded = {"services/example.py": digest}
            baseline = {
                "protected_roots": ["services", "web"],
                "files": recorded,
            }

            current = self.acceptance._current_protected_files(
                root,
                baseline,
                recorded,
            )

        self.assertEqual(current, recorded)

    def test_acceptance_scanner_rejects_missing_root_with_recorded_file(self) -> None:
        temporary, root, digest = self._workspace()
        with temporary:
            recorded = {
                "services/example.py": digest,
                "web/index.html": "0" * 64,
            }
            baseline = {
                "protected_roots": ["services", "web"],
                "files": recorded,
            }

            with self.assertRaisesRegex(
                self.acceptance.BaselineAcceptanceError,
                r"protected root is missing: web",
            ):
                self.acceptance._current_protected_files(
                    root,
                    baseline,
                    recorded,
                )


if __name__ == "__main__":
    unittest.main()
