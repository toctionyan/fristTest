#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import json

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Durable state contract: distinguish transient configured-provider/runtime
# availability from missing/invalid Environment configuration.
state_path = ROOT / "scripts/wp08_release_state.py"
replace_once(
    state_path,
    'STATUS_AWAITING_ENVIRONMENT_CONFIGURATION = "AWAITING_ENVIRONMENT_CONFIGURATION"\nSTATUS_MAIN_QUALITY_FAILED = "MAIN_QUALITY_FAILED"\n',
    'STATUS_AWAITING_ENVIRONMENT_CONFIGURATION = "AWAITING_ENVIRONMENT_CONFIGURATION"\nSTATUS_AWAITING_ENVIRONMENT_RUNTIME = "AWAITING_ENVIRONMENT_RUNTIME"\nSTATUS_MAIN_QUALITY_FAILED = "MAIN_QUALITY_FAILED"\n',
)
replace_once(
    state_path,
    '    STATUS_AWAITING_ENVIRONMENT_CONFIGURATION,\n    STATUS_MAIN_QUALITY_FAILED,\n',
    '    STATUS_AWAITING_ENVIRONMENT_CONFIGURATION,\n    STATUS_AWAITING_ENVIRONMENT_RUNTIME,\n    STATUS_MAIN_QUALITY_FAILED,\n',
)
replace_once(
    state_path,
    '_REPAIR_PARENT_RE = re.compile(r"(?mi)^\\s*WP08-Parent-Run-ID:\\s*([0-9]+)\\s*$")\n',
    '_REPAIR_PARENT_RE = re.compile(r"(?mi)^\\s*WP08-Parent-Run-ID:\\s*([0-9]+)\\s*$")\n'
    '_REPAIR_ENVIRONMENT_GATE_RE = re.compile(\n'
    '    r"(?mi)^\\s*WP08-Post-Repair-Environment-Gate:\\s*(environment_runtime)\\s*$"\n'
    ')\n',
)
replace_once(
    state_path,
    'def new_state(\n',
    'def parse_repair_environment_gate(body: str) -> str | None:\n'
    '    match = _REPAIR_ENVIRONMENT_GATE_RE.search(str(body or ""))\n'
    '    return str(match.group(1)).strip().casefold() if match else None\n\n\n'
    'def new_state(\n',
)
replace_once(
    state_path,
    '    "STATUS_AWAITING_ENVIRONMENT_CONFIGURATION",\n    "STATUS_CERTIFYING",\n',
    '    "STATUS_AWAITING_ENVIRONMENT_CONFIGURATION",\n    "STATUS_AWAITING_ENVIRONMENT_RUNTIME",\n    "STATUS_CERTIFYING",\n',
)
replace_once(
    state_path,
    '    "parse_issue_state",\n    "parse_repair_markers",\n',
    '    "parse_issue_state",\n    "parse_repair_environment_gate",\n    "parse_repair_markers",\n',
)


# 2) Dispatch adapter: support an explicit, versioned checkpoint resume input.
# Existing fresh dispatch callers remain unchanged.
github_path = ROOT / "scripts/wp08_release_github.py"
replace_once(
    github_path,
    '    def dispatch_wp08(self, *, candidate_sha: str) -> int:\n'
    '        candidate_sha = sha40(candidate_sha, name="candidate_sha")\n',
    '    def dispatch_wp08(\n'
    '        self,\n'
    '        *,\n'
    '        candidate_sha: str,\n'
    '        resume_run_id: int | None = None,\n'
    '        resume_run_attempt: int = 1,\n'
    '    ) -> int:\n'
    '        candidate_sha = sha40(candidate_sha, name="candidate_sha")\n'
    '        resume_id = (\n'
    '            positive_int(resume_run_id, name="resume_run_id")\n'
    '            if resume_run_id is not None else None\n'
    '        )\n'
    '        resume_attempt = positive_int(resume_run_attempt, name="resume_run_attempt")\n',
)
replace_once(
    github_path,
    '        status, payload = self._request(\n'
    '            "POST",\n'
    '            f"/repos/{self.repository}/actions/workflows/{WP08_WORKFLOW_FILE}/dispatches",\n'
    '            payload={"ref": MAIN_BRANCH},\n'
    '            expected=(200, 204),\n'
    '        )\n',
    '        dispatch_payload: dict[str, Any] = {"ref": MAIN_BRANCH}\n'
    '        if resume_id is not None:\n'
    '            dispatch_payload["inputs"] = {\n'
    '                "resume_run_id": str(resume_id),\n'
    '                "resume_run_attempt": str(resume_attempt),\n'
    '            }\n'
    '        status, payload = self._request(\n'
    '            "POST",\n'
    '            f"/repos/{self.repository}/actions/workflows/{WP08_WORKFLOW_FILE}/dispatches",\n'
    '            payload=dispatch_payload,\n'
    '            expected=(200, 204),\n'
    '        )\n',
)


# 3) Workflow contract: make checkpoint resume an explicit workflow_dispatch
# input, preserving repository-variable fallback for legacy/manual operation.
workflow_path = ROOT / ".github/workflows/wp08-certification.yml"
replace_once(
    workflow_path,
    '"on":\n  workflow_dispatch:\n\npermissions:\n',
    '"on":\n'
    '  workflow_dispatch:\n'
    '    inputs:\n'
    '      resume_run_id:\n'
    '        description: "Completed WP-08 run ID to resume from; must match the same candidate SHA"\n'
    '        required: false\n'
    '        type: string\n'
    '      resume_run_attempt:\n'
    '        description: "GitHub run attempt for the checkpoint artifact"\n'
    '        required: false\n'
    '        default: "1"\n'
    '        type: string\n\n'
    'permissions:\n',
)
replace_once(
    workflow_path,
    '    environment: production-certification\n    steps:\n',
    '    environment: production-certification\n'
    '    env:\n'
    '      WP08_RESUME_RUN_ID_RESOLVED: ${{ inputs.resume_run_id || vars.WP08_RESUME_RUN_ID }}\n'
    '      WP08_RESUME_RUN_ATTEMPT_RESOLVED: ${{ inputs.resume_run_attempt || vars.WP08_RESUME_RUN_ATTEMPT || \'1\' }}\n'
    '    steps:\n',
)
replace_once(
    workflow_path,
    '        if: ${{ vars.WP08_RESUME_RUN_ID != \'\' }}\n',
    '        if: ${{ env.WP08_RESUME_RUN_ID_RESOLVED != \'\' }}\n',
)
replace_once(
    workflow_path,
    '          name: wp08-full-stack-certification-${{ vars.WP08_RESUME_RUN_ID }}-${{ vars.WP08_RESUME_RUN_ATTEMPT || \'1\' }}\n'
    '          path: ${{ runner.temp }}/wp08-resume\n'
    '          github-token: ${{ github.token }}\n'
    '          repository: ${{ github.repository }}\n'
    '          run-id: ${{ vars.WP08_RESUME_RUN_ID }}\n',
    '          name: wp08-full-stack-certification-${{ env.WP08_RESUME_RUN_ID_RESOLVED }}-${{ env.WP08_RESUME_RUN_ATTEMPT_RESOLVED }}\n'
    '          path: ${{ runner.temp }}/wp08-resume\n'
    '          github-token: ${{ github.token }}\n'
    '          repository: ${{ github.repository }}\n'
    '          run-id: ${{ env.WP08_RESUME_RUN_ID_RESOLVED }}\n',
)
replace_once(
    workflow_path,
    '        if: ${{ vars.WP08_RESUME_RUN_ID != \'\' }}\n'
    '        env:\n',
    '        if: ${{ env.WP08_RESUME_RUN_ID_RESOLVED != \'\' }}\n'
    '        env:\n',
)
replace_once(
    workflow_path,
    '          --expected-run-id "${{ vars.WP08_RESUME_RUN_ID }}"\n'
    '          --expected-run-attempt "${{ vars.WP08_RESUME_RUN_ATTEMPT || \'1\' }}"\n',
    '          --expected-run-id "$WP08_RESUME_RUN_ID_RESOLVED"\n'
    '          --expected-run-attempt "$WP08_RESUME_RUN_ATTEMPT_RESOLVED"\n',
)
replace_once(
    workflow_path,
    '          WP08_RESUME_REQUESTED: ${{ vars.WP08_RESUME_RUN_ID != \'\' && \'1\' || \'0\' }}\n',
    '          WP08_RESUME_REQUESTED: ${{ env.WP08_RESUME_RUN_ID_RESOLVED != \'\' && \'1\' || \'0\' }}\n',
)


# 4) Coordinator: a repair can explicitly request a post-Quality environment
# runtime gate. Quality success then waits instead of burning the next attempt.
coordinator_path = ROOT / "scripts/wp08_release_coordinator.py"
replace_once(
    coordinator_path,
    '    STATUS_ATTEMPT_BUDGET_EXHAUSTED,\n'
    '    STATUS_CERTIFYING,\n',
    '    STATUS_ATTEMPT_BUDGET_EXHAUSTED,\n'
    '    STATUS_AWAITING_ENVIRONMENT_RUNTIME,\n'
    '    STATUS_CERTIFYING,\n',
)
replace_once(
    coordinator_path,
    '    new_state,\n'
    '    parse_repair_markers,\n',
    '    new_state,\n'
    '    parse_repair_environment_gate,\n'
    '    parse_repair_markers,\n',
)
replace_once(
    coordinator_path,
    '        "current_wp08_run_id": run_id,\n'
    '        "updated_at": utc_now(),\n',
    '        "current_wp08_run_id": run_id,\n'
    '        "environment_runtime": None,\n'
    '        "updated_at": utc_now(),\n',
)
quality_helper = '''\n\ndef _after_quality_success(\n    api: GitHubAPI,\n    issue_number: int,\n    state: Mapping[str, Any],\n    *,\n    reason: str,\n) -> dict[str, Any]:\n    current = validate_state(state)\n    repair = current.get("repair_pr") if isinstance(current.get("repair_pr"), Mapping) else {}\n    if str(repair.get("post_quality_gate") or "") == "environment_runtime":\n        source_run_id = positive_int(repair.get("parent_wp08_run_id"), name="repair parent run")\n        source_candidate_sha = sha40(\n            repair.get("parent_candidate_sha"),\n            name="repair parent candidate SHA",\n        )\n        gated = {\n            **current,\n            "status": STATUS_AWAITING_ENVIRONMENT_RUNTIME,\n            "failure_signature": "environment_runtime:awaiting_provider_recovery_after_repair",\n            "environment_runtime": {\n                "source_wp08_run_id": source_run_id,\n                "source_candidate_sha": source_candidate_sha,\n                "resume_checkpoint": False,\n                "reason": "configured_model_provider_unavailable",\n            },\n            "updated_at": utc_now(),\n            "history": _history(\n                current,\n                event="repair_quality_passed_awaiting_environment_runtime",\n                reason=reason,\n                source_wp08_run_id=source_run_id,\n                source_candidate_sha=source_candidate_sha,\n                candidate_sha=current["current_candidate_sha"],\n            ),\n        }\n        return persist_release_state(api, issue_number, gated)\n    return _dispatch(api, issue_number, current, reason=reason)\n'''
replace_once(
    coordinator_path,
    '\ndef _maybe_dispatch_after_quality(\n',
    quality_helper + '\n\ndef _maybe_dispatch_after_quality(\n',
)
replace_once(
    coordinator_path,
    '    if _quality_pass_exists(api, current["current_candidate_sha"]):\n'
    '        return _dispatch(api, issue_number, current, reason=reason)\n',
    '    if _quality_pass_exists(api, current["current_candidate_sha"]):\n'
    '        return _after_quality_success(api, issue_number, current, reason=reason)\n',
)
replace_once(
    coordinator_path,
    '    release_run_id, parent_run_id = markers\n'
    '    found = find_release_issue(api, release_run_id=release_run_id)\n',
    '    release_run_id, parent_run_id = markers\n'
    '    post_quality_gate = parse_repair_environment_gate(str(pr.get("body") or ""))\n'
    '    found = find_release_issue(api, release_run_id=release_run_id)\n',
)
replace_once(
    coordinator_path,
    '            "parent_wp08_run_id": parent_run_id,\n'
    '            "merge_commit_sha": merge_sha,\n',
    '            "parent_wp08_run_id": parent_run_id,\n'
    '            "parent_candidate_sha": current["current_candidate_sha"],\n'
    '            "post_quality_gate": post_quality_gate,\n'
    '            "merge_commit_sha": merge_sha,\n',
)
replace_once(
    coordinator_path,
    '    if conclusion == "success":\n'
    '        _dispatch(api, issue_number, current, reason="main_quality_passed")\n'
    '        return\n',
    '    if conclusion == "success":\n'
    '        _after_quality_success(api, issue_number, current, reason="main_quality_passed")\n'
    '        return\n',
)


# 5) Recovery: explicit runtime-provider classification and resume are separate
# from configuration repair. Same-candidate runtime blockers may resume their
# PASS checkpoint; post-source-repair gates dispatch fresh because source
# fingerprints intentionally changed.
recovery_path = ROOT / "scripts/wp08_release_recovery.py"
replace_once(
    recovery_path,
    '    STATUS_AWAITING_ENVIRONMENT_CONFIGURATION,\n'
    '    STATUS_CERTIFYING,\n',
    '    STATUS_AWAITING_ENVIRONMENT_CONFIGURATION,\n'
    '    STATUS_AWAITING_ENVIRONMENT_RUNTIME,\n'
    '    STATUS_CERTIFYING,\n',
)
replace_once(
    recovery_path,
    '_CLASSIFY_RE = re.compile(r"^/wp08\\s+classify\\s+environment_configuration\\s+run=([0-9]+)\\s*$")\n'
    '_RESUME_RE = re.compile(r"^/wp08\\s+resume\\s+environment_configuration\\s+run=([0-9]+)\\s*$")\n',
    '_CLASSIFY_RE = re.compile(r"^/wp08\\s+classify\\s+environment_configuration\\s+run=([0-9]+)\\s*$")\n'
    '_RESUME_RE = re.compile(r"^/wp08\\s+resume\\s+environment_configuration\\s+run=([0-9]+)\\s*$")\n'
    '_CLASSIFY_RUNTIME_RE = re.compile(r"^/wp08\\s+classify\\s+environment_runtime\\s+run=([0-9]+)\\s*$")\n'
    '_RESUME_RUNTIME_RE = re.compile(r"^/wp08\\s+resume\\s+environment_runtime\\s+run=([0-9]+)\\s*$")\n',
)
replace_once(
    recovery_path,
    'def _dispatch(api: GitHubAPI, issue_number: int, state: Mapping[str, Any], *, reason: str) -> dict[str, Any]:\n',
    'def _dispatch(\n'
    '    api: GitHubAPI,\n'
    '    issue_number: int,\n'
    '    state: Mapping[str, Any],\n'
    '    *,\n'
    '    reason: str,\n'
    '    resume_run_id: int | None = None,\n'
    '    resume_run_attempt: int = 1,\n'
    ') -> dict[str, Any]:\n',
)
replace_once(
    recovery_path,
    '    run_id = api.dispatch_wp08(candidate_sha=current["current_candidate_sha"])\n',
    '    run_id = api.dispatch_wp08(\n'
    '        candidate_sha=current["current_candidate_sha"],\n'
    '        resume_run_id=resume_run_id,\n'
    '        resume_run_attempt=resume_run_attempt,\n'
    '    )\n',
)
replace_once(
    recovery_path,
    '        "current_wp08_run_id": run_id,\n'
    '        "updated_at": utc_now(),\n',
    '        "current_wp08_run_id": run_id,\n'
    '        "environment_runtime": None,\n'
    '        "updated_at": utc_now(),\n',
)
replace_once(
    recovery_path,
    '            wp08_run_id=run_id,\n'
    '        ),\n',
    '            wp08_run_id=run_id,\n'
    '            **({"resume_from_wp08_run_id": resume_run_id} if resume_run_id is not None else {}),\n'
    '        ),\n',
)
replace_once(
    recovery_path,
    'def _validate_wp08_run(api: GitHubAPI, state: Mapping[str, Any], run_id: int, *, require_completed: bool = True) -> dict[str, Any]:\n'
    '    current = validate_state(state)\n',
    'def _validate_wp08_run(\n'
    '    api: GitHubAPI,\n'
    '    state: Mapping[str, Any],\n'
    '    run_id: int,\n'
    '    *,\n'
    '    require_completed: bool = True,\n'
    '    expected_candidate_sha: str | None = None,\n'
    ') -> dict[str, Any]:\n'
    '    current = validate_state(state)\n'
    '    expected_sha = sha40(\n'
    '        expected_candidate_sha or current["current_candidate_sha"],\n'
    '        name="expected WP-08 candidate SHA",\n'
    '    )\n',
)
replace_once(
    recovery_path,
    '    if sha40(run.get("head_sha"), name="WP-08 head_sha") != current["current_candidate_sha"]:\n'
    '        raise RecoveryError("referenced WP-08 run does not match current candidate SHA")\n',
    '    if sha40(run.get("head_sha"), name="WP-08 head_sha") != expected_sha:\n'
    '        raise RecoveryError("referenced WP-08 run does not match expected candidate SHA")\n',
)

runtime_handlers = '''\n    classify_runtime = _CLASSIFY_RUNTIME_RE.fullmatch(body)\n    if classify_runtime:\n        run_id = positive_int(classify_runtime.group(1), name="classified WP-08 run id")\n        if int(current.get("current_wp08_run_id") or 0) != run_id:\n            raise RecoveryError("classification run ID does not match active WP-08 run")\n        if current["status"] not in {STATUS_CERTIFYING, STATUS_FAILED_NEEDS_CLASSIFICATION}:\n            raise RecoveryError("ReleaseRun is not awaiting WP-08 failure classification")\n        run = _validate_wp08_run(api, current, run_id)\n        if str(run.get("conclusion") or "") != "failure":\n            raise RecoveryError("environment runtime classification requires a failed WP-08 run")\n        updated = {\n            **current,\n            "status": STATUS_AWAITING_ENVIRONMENT_RUNTIME,\n            "last_wp08_run_id": run_id,\n            "last_wp08_conclusion": "failure",\n            "failure_signature": "environment_runtime:configured_model_provider_unavailable",\n            "environment_runtime": {\n                "source_wp08_run_id": run_id,\n                "source_candidate_sha": current["current_candidate_sha"],\n                "resume_checkpoint": True,\n                "reason": "configured_model_provider_unavailable",\n            },\n            "updated_at": utc_now(),\n            "history": _history(\n                current,\n                event="wp08_classified_environment_runtime",\n                wp08_run_id=run_id,\n                actor=actor,\n            ),\n        }\n        persist_release_state(api, issue_number, updated)\n        return\n\n    resume_runtime = _RESUME_RUNTIME_RE.fullmatch(body)\n    if resume_runtime:\n        run_id = positive_int(resume_runtime.group(1), name="resume WP-08 run id")\n        if current["status"] != STATUS_AWAITING_ENVIRONMENT_RUNTIME:\n            raise RecoveryError("ReleaseRun is not awaiting configured model runtime recovery")\n        runtime_gate = current.get("environment_runtime") if isinstance(current.get("environment_runtime"), Mapping) else {}\n        source_run_id = positive_int(runtime_gate.get("source_wp08_run_id"), name="environment runtime source run id")\n        if run_id != source_run_id:\n            raise RecoveryError("resume run ID does not match active environment runtime blocker")\n        source_candidate_sha = sha40(\n            runtime_gate.get("source_candidate_sha"),\n            name="environment runtime source candidate SHA",\n        )\n        run = _validate_wp08_run(\n            api,\n            current,\n            run_id,\n            expected_candidate_sha=source_candidate_sha,\n        )\n        if str(run.get("conclusion") or "") != "failure":\n            raise RecoveryError("environment runtime resume requires the failed blocker run")\n        resume_checkpoint = bool(runtime_gate.get("resume_checkpoint"))\n        if resume_checkpoint:\n            if source_candidate_sha != current["current_candidate_sha"]:\n                raise RecoveryError("checkpoint resume cannot cross candidate source identity")\n            _dispatch(\n                api,\n                issue_number,\n                current,\n                reason="environment_runtime_recovered",\n                resume_run_id=run_id,\n                resume_run_attempt=positive_int(run.get("run_attempt", 1), name="WP-08 run attempt"),\n            )\n        else:\n            _dispatch(\n                api,\n                issue_number,\n                current,\n                reason="environment_runtime_recovered_after_repair",\n            )\n        return\n'''
replace_once(
    recovery_path,
    '    classify = _CLASSIFY_RE.fullmatch(body)\n',
    runtime_handlers + '\n    classify = _CLASSIFY_RE.fullmatch(body)\n',
)
replace_once(
    recovery_path,
    '        _dispatch(api, issue_number, current, reason="environment_configuration_updated")\n',
    '        _dispatch(\n'
    '            api,\n'
    '            issue_number,\n'
    '            current,\n'
    '            reason="environment_configuration_updated",\n'
    '            resume_run_id=run_id,\n'
    '            resume_run_attempt=positive_int(run.get("run_attempt", 1), name="WP-08 run attempt"),\n'
    '        )\n',
)


# 6) Protected browser harness: if a browser response gate fires before a graph
# snapshot can record the model failure, perform one independent post-failure
# configured-model smoke probe. Only a provider-environment blocked probe turns
# the browser lane into BLOCKED_BY_ENVIRONMENT; otherwise the original FAIL is
# preserved. The 120-second browser response SLA is untouched.
browser_path = ROOT / "scripts/verify_product_browser_journey.py"
replace_once(
    browser_path,
    'def _node_binary() -> Path:\n',
    'def _browser_response_timeout_signal(stdout: str, stderr: str) -> bool:\n'
    '    combined = f"{stdout}\\n{stderr}"\n'
    '    return (\n'
    '        "page.waitForResponse: Timeout 120000ms exceeded" in combined\n'
    '        or "Timeout 120000ms exceeded while waiting for event \\\"response\\\"" in combined\n'
    '    )\n\n\n'
    'def _node_binary() -> Path:\n',
)
replace_once(
    browser_path,
    '                provider_failure = _environmental_failure(graph_diagnostics)\n'
    '                if provider_failure is not None:\n'
    '                    raise ConfiguredModelEnvironmentBlocked({\n'
    '                        "phase": "browser_journey",\n'
    '                        "provider_failure": provider_failure,\n'
    '                        "model_preflight": model_preflight,\n'
    '                    })\n'
    '                raise RuntimeError({\n',
    '                provider_failure = _environmental_failure(graph_diagnostics)\n'
    '                if provider_failure is not None:\n'
    '                    raise ConfiguredModelEnvironmentBlocked({\n'
    '                        "phase": "browser_journey",\n'
    '                        "provider_failure": provider_failure,\n'
    '                        "model_preflight": model_preflight,\n'
    '                    })\n'
    '                if not deterministic_model and _browser_response_timeout_signal(result.stdout, result.stderr):\n'
    '                    try:\n'
    '                        _configured_model_preflight(harness.env)\n'
    '                    except ConfiguredModelEnvironmentBlocked as exc:\n'
    '                        raise ConfiguredModelEnvironmentBlocked({\n'
    '                            "phase": "browser_journey_post_response_timeout_probe",\n'
    '                            "browser_response_timeout": True,\n'
    '                            "provider_probe": exc.diagnostics,\n'
    '                            "model_preflight": model_preflight,\n'
    '                        }) from exc\n'
    '                    except RuntimeError:\n'
    '                        # A non-environment smoke failure must not relabel the browser failure.\n'
    '                        pass\n'
    '                raise RuntimeError({\n',
)


# 7) Focused governance tests.
test_path = ROOT / "skill-system/tests/test_wp08_new_release_attempt6_environment_runtime.py"
test_path.write_text(r'''from __future__ import annotations

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
''', encoding="utf-8")

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(state_path.relative_to(ROOT)),
        str(github_path.relative_to(ROOT)),
        str(workflow_path.relative_to(ROOT)),
        str(coordinator_path.relative_to(ROOT)),
        str(recovery_path.relative_to(ROOT)),
        str(browser_path.relative_to(ROOT)),
        str(test_path.relative_to(ROOT)),
    ],
}, ensure_ascii=False, indent=2))
