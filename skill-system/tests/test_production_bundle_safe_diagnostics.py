from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "verify_production_certification_bundle.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "production_bundle_safe_diagnostics_regression", RUNNER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductionBundleSafeDiagnosticsTests(unittest.TestCase):
    def test_real_model_failure_projects_only_safe_nested_diagnostics(self) -> None:
        runner = _load_runner()
        protected_secret = "protected-secret-value-123456"
        env = {
            runner.TOOLCHAIN_EVIDENCE_ENV: "/tmp/fake-toolchain.json",
            runner.TOOLCHAIN_FINGERPRINT_ENV: "a" * 64,
            "OPENAI_API_KEY": protected_secret,
        }
        payload = {
            "status": "FAIL",
            "reason": "real_model_certification_component_failed",
            "error_code": "real_model_certification_component_failed",
            "credential_fingerprint_sha256_16": "must-not-propagate",
            "unknown_raw_field": "must-not-propagate",
            "real_model_bundle": {
                "status": "FAIL",
                "reason": "real_model_certification_component_failed",
                "error_code": "semantic_component_failed",
                "failed_component": "semantic",
                "identity": {"credential_fingerprint_sha256_16": "must-not-propagate"},
                "component_diagnostic": {
                    "reason": "semantic_live_call_failed",
                    "error_code": "semantic_provider_response_invalid",
                    "error_type": "RuntimeError",
                    "error": f"provider rejected request carrying {protected_secret}",
                    "token": "must-not-propagate",
                    "credential_fingerprint_sha256_16": "must-not-propagate",
                },
            },
        }

        projected = runner._safe_failure_diagnostic("real_model", payload, env)

        self.assertEqual(
            projected["component_error_code"],
            "real_model_certification_component_failed",
        )
        self.assertEqual(projected["real_model_failed_component"], "semantic")
        self.assertEqual(
            projected["real_model_component_error_code"],
            "semantic_component_failed",
        )
        diagnostic = projected["real_model_component_diagnostic"]
        self.assertEqual(diagnostic["component"], "semantic")
        self.assertEqual(diagnostic["error_code"], "semantic_provider_response_invalid")
        self.assertNotIn(protected_secret, diagnostic["error"])
        self.assertIn("***", diagnostic["error"])

        flattened = repr(projected)
        self.assertNotIn("must-not-propagate", flattened)
        self.assertNotIn("credential_fingerprint_sha256_16", flattened)
        self.assertNotIn("unknown_raw_field", flattened)
        self.assertNotIn("token", diagnostic)

    def test_non_real_model_failure_does_not_copy_nested_payload(self) -> None:
        runner = _load_runner()
        projected = runner._safe_failure_diagnostic(
            "postgres",
            {
                "status": "FAIL",
                "error_code": "postgres_probe_failed",
                "component_diagnostic": {"error": "raw nested detail"},
            },
            {},
        )

        self.assertEqual(projected, {"component_error_code": "postgres_probe_failed"})


if __name__ == "__main__":
    unittest.main()
