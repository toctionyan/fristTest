from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
AGENT_SRC = ROOT / "services/agent-service/src"
for path in (SCRIPTS, AGENT_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from wp08_release_state import (  # noqa: E402
    STATUS_AWAITING_ENVIRONMENT_RUNTIME,
    STATUS_WAITING_REPAIR_CI,
    new_state,
    parse_issue_state,
    parse_repair_environment_gate,
    render_issue_body,
    validate_state,
)


class FakeAPI:
    repository = "toctionyan/fristTest"

    def __init__(self, state: dict, *, source_run_id: int, source_candidate_sha: str):
        self.issue_body = render_issue_body(state)
        self.dispatches: list[dict] = []
        self.source_run_id = source_run_id
        self.source_candidate_sha = source_candidate_sha

    def list_issues(self, *, state: str = "open"):
        return [{"number": 267, "body": self.issue_body}] if state == "open" else []

    def update_issue(self, issue_number: int, *, body: str, close: bool = False):
        self.issue_body = body
        return {"number": issue_number, "body": body, "state": "closed" if close else "open"}

    def dispatch_wp08(self, **kwargs):
        self.dispatches.append(dict(kwargs))
        return 9000 + len(self.dispatches)

    def get_workflow_run(self, run_id: int):
        if run_id != self.source_run_id:
            raise AssertionError(f"unexpected run {run_id}")
        return {
            "id": run_id,
            "name": "wp08-full-stack-certification",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": self.source_candidate_sha,
            "status": "completed",
            "conclusion": "failure",
            "run_attempt": 1,
        }

    def list_workflow_runs(self, workflow_file: str, *, branch: str = "main", event: str | None = None):
        return []


def _base_state(*, candidate: str, attempt: int = 6, run_id: int = 600) -> dict:
    state = new_state(
        release_run_id="wp08-release-test-attempt6",
        authorized_initial_sha="a" * 40,
        current_candidate_sha=candidate,
        max_attempts=8,
        actor="github-actions[bot]",
    )
    state.update({
        "attempt": attempt,
        "current_wp08_run_id": run_id,
        "last_wp08_run_id": run_id,
        "last_wp08_conclusion": "failure",
    })
    return validate_state(state)


class Attempt6EnvironmentRuntimeRepairTests(unittest.TestCase):
    def test_repair_environment_gate_marker_is_explicit_and_narrow(self) -> None:
        body = "WP08-Post-Repair-Environment-Gate: environment_runtime\n"
        self.assertEqual(parse_repair_environment_gate(body), "environment_runtime")
        self.assertIsNone(parse_repair_environment_gate("WP08-Post-Repair-Environment-Gate: configuration\n"))

    def test_quality_success_waits_for_runtime_recovery_instead_of_dispatching(self) -> None:
        from wp08_release_coordinator import _after_quality_success

        old_candidate = "b" * 40
        new_candidate = "c" * 40
        state = _base_state(candidate=new_candidate)
        state.update({
            "status": STATUS_WAITING_REPAIR_CI,
            "repair_pr": {
                "number": 999,
                "parent_wp08_run_id": 600,
                "parent_candidate_sha": old_candidate,
                "post_quality_gate": "environment_runtime",
                "merge_commit_sha": new_candidate,
            },
        })
        api = FakeAPI(state, source_run_id=600, source_candidate_sha=old_candidate)
        updated = _after_quality_success(api, 267, state, reason="main_quality_passed")
        self.assertEqual(updated["status"], STATUS_AWAITING_ENVIRONMENT_RUNTIME)
        self.assertEqual(api.dispatches, [])
        self.assertEqual(updated["environment_runtime"]["source_wp08_run_id"], 600)
        self.assertEqual(updated["environment_runtime"]["source_candidate_sha"], old_candidate)
        self.assertFalse(updated["environment_runtime"]["resume_checkpoint"])

    def test_post_repair_runtime_resume_dispatches_fresh_candidate(self) -> None:
        from wp08_release_recovery import handle_issue_comment

        old_candidate = "b" * 40
        new_candidate = "c" * 40
        state = _base_state(candidate=new_candidate)
        state.update({
            "status": STATUS_AWAITING_ENVIRONMENT_RUNTIME,
            "repair_pr": {
                "number": 999,
                "parent_wp08_run_id": 600,
                "parent_candidate_sha": old_candidate,
                "post_quality_gate": "environment_runtime",
                "merge_commit_sha": new_candidate,
            },
            "environment_runtime": {
                "source_wp08_run_id": 600,
                "source_candidate_sha": old_candidate,
                "resume_checkpoint": False,
                "reason": "configured_model_provider_unavailable",
            },
        })
        api = FakeAPI(state, source_run_id=600, source_candidate_sha=old_candidate)
        event = {
            "issue": {"number": 267},
            "comment": {"body": "/wp08 resume environment_runtime run=600", "user": {"login": "toctionyan"}},
        }
        handle_issue_comment(api, event)
        self.assertEqual(len(api.dispatches), 1)
        dispatch = api.dispatches[0]
        self.assertEqual(dispatch["candidate_sha"], new_candidate)
        self.assertIsNone(dispatch["resume_run_id"])
        parsed = parse_issue_state(api.issue_body)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["attempt"], 7)
        self.assertEqual(parsed["status"], "CERTIFYING")

    def test_same_candidate_runtime_resume_uses_checkpoint_inputs(self) -> None:
        from wp08_release_recovery import handle_issue_comment

        candidate = "d" * 40
        state = _base_state(candidate=candidate)
        state.update({
            "status": STATUS_AWAITING_ENVIRONMENT_RUNTIME,
            "environment_runtime": {
                "source_wp08_run_id": 600,
                "source_candidate_sha": candidate,
                "resume_checkpoint": True,
                "reason": "configured_model_provider_unavailable",
            },
        })
        api = FakeAPI(state, source_run_id=600, source_candidate_sha=candidate)
        event = {
            "issue": {"number": 267},
            "comment": {"body": "/wp08 resume environment_runtime run=600", "user": {"login": "toctionyan"}},
        }
        handle_issue_comment(api, event)
        self.assertEqual(len(api.dispatches), 1)
        dispatch = api.dispatches[0]
        self.assertEqual(dispatch["candidate_sha"], candidate)
        self.assertEqual(dispatch["resume_run_id"], 600)
        self.assertEqual(dispatch["resume_run_attempt"], 1)

    def test_browser_timeout_probe_only_promotes_verified_provider_blocker(self) -> None:
        path = ROOT / "scripts/verify_product_browser_journey.py"
        spec = importlib.util.spec_from_file_location("attempt6_browser_journey", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(module._browser_response_timeout_signal("", 'page.waitForResponse: Timeout 120000ms exceeded while waiting for event "response"'))
        self.assertFalse(module._browser_response_timeout_signal("", "Timeout 30000ms exceeded"))
        source = path.read_text(encoding="utf-8")
        self.assertIn("browser_journey_post_response_timeout_probe", source)
        self.assertIn("except ConfiguredModelEnvironmentBlocked as exc", source)
        self.assertIn("except RuntimeError", source)

    def test_workflow_has_explicit_resume_inputs_and_keeps_browser_sla(self) -> None:
        workflow = (ROOT / ".github/workflows/wp08-certification.yml").read_text(encoding="utf-8")
        browser = (ROOT / "services/agent-service/frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn("resume_run_id:", workflow)
        self.assertIn("WP08_RESUME_RUN_ID_RESOLVED", workflow)
        self.assertIn("--expected-run-id \"$WP08_RESUME_RUN_ID_RESOLVED\"", workflow)
        self.assertIn('{ timeout: 120_000 }', browser)

    def test_dispatch_adapter_sends_resume_inputs_only_when_requested(self) -> None:
        source = (ROOT / "scripts/wp08_release_github.py").read_text(encoding="utf-8")
        self.assertIn('dispatch_payload["inputs"]', source)
        self.assertIn('"resume_run_id": str(resume_id)', source)
        self.assertIn('"resume_run_attempt": str(resume_attempt)', source)


if __name__ == "__main__":
    unittest.main()
