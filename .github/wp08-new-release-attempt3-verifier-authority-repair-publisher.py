#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import json

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:160]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


bundle = ROOT / "services/agent-service/src/agent_core/model_calls/real_model_certification_bundle.py"
replace_once(
    bundle,
    '''_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{15,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
''',
    '''_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{15,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROTECTED_VERIFIER_MODES = {
    "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
    "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
    "GOAL_GRANULARITY_VERIFIER_MODE": "model",
    "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
}
''',
)
replace_once(
    bundle,
    '''        component_env = dict(source)
        component_env.update({
            _SESSION_ENV: session_id,
            _WORKSPACE_ENV: fingerprint,
            _STARTED_ENV: _iso(started_at),
            _COMPONENT_ENV: component,
        })
''',
    '''        component_env = dict(source)
        component_env.update({
            # Real-model certification must exercise the same independent-verifier
            # authority as protected Runtime.  Without an explicit protected profile,
            # resolve_verifier_mode(auto) degrades to candidate-only local evidence.
            "APP_PROFILE": "preprod",
            **_PROTECTED_VERIFIER_MODES,
            _SESSION_ENV: session_id,
            _WORKSPACE_ENV: fingerprint,
            _STARTED_ENV: _iso(started_at),
            _COMPONENT_ENV: component,
        })
''',
)

semantic = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
replace_once(
    semantic,
    '''from agent_core.runtime.node_support import tool_calls  # noqa: E402
from agent_core.composition import get_runtime_registry  # noqa: E402
''',
    '''from agent_core.runtime.node_support import tool_calls  # noqa: E402
from agent_core.runtime.profile import resolve_verifier_mode  # noqa: E402
from agent_core.composition import get_runtime_registry  # noqa: E402
''',
)
replace_once(
    semantic,
    '''def main() -> int:
    try:
        identity = resolve_real_model_identity()
''',
    '''def _semantic_verifier_authority() -> dict[str, str]:
    modes = {
        name: resolve_verifier_mode(name)
        for name in (
            "GOAL_ALIGNMENT_VERIFIER_MODE",
            "GOAL_GRANULARITY_VERIFIER_MODE",
        )
    }
    invalid = {name: value for name, value in modes.items() if value != "model"}
    if invalid:
        raise RuntimeError(
            "semantic certification requires protected model verifier authority: "
            + json.dumps(invalid, sort_keys=True)
        )
    return modes


def main() -> int:
    try:
        identity = resolve_real_model_identity()
        verifier_authority = _semantic_verifier_authority()
''',
)
replace_once(
    semantic,
    '''        # Each prototype may consume one declaration plus an independent validator,
        # and at most one bounded declaration repair with the same validator path.
        with model_call_scope(max_calls=48, scope="preprod_semantic_goal_prototypes") as calls:
''',
    '''        # Each accepted declaration is checked by both independent model validators
        # (alignment + candidate-blind granularity). A rejected declaration may be repaired
        # once through the exact same protected path, so the worst-case bounded envelope is
        # 12 prototypes * 2 declaration attempts * 3 model calls = 72.
        with model_call_scope(max_calls=72, scope="preprod_semantic_goal_prototypes") as calls:
''',
)
replace_once(
    semantic,
    '''            "model_profile": get_model_profile(),
            "calls": calls.summary(),
''',
    '''            "model_profile": get_model_profile(),
            "verifier_authority": verifier_authority,
            "calls": calls.summary(),
''',
)

lifecycle = ROOT / "services/agent-service/scripts/verify_preprod_full_lifecycle.py"
replace_once(
    lifecycle,
    '''        harness.env.update({
            "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
            "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
            "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
        })
''',
    '''        harness.env.update({
            "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
            "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
            "GOAL_GRANULARITY_VERIFIER_MODE": "model",
            "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
        })
''',
)

harness = ROOT / "scripts/verify_full_lifecycle_canary.py"
replace_once(
    harness,
    '''                "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
                "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
                "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
''',
    '''                "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
                "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
                "GOAL_GRANULARITY_VERIFIER_MODE": "model",
                "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
''',
)
replace_once(
    harness,
    '''                "CAPABILITY_SEMANTIC_VERIFIER_MODE",
                "GOAL_ALIGNMENT_VERIFIER_MODE",
                "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE",
''',
    '''                "CAPABILITY_SEMANTIC_VERIFIER_MODE",
                "GOAL_ALIGNMENT_VERIFIER_MODE",
                "GOAL_GRANULARITY_VERIFIER_MODE",
                "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE",
''',
)

browser = ROOT / "scripts/verify_production_browser_bundle.py"
replace_once(
    browser,
    '''        "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
        "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
        "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
''',
    '''        "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
        "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
        "GOAL_GRANULARITY_VERIFIER_MODE": "model",
        "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
''',
)

regression = ROOT / "skill-system/tests/test_wp08_new_release_attempt3_protected_verifier_authority.py"
regression.write_text(r'''from __future__ import annotations

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
        self.assertIn('model_call_scope(max_calls=72, scope="preprod_semantic_goal_prototypes")', source)
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
''', encoding="utf-8")

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(bundle.relative_to(ROOT)),
        str(semantic.relative_to(ROOT)),
        str(lifecycle.relative_to(ROOT)),
        str(harness.relative_to(ROOT)),
        str(browser.relative_to(ROOT)),
        str(regression.relative_to(ROOT)),
    ],
    "root_cause": "real-model semantic certification ran auto verifier modes without a protected profile, so alignment/granularity resolved candidate-only",
}, ensure_ascii=False, indent=2))
