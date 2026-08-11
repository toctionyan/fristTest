#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


# 1) Make coordinator the sole owner of workflow-run state transitions, while
# preserving distinct history labels for live event delivery vs reconciliation.
coordinator_path = ROOT / "scripts" / "wp08_release_coordinator.py"
coordinator = coordinator_path.read_text(encoding="utf-8")
coordinator = replace_once(
    coordinator,
    "def handle_quality_run(api: GitHubAPI, workflow_run: Mapping[str, Any]) -> None:\n",
    "def handle_quality_run(\n    api: GitHubAPI,\n    workflow_run: Mapping[str, Any],\n    *,\n    source: str = \"event\",\n) -> None:\n",
    "quality signature",
)
coordinator = replace_once(
    coordinator,
    "    if conclusion == \"success\":\n        _after_quality_success(api, issue_number, current, reason=\"main_quality_passed\")\n        return\n",
    "    if conclusion == \"success\":\n        reason = (\n            \"reconciled_main_quality_passed\"\n            if source == \"reconcile\"\n            else \"main_quality_passed\"\n        )\n        _after_quality_success(api, issue_number, current, reason=reason)\n        return\n",
    "quality success provenance",
)
coordinator = replace_once(
    coordinator,
    "            event=\"main_quality_failed\",\n",
    "            event=(\n                \"main_quality_reconciled_failure\"\n                if source == \"reconcile\"\n                else \"main_quality_failed\"\n            ),\n",
    "quality failure provenance",
)
coordinator = replace_once(
    coordinator,
    "def handle_wp08_run(api: GitHubAPI, workflow_run: Mapping[str, Any]) -> None:\n",
    "def handle_wp08_run(\n    api: GitHubAPI,\n    workflow_run: Mapping[str, Any],\n    *,\n    source: str = \"event\",\n) -> None:\n",
    "wp08 signature",
)
coordinator = replace_once(
    coordinator,
    "                event=\"wp08_passed\",\n",
    "                event=(\n                    \"wp08_reconciled_pass\"\n                    if source == \"reconcile\"\n                    else \"wp08_passed\"\n                ),\n",
    "wp08 pass provenance",
)
coordinator = replace_once(
    coordinator,
    "                event=\"wp08_retryable_workflow_end\",\n",
    "                event=(\n                    \"wp08_reconciled_retryable_end\"\n                    if source == \"reconcile\"\n                    else \"wp08_retryable_workflow_end\"\n                ),\n",
    "wp08 retry provenance",
)
coordinator = replace_once(
    coordinator,
    "        _dispatch(api, issue_number, retry_state, reason=f\"bounded_retry_after_{conclusion}\")\n",
    "        retry_reason = (\n            f\"reconciled_{conclusion}\"\n            if source == \"reconcile\"\n            else f\"bounded_retry_after_{conclusion}\"\n        )\n        _dispatch(api, issue_number, retry_state, reason=retry_reason)\n",
    "wp08 retry reason",
)
coordinator = replace_once(
    coordinator,
    "            event=\"wp08_requires_classification\",\n",
    "            event=(\n                \"wp08_reconciled_completed_failure\"\n                if source == \"reconcile\"\n                else \"wp08_requires_classification\"\n            ),\n",
    "wp08 failure provenance",
)
coordinator = replace_once(
    coordinator,
    "def handle_workflow_run(api: GitHubAPI, event: Mapping[str, Any]) -> None:\n    workflow_run = event.get(\"workflow_run\") if isinstance(event.get(\"workflow_run\"), Mapping) else {}\n",
    "def handle_workflow_run(\n    api: GitHubAPI,\n    event: Mapping[str, Any],\n    *,\n    source: str = \"event\",\n) -> None:\n    if source not in {\"event\", \"reconcile\"}:\n        raise CoordinatorError(\"workflow run source must be event or reconcile\")\n    workflow_run = event.get(\"workflow_run\") if isinstance(event.get(\"workflow_run\"), Mapping) else {}\n",
    "workflow source contract",
)
coordinator = replace_once(
    coordinator,
    "        handle_quality_run(api, workflow_run)\n    elif name == WP08_WORKFLOW_NAME:\n        handle_wp08_run(api, workflow_run)\n",
    "        handle_quality_run(api, workflow_run, source=source)\n    elif name == WP08_WORKFLOW_NAME:\n        handle_wp08_run(api, workflow_run, source=source)\n",
    "workflow source forwarding",
)
coordinator_path.write_text(coordinator, encoding="utf-8")


# 2) Reduce recovery reconciliation to observation + replay. Issue-comment
# environment controls remain separate and unchanged.
recovery_path = ROOT / "scripts" / "wp08_release_recovery.py"
recovery = recovery_path.read_text(encoding="utf-8")
recovery = replace_once(
    recovery,
    "from wp08_release_state import (  # noqa: E402\n",
    "from wp08_release_coordinator import handle_workflow_run as consume_workflow_run  # noqa: E402\nfrom wp08_release_state import (  # noqa: E402\n",
    "recovery coordinator import",
)
for unused in (
    "    RETRYABLE_WORKFLOW_CONCLUSIONS,\n",
    "    STATUS_MAIN_QUALITY_FAILED,\n",
    "    STATUS_WP08_PASS,\n",
):
    recovery = recovery.replace(unused, "")
start = recovery.index("\ndef _complete_wp08(")
end = recovery.index("\ndef _authorized_commenter(", start)
replacement = r'''

def _replay_workflow_run(api: GitHubAPI, run: Mapping[str, Any]) -> None:
    """Replay authoritative GitHub run evidence through coordinator authority."""
    consume_workflow_run(api, {"workflow_run": dict(run)}, source="reconcile")


def _latest_completed_quality_run(
    api: GitHubAPI,
    state: Mapping[str, Any],
) -> dict[str, Any] | None:
    current = validate_state(state)
    matching = [
        row
        for row in api.list_workflow_runs(QUALITY_WORKFLOW_FILE, event="push")
        if str(row.get("head_branch") or "") == MAIN_BRANCH
        and str(row.get("head_sha") or "").casefold() == current["current_candidate_sha"]
        and str(row.get("status") or "") == "completed"
    ]
    if not matching:
        return None
    return dict(max(matching, key=lambda row: int(row.get("id") or 0)))


def reconcile(api: GitHubAPI) -> None:
    """Observe missed terminal evidence and replay it; never decide state here."""
    found = find_release_issue(api)
    if found is None:
        return
    _, state = found
    current = validate_state(state)
    if current["status"] == STATUS_CERTIFYING:
        run_id = positive_int(current.get("current_wp08_run_id"), name="current_wp08_run_id")
        run = _validate_wp08_run(api, current, run_id, require_completed=False)
        if str(run.get("status") or "") == "completed":
            _replay_workflow_run(api, run)
        return
    if current["status"] in {STATUS_WAITING_MAIN_QUALITY, STATUS_WAITING_REPAIR_CI}:
        run = _latest_completed_quality_run(api, current)
        if run is not None:
            _replay_workflow_run(api, run)
'''
recovery = recovery[:start] + replacement + recovery[end:]
recovery_path.write_text(recovery, encoding="utf-8")


# 3) Permanent workflow: retain event delivery, keep schedule fallback, add a
# trusted manual reconcile operation, and stagger cron away from minute 0.
workflow_path = ROOT / ".github" / "workflows" / "wp08-release-coordinator.yml"
workflow = workflow_path.read_text(encoding="utf-8")
workflow = replace_once(
    workflow,
    "  workflow_dispatch:\n  schedule:\n    - cron: '*/5 * * * *'\n",
    "  workflow_dispatch:\n    inputs:\n      operation:\n        description: Authorize a new ReleaseRun or reconcile the active one\n        required: true\n        default: authorize\n        type: choice\n        options:\n          - authorize\n          - reconcile\n  schedule:\n    - cron: '2-59/5 * * * *'\n",
    "workflow dispatch inputs",
)
workflow = replace_once(
    workflow,
    "        env:\n          GITHUB_TOKEN: ${{ github.token }}\n",
    "        env:\n          GITHUB_TOKEN: ${{ github.token }}\n          WP08_COORDINATOR_OPERATION: ${{ inputs.operation }}\n",
    "workflow operation env",
)
workflow = replace_once(
    workflow,
    "            workflow_dispatch)\n              python -B scripts/wp08_release_coordinator.py --mode authorize\n              ;;\n",
    "            workflow_dispatch)\n              case \"${WP08_COORDINATOR_OPERATION:-authorize}\" in\n                authorize)\n                  python -B scripts/wp08_release_coordinator.py --mode authorize\n                  ;;\n                reconcile)\n                  python -B scripts/wp08_release_recovery.py --mode reconcile\n                  ;;\n                *)\n                  echo \"Unsupported workflow_dispatch operation: ${WP08_COORDINATOR_OPERATION}\" >&2\n                  exit 2\n                  ;;\n              esac\n              ;;\n",
    "workflow manual reconcile route",
)
workflow_path.write_text(workflow, encoding="utf-8")


# 4) Strengthen the static coordinator contract around the permanent recovery
# entrypoint without broadening any permissions.
contract_path = ROOT / "scripts" / "wp08_release_coordinator_contract.py"
contract = contract_path.read_text(encoding="utf-8")
contract = replace_once(
    contract,
    '        "workflow_dispatch:",\n',
    '        "workflow_dispatch:",\n        "operation:",\n        "default: authorize",\n        "- reconcile",\n        "cron: \'2-59/5 * * * *\'",\n',
    "contract workflow recovery fragments",
)
contract = replace_once(
    contract,
    '        "scripts/wp08_release_coordinator.py --mode workflow-run",\n',
    '        "scripts/wp08_release_coordinator.py --mode workflow-run",\n        "scripts/wp08_release_recovery.py --mode reconcile",\n        "WP08_COORDINATOR_OPERATION",\n',
    "contract reconcile command",
)
contract = replace_once(
    contract,
    '        "STATUS_WAITING_REPAIR_CI",\n',
    '        "STATUS_WAITING_REPAIR_CI",\n        "workflow run source must be event or reconcile",\n        "wp08_reconciled_completed_failure",\n',
    "contract coordinator reconcile authority",
)
contract = replace_once(
    contract,
    '        "semantic_failure_auto_retry": False,\n',
    '        "semantic_failure_auto_retry": False,\n        "manual_reconcile": True,\n        "reconciliation_authority": "coordinator-event-replay",\n',
    "contract result fields",
)
contract_path.write_text(contract, encoding="utf-8")


# 5) Add domain-neutral counterexamples. Use unittest so the existing Skill
# discovery path executes them rather than merely importing the file.
test_path = ROOT / "skill-system" / "tests" / "test_wp08_m3_reconciler_authority.py"
test_path.write_text(r'''from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STATE = _load("wp08_m3_state_test", "scripts/wp08_release_state.py")
COORD = _load("wp08_m3_coordinator_test", "scripts/wp08_release_coordinator.py")
RECOVERY = _load("wp08_m3_recovery_test", "scripts/wp08_release_recovery.py")
CONTRACT = _load("wp08_m3_contract_test", "scripts/wp08_release_coordinator_contract.py")


def _state(*, status: str, attempt: int = 2) -> dict:
    row = STATE.new_state(
        release_run_id="wp08-release-123456",
        authorized_initial_sha="a" * 40,
        current_candidate_sha="b" * 40,
        max_attempts=8,
        actor="toctionyan",
    )
    row.update({
        "status": status,
        "attempt": attempt,
        "current_wp08_run_id": 42,
    })
    return STATE.validate_state(row)


class FakeAPI:
    repository = "toctionyan/fristTest"

    def __init__(self, state: dict) -> None:
        self.issue = {"number": 219, "body": STATE.render_issue_body(state)}
        self.updated: list[dict] = []
        self.dispatched: list[str] = []
        self.run = {
            "id": 42,
            "name": "wp08-full-stack-certification",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": "b" * 40,
            "status": "completed",
            "conclusion": "failure",
        }
        self.quality_runs: list[dict] = []

    def list_issues(self, *, state: str = "open"):
        return [self.issue] if state == "open" else []

    def update_issue(self, issue_number: int, *, body: str, close: bool = False):
        self.assert_issue(issue_number)
        self.issue["body"] = body
        parsed = STATE.parse_issue_state(body)
        assert parsed is not None
        self.updated.append(parsed)
        return self.issue

    def assert_issue(self, issue_number: int) -> None:
        assert issue_number == 219

    def get_workflow_run(self, run_id: int):
        assert run_id == 42
        return dict(self.run)

    def list_workflow_runs(self, workflow_file: str, *, branch: str = "main", event: str | None = None):
        return [dict(row) for row in self.quality_runs]

    def dispatch_wp08(self, *, candidate_sha: str) -> int:
        self.dispatched.append(candidate_sha)
        return 9000 + len(self.dispatched)


class M3ReconcilerAuthorityTests(unittest.TestCase):
    def test_recovery_reconcile_replays_completed_wp08_without_direct_mutation(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_CERTIFYING))
        calls: list[tuple[dict, str]] = []
        original = RECOVERY.consume_workflow_run
        try:
            RECOVERY.consume_workflow_run = lambda _api, event, *, source="event": calls.append((event, source))
            RECOVERY.reconcile(api)
        finally:
            RECOVERY.consume_workflow_run = original
        self.assertEqual([], api.updated)
        self.assertEqual(1, len(calls))
        self.assertEqual(42, calls[0][0]["workflow_run"]["id"])
        self.assertEqual("reconcile", calls[0][1])

    def test_quality_reconcile_replays_latest_exact_completed_run_only(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_WAITING_REPAIR_CI))
        api.quality_runs = [
            {"id": 50, "name": "quality", "event": "push", "head_branch": "main", "head_sha": "b" * 40, "status": "completed", "conclusion": "success"},
            {"id": 51, "name": "quality", "event": "push", "head_branch": "main", "head_sha": "c" * 40, "status": "completed", "conclusion": "failure"},
            {"id": 52, "name": "quality", "event": "push", "head_branch": "main", "head_sha": "b" * 40, "status": "in_progress", "conclusion": None},
            {"id": 53, "name": "quality", "event": "push", "head_branch": "main", "head_sha": "b" * 40, "status": "completed", "conclusion": "failure"},
        ]
        calls: list[tuple[dict, str]] = []
        original = RECOVERY.consume_workflow_run
        try:
            RECOVERY.consume_workflow_run = lambda _api, event, *, source="event": calls.append((event, source))
            RECOVERY.reconcile(api)
        finally:
            RECOVERY.consume_workflow_run = original
        self.assertEqual([53], [row[0]["workflow_run"]["id"] for row in calls])
        self.assertEqual("reconcile", calls[0][1])

    def test_coordinator_owns_reconciled_failure_transition_and_history(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_CERTIFYING))
        COORD.handle_workflow_run(api, {"workflow_run": dict(api.run)}, source="reconcile")
        current = STATE.parse_issue_state(api.issue["body"])
        assert current is not None
        self.assertEqual(STATE.STATUS_FAILED_NEEDS_CLASSIFICATION, current["status"])
        self.assertEqual("failure", current["last_wp08_conclusion"])
        self.assertEqual("wp08_reconciled_completed_failure", current["history"][-1]["event"])
        self.assertEqual([], api.dispatched)

    def test_reconciled_retry_still_cannot_cross_attempt_budget(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_CERTIFYING, attempt=8))
        run = dict(api.run)
        run["conclusion"] = "cancelled"
        COORD.handle_workflow_run(api, {"workflow_run": run}, source="reconcile")
        current = STATE.parse_issue_state(api.issue["body"])
        assert current is not None
        self.assertEqual(STATE.STATUS_ATTEMPT_BUDGET_EXHAUSTED, current["status"])
        self.assertEqual(8, current["attempt"])
        self.assertEqual([], api.dispatched)

    def test_reconcile_source_rejects_unknown_authority(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_CERTIFYING))
        with self.assertRaisesRegex(COORD.CoordinatorError, "source must be event or reconcile"):
            COORD.handle_workflow_run(api, {"workflow_run": dict(api.run)}, source="model_guess")

    def test_permanent_workflow_has_manual_reconcile_and_staggered_fallback(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "wp08-release-coordinator.yml").read_text(encoding="utf-8")
        self.assertIn("operation:", workflow)
        self.assertIn("default: authorize", workflow)
        self.assertIn("- reconcile", workflow)
        self.assertIn("cron: '2-59/5 * * * *'", workflow)
        self.assertNotIn("cron: '*/5 * * * *'", workflow)
        self.assertIn("WP08_COORDINATOR_OPERATION", workflow)
        self.assertIn("scripts/wp08_release_recovery.py --mode reconcile", workflow)
        self.assertNotIn("environment: production-certification", workflow)
        self.assertNotIn("secrets.", workflow)

    def test_reconcile_function_has_no_direct_state_transition_authority(self) -> None:
        source = (ROOT / "scripts" / "wp08_release_recovery.py").read_text(encoding="utf-8")
        segment = source[source.index("def reconcile("):source.index("def _authorized_commenter(")]
        self.assertIn("_replay_workflow_run", segment)
        self.assertNotIn("persist_release_state", segment)
        self.assertNotIn("_dispatch(", segment)
        self.assertNotIn("def _complete_wp08", source)
        self.assertNotIn("def _reconcile_quality", source)

    def test_static_contract_declares_manual_reconcile_authority(self) -> None:
        result = CONTRACT.validate_static(ROOT)
        self.assertEqual("PASS", result["status"])
        self.assertIs(True, result["manual_reconcile"])
        self.assertEqual("coordinator-event-replay", result["reconciliation_authority"])
        self.assertIs(False, result["production_closed"])


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print("M3 reconciler patch applied")
