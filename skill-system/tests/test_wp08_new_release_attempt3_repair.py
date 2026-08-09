from __future__ import annotations

import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services/agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for path in (AGENT_ROOT, AGENT_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class Attempt3RepairTests(unittest.TestCase):
    def test_model_defaults_leave_retry_headroom(self) -> None:
        from agent_core.config import get_model_settings
        with patch.dict(os.environ, {}, clear=True):
            settings = get_model_settings()
        self.assertEqual(settings["timeout_seconds"], 25.0)
        self.assertEqual(settings["max_retries"], 1)
        self.assertLessEqual(float(settings["timeout_seconds"]) * (int(settings["max_retries"]) + 1), 60.0)

    def test_unsafe_production_provider_retry_envelope_is_rejected(self) -> None:
        source = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
        self.assertIn('provider_retry_envelope > 60.0', source)
        self.assertIn('MODEL_TIMEOUT_SECONDS * (MODEL_MAX_RETRIES + 1) must be <= 60 seconds', source)

    def test_semantic_certification_retries_only_after_production_rejection(self) -> None:
        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        helper_start = source.index("def _declare_with_bounded_production_repair")
        helper_end = source.index("def _identity_failure_reason", helper_start)
        helper = source[helper_start:helper_end]
        self.assertIn("for attempt in range(1, 3)", helper)
        self.assertIn("_validate_with_production_goal_contract", helper)
        self.assertIn("except RuntimeError", helper)
        self.assertNotIn("goal_oracle", helper)
        self.assertNotIn("_match_oracle", helper)
        self.assertIn("不能删除系统没有精确能力的分支", helper)
        # Protected certification now executes declaration + independent alignment
        # + independent candidate-blind granularity, with at most one repair attempt.
        self.assertIn('model_call_scope(max_calls=72', source)
        self.assertIn('"GOAL_GRANULARITY_VERIFIER_MODE"', source)

    def test_independent_oracle_still_runs_after_production_freeze(self) -> None:
        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        declaration = source.index("goals, declared, declaration_evidence, declaration_attempts")
        oracle = source.index("_match_oracle(", declaration)
        self.assertLess(declaration, oracle)
        self.assertIn('candidate_effect = _effect_identity(row.get("requested_effect"))', source)
        self.assertIn('_effect_identity(row.get("requested_effect")) == expected_effect', source)

    def test_safe_model_call_events_use_service_logger_when_available(self) -> None:
        from agent_core.model_calls import gateway
        records: list[str] = []
        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())
        logger = logging.getLogger("uvicorn.error")
        handler = Handler()
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            gateway._emit_model_call_log("started", {
                "purpose": "agent_loop",
                "model": "safe-model",
                "sequence": 1,
                "scope": "request",
                "lane": "planner",
                "secret": "must-not-leak",
            })
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        self.assertTrue(any("model_call_event" in row for row in records))
        joined = "\n".join(records)
        self.assertNotIn("must-not-leak", joined)
        self.assertNotIn("secret", joined)

    def test_browser_response_gate_remains_120_seconds(self) -> None:
        source = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn('{ timeout: 120_000 }', source)


if __name__ == "__main__":
    unittest.main()
