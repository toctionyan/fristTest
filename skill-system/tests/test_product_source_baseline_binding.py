from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system/controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from product_source_baseline_policy import (  # type: ignore
    BASELINE_PATH,
    BaselineMode,
    ProductSourcePolicyError,
    build_canonical_product_snapshot,
    baseline_mode_for_authority,
    load_baseline_document,
    validate_baseline_document,
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ProductSourceBaselineBindingTests(unittest.TestCase):
    def test_registry_is_the_v3_accepted_authority(self) -> None:
        document = load_baseline_document(ROOT)
        rebuilt = build_canonical_product_snapshot(
            ROOT,
            document.product_source_ref.removeprefix("git-commit-sha1:"),
            document.protected_roots,
        )
        self.assertEqual(rebuilt, document.payload)
        self.assertEqual(validate_baseline_document(document.payload), [])

    def test_candidate_mode_reports_drift_without_promotion(self) -> None:
        document = load_baseline_document(ROOT)
        self.assertEqual(
            baseline_mode_for_authority(
                "historical-registry-baseline",
                event_name="pull_request",
            ),
            BaselineMode.PR_CANDIDATE,
        )
        self.assertTrue(document.protected_snapshot_digest.startswith("sha256:"))

    def test_v2_registry_is_rejected_before_binding(self) -> None:
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

    def test_offline_accepted_identity_fails_closed(self) -> None:
        verifier = _load(
            "offline_baseline_verifier",
            ROOT / "scripts/verify_product_source_baseline.py",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / BASELINE_PATH
            path.parent.mkdir(parents=True)
            path.write_text(
                (ROOT / BASELINE_PATH).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            with self.assertRaises(verifier.BaselineVerificationError):
                verifier.verify(root)

    def test_baseline_does_not_claim_production_closure(self) -> None:
        task_ledger = json.loads(
            (ROOT / "governance/task-ledger.json").read_text(encoding="utf-8")
        )
        self.assertNotIn('"production_closed": true', json.dumps(task_ledger, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
