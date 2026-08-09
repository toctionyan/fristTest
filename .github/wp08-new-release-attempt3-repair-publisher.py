#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: {count} for {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Certification must exercise the same bounded declaration-repair behavior
# as production instead of failing a stochastic first draft before the
# production independent alignment/granularity validators can reject it.
smoke = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
replace_once(
    smoke,
    "from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402\n",
    "from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage  # noqa: E402\n",
)
helper = r'''

def _declare_with_bounded_production_repair(
    *,
    case_id: str,
    user_text: str,
    bound: Any,
    system: SystemMessage,
    identity: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], int]:
    """Return the first declaration that production validators can freeze.

    The repair message contains no oracle count, expected effect identity or
    expected span. It mirrors the production rule: a rejected declaration is
    not frozen and the model must re-read the same user turn, preserving every
    independently completable effect, including open effects with no exact
    registered capability.
    """
    messages: list[Any] = [system, HumanMessage(content=user_text)]
    last_error: Exception | None = None
    for attempt in range(1, 3):
        response, trace = invoke_model(
            purpose=f"preprod_semantic_goal:{case_id}:attempt{attempt}",
            model=bound,
            payload=messages,
        )
        attestation = attest_real_model_metadata(response=response, identity=identity)
        candidates = tool_calls(response)
        if len(candidates) != 1 or str(candidates[0].get("name") or "") != "declare_turn_goals":
            raise RuntimeError(f"{case_id}: model did not emit exactly one declare_turn_goals call")
        call = candidates[0]
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        goals = [row for row in list(args.get("goals") or []) if isinstance(row, dict)]
        try:
            declared = _validate_with_production_goal_contract(
                case_id=case_id,
                user_text=user_text,
                goals=goals,
            )
            return goals, declared, {"trace": trace, "attestation": attestation}, attempt
        except RuntimeError as exc:
            last_error = exc
            if attempt >= 2:
                break
            tool_call_id = str(call.get("id") or f"{case_id}:declare:{attempt}")
            messages = [
                system,
                HumanMessage(content=user_text),
                response,
                ToolMessage(
                    tool_call_id=tool_call_id,
                    content=json.dumps(
                        {
                            "ok": False,
                            "code": "GOAL_DECLARATION_RETRY_REQUIRED",
                            "message": (
                                "Runtime 未冻结当前候选。重新逐段检查同一用户原话中的每一个可独立完成业务效果；"
                                "不能删除系统没有精确能力的分支，也不能把它改写为相近能力。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
    raise RuntimeError(f"{case_id}: bounded production declaration repair exhausted: {last_error}")
'''
replace_once(
    smoke,
    "\ndef _identity_failure_reason(exc: RealModelCertificationError) -> str:\n",
    helper + "\ndef _identity_failure_reason(exc: RealModelCertificationError) -> str:\n",
)
replace_once(
    smoke,
    "        # Protected mode uses the production independent goal-alignment model,\n        # so each prototype may consume one declaration call and one verifier call.\n        with model_call_scope(max_calls=24, scope=\"preprod_semantic_goal_prototypes\") as calls:\n",
    "        # Each prototype may consume one declaration plus an independent validator,\n        # and at most one bounded declaration repair with the same validator path.\n        with model_call_scope(max_calls=48, scope=\"preprod_semantic_goal_prototypes\") as calls:\n",
)
old_loop = r'''                response, trace = invoke_model(
                    purpose=f"preprod_semantic_goal:{case['id']}",
                    model=bound,
                    payload=[system, HumanMessage(content=str(turn["user_text"]))],
                )
                attestation = attest_real_model_metadata(
                    response=response,
                    identity=identity,
                )
                candidates = tool_calls(response)
                if len(candidates) != 1 or str(candidates[0].get("name") or "") != "declare_turn_goals":
                    raise RuntimeError(f"{case['id']}: model did not emit exactly one declare_turn_goals call")
                args = candidates[0].get("args") if isinstance(candidates[0].get("args"), dict) else {}
                goals = [row for row in list(args.get("goals") or []) if isinstance(row, dict)]
                oracle = [row for row in list(turn.get("goal_oracle") or []) if isinstance(row, dict)]
                _match_oracle(
                    case_id=case["id"],
                    oracle=oracle,
                    goals=goals,
                    registered_effect_identities=registered_effect_identities,
                )
                declared = _validate_with_production_goal_contract(
                    case_id=str(case["id"]),
                    user_text=str(turn["user_text"]),
                    goals=goals,
                )
'''
new_loop = r'''                goals, declared, declaration_evidence, declaration_attempts = _declare_with_bounded_production_repair(
                    case_id=str(case["id"]),
                    user_text=str(turn["user_text"]),
                    bound=bound,
                    system=system,
                    identity=identity,
                )
                oracle = [row for row in list(turn.get("goal_oracle") or []) if isinstance(row, dict)]
                _match_oracle(
                    case_id=case["id"],
                    oracle=oracle,
                    goals=goals,
                    registered_effect_identities=registered_effect_identities,
                )
                trace = declaration_evidence["trace"]
                attestation = declaration_evidence["attestation"]
'''
replace_once(smoke, old_loop, new_loop)
replace_once(
    smoke,
    '                    "goal_count": len(goals),\n',
    '                    "goal_count": len(goals),\n                    "declaration_attempts": declaration_attempts,\n',
)


# 2) A single provider invocation must not be able to consume the complete
# user-facing response window through retries. Defaults now leave bounded room
# for orchestration; production/preprod rejects unsafe override envelopes.
config = ROOT / "services/agent-service/src/agent_core/config.py"
replace_once(
    config,
    '        "timeout_seconds": _bounded_float_env("MODEL_TIMEOUT_SECONDS", 60.0, minimum=1.0, maximum=600.0),\n        "max_retries": _bounded_int_env("MODEL_MAX_RETRIES", 2, minimum=0, maximum=10),\n',
    '        "timeout_seconds": _bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0, minimum=1.0, maximum=600.0),\n        "max_retries": _bounded_int_env("MODEL_MAX_RETRIES", 1, minimum=0, maximum=10),\n',
)
replace_once(
    config,
    '    errors: list[str] = []\n    if not _truthy(os.getenv("AGENT_REQUIRE_AUTH", "false")):\n',
    '    errors: list[str] = []\n    model_settings = get_model_settings()\n    provider_retry_envelope = float(model_settings["timeout_seconds"]) * (int(model_settings["max_retries"]) + 1)\n    if provider_retry_envelope > 60.0:\n        errors.append(\n            "MODEL_TIMEOUT_SECONDS * (MODEL_MAX_RETRIES + 1) must be <= 60 seconds in preprod/production"\n        )\n    if not _truthy(os.getenv("AGENT_REQUIRE_AUTH", "false")):\n',
)

env_example = ROOT / "services/agent-service/.env.example"
replace_once(
    env_example,
    "MODEL_TIMEOUT_SECONDS=60\nMODEL_MAX_RETRIES=2\n",
    "MODEL_TIMEOUT_SECONDS=25\nMODEL_MAX_RETRIES=1\n",
)


# 3) The prior safe start/finish events were emitted through a module logger
# that is not attached to the protected uvicorn service log. Prefer uvicorn's
# configured error logger when present, while retaining the generic logger for
# CLI/test contexts. Payload remains strictly allow-listed.
gateway = ROOT / "services/agent-service/src/agent_core/model_calls/gateway.py"
replace_once(
    gateway,
    "        LOGGER.info(\n            'model_call_event %s',\n            json.dumps({'event': str(event), **safe}, ensure_ascii=False, sort_keys=True),\n        )\n",
    "        service_logger = logging.getLogger('uvicorn.error')\n        sink = service_logger if service_logger.handlers else LOGGER\n        sink.info(\n            'model_call_event %s',\n            json.dumps({'event': str(event), **safe}, ensure_ascii=False, sort_keys=True),\n        )\n",
)


# 4) Focused governance regressions.
test_file = ROOT / "skill-system/tests/test_wp08_new_release_attempt3_repair.py"
test_file.write_text(r'''from __future__ import annotations

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
        self.assertIn('model_call_scope(max_calls=48', source)

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
''', encoding="utf-8")

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(smoke.relative_to(ROOT)),
        str(config.relative_to(ROOT)),
        str(env_example.relative_to(ROOT)),
        str(gateway.relative_to(ROOT)),
        str(test_file.relative_to(ROOT)),
    ],
}, ensure_ascii=False, indent=2))
