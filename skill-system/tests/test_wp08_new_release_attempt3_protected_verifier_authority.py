from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services/agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for path in (ROOT / "scripts", AGENT_ROOT, AGENT_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agent_core.lifecycle import goal_granularity  # noqa: E402
from agent_core.model_calls import real_model_certification_bundle as bundle  # noqa: E402


def _load_semantic_smoke():
    path = AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("wp08_attempt3_semantic_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ProtectedVerifierAuthorityTests(unittest.TestCase):
    def test_bundle_forces_protected_profile_and_all_independent_verifiers(self) -> None:
        captured: dict[str, str] = {}

        def runner(*, component, script_path, env, workspace_root):
            del script_path, workspace_root
            captured.update({str(k): str(v) for k, v in env.items()})
            return {
                "status": "BLOCKED_BY_ENVIRONMENT",
                "reason": "test_stop_after_environment_capture",
                "error_code": "test_stop",
            }

        identity = {
            "provider": "deepseek",
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "credential_fingerprint_sha256_16": "a" * 16,
            "official_endpoint": True,
            "https": True,
        }
        with patch.object(bundle, "resolve_real_model_identity", return_value=identity), patch.object(
            bundle, "_source_workspace_fingerprint", return_value="b" * 64
        ):
            result = bundle.run_certification_bundle(
                workspace_root=ROOT,
                env={"OPENAI_API_KEY": "test-only"},
                component_runner=runner,
            )
        self.assertEqual(result["status"], "BLOCKED_BY_ENVIRONMENT")
        self.assertEqual(captured["APP_PROFILE"], "preprod")
        for name in (
            "CAPABILITY_SEMANTIC_VERIFIER_MODE",
            "GOAL_ALIGNMENT_VERIFIER_MODE",
            "GOAL_GRANULARITY_VERIFIER_MODE",
            "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE",
        ):
            self.assertEqual(captured[name], "model")

    def test_semantic_smoke_fails_closed_without_model_granularity_authority(self) -> None:
        smoke = _load_semantic_smoke()
        env = {
            "APP_PROFILE": "local",
            "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
            "GOAL_GRANULARITY_VERIFIER_MODE": "candidate",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(RuntimeError, "protected model verifier authority"):
                smoke._semantic_verifier_authority()

    def test_semantic_smoke_accepts_only_model_alignment_and_granularity(self) -> None:
        smoke = _load_semantic_smoke()
        env = {
            "APP_PROFILE": "preprod",
            "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
            "GOAL_GRANULARITY_VERIFIER_MODE": "model",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(smoke._semantic_verifier_authority(), {
                "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
                "GOAL_GRANULARITY_VERIFIER_MODE": "model",
            })
            self.assertEqual(goal_granularity._goal_granularity_mode(), "model")

    def test_all_protected_browser_authority_evidence_includes_granularity(self) -> None:
        harness_source = (ROOT / "scripts/verify_full_lifecycle_canary.py").read_text(encoding="utf-8")
        browser_source = (ROOT / "scripts/verify_production_browser_bundle.py").read_text(encoding="utf-8")
        lifecycle_source = (AGENT_ROOT / "scripts/verify_preprod_full_lifecycle.py").read_text(encoding="utf-8")
        marker = '"GOAL_GRANULARITY_VERIFIER_MODE": "model"'
        self.assertIn(marker, harness_source)
        self.assertIn(marker, browser_source)
        self.assertIn(marker, lifecycle_source)

    def test_attempt3_failure_path_was_vulnerable_to_candidate_fallback_before_repair(self) -> None:
        # The root cause is configuration authority, not another keyword rule:
        # auto outside a protected profile resolves to candidate-only.
        with patch.dict(os.environ, {
            "APP_PROFILE": "",
            "GOAL_GRANULARITY_VERIFIER_MODE": "auto",
        }, clear=False):
            self.assertEqual(goal_granularity._goal_granularity_mode(), "candidate")

    def test_semantic_budget_covers_worst_case_bounded_repair(self) -> None:
        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn('model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")', source)
        self.assertIn('"verifier_authority": verifier_authority', source)

    def test_deepseek_side_candidate_is_composed_without_raising_outer_slas(self) -> None:
        config = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
        browser = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn('extra_body={"thinking": {"type": "disabled"}} if deepseek_v4 else None', config)
        self.assertIn('_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0', config)
        self.assertIn('_bounded_int_env("MODEL_MAX_RETRIES", 1', config)
        self.assertIn('{ timeout: 120_000 }', browser)


if __name__ == "__main__":
    unittest.main()
