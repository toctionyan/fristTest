from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PATH = ROOT / "scripts/github_repair_baseline_acceptance.py"
VERIFIER_PATH = ROOT / "scripts/verify_product_source_baseline.py"
CONTROL = ROOT / "skill-system/controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from product_source_baseline_policy import build_canonical_product_snapshot  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ProductSourceBaselineGitObjectContractTests(unittest.TestCase):
    def test_verifier_rejects_registry_without_git_objects(self) -> None:
        verifier = _load("v3_empty_root_verifier", VERIFIER_PATH)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / verifier.BASELINE_PATH
            path.parent.mkdir(parents=True)
            path.write_text(
                (ROOT / verifier.BASELINE_PATH).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            with self.assertRaises(verifier.BaselineVerificationError):
                verifier.verify(root)

    def test_v3_snapshot_allows_an_empty_protected_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
            (root / "README.md").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
            sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            snapshot = build_canonical_product_snapshot(root, sha, ("services", "web"))
        self.assertEqual(snapshot["entry_count"], 0)
        self.assertEqual(snapshot["entries"], {})


if __name__ == "__main__":
    unittest.main()
