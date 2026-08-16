from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRUSTED_JUDGE = ROOT / "skill-system" / "controller" / "trusted_judge.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("trusted_judge_exact_surface_test", TRUSTED_JUDGE)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load trusted_judge")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seed_minimal_root(root: Path) -> None:
    source = ROOT / "scripts" / "quality_loop.py"
    target = root / "scripts" / "quality_loop.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


class TrustedJudgeExactSurfaceTests(unittest.TestCase):
    def test_candidate_added_trusted_input_is_rejected_even_when_manifest_hashes_match(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="trusted-judge-exact-candidate-") as temp:
            base = Path(temp)
            judge = base / "judge"
            candidate = base / "candidate"
            _seed_minimal_root(judge)
            _seed_minimal_root(candidate)
            module.write_manifest(judge)
            self.assertEqual(module.verify_root(judge), [])
            self.assertEqual(module.verify_candidate(candidate, judge), [])

            extra = candidate / "scripts" / "verify_candidate_owned_judge.py"
            extra.write_text("# candidate-owned Judge input\n", encoding="utf-8")
            errors = module.verify_candidate(candidate, judge)
            self.assertIn(
                "candidate_extra_trusted_input:scripts/verify_candidate_owned_judge.py",
                errors,
            )

    def test_judge_manifest_is_stale_when_new_trusted_input_is_unmanifested(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="trusted-judge-exact-root-") as temp:
            judge = Path(temp) / "judge"
            _seed_minimal_root(judge)
            module.write_manifest(judge)
            self.assertEqual(module.verify_root(judge), [])

            extra = judge / "scripts" / "verify_new_judge_rule.py"
            extra.write_text("# new Judge input\n", encoding="utf-8")
            errors = module.verify_root(judge)
            self.assertIn(
                "unmanifested_trusted_input:scripts/verify_new_judge_rule.py",
                errors,
            )

    def test_candidate_comparison_refuses_stale_judge_manifest(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="trusted-judge-stale-root-") as temp:
            base = Path(temp)
            judge = base / "judge"
            candidate = base / "candidate"
            _seed_minimal_root(judge)
            _seed_minimal_root(candidate)
            module.write_manifest(judge)
            (judge / "scripts" / "quality_loop.py").write_text(
                "# changed after manifest\n",
                encoding="utf-8",
            )
            errors = module.verify_candidate(candidate, judge)
            self.assertTrue(
                any(item.startswith("judge_trust_root_invalid:fingerprint_mismatch:scripts/quality_loop.py") for item in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
