from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from autonomy_grant import bind_autonomy_grant, create_autonomy_grant  # noqa: E402
from engineering_autonomy_dispatch import (  # noqa: E402
    AutonomyDispatchError,
    build_owner_authorization_evidence,
    compile_dispatch_plan,
)
from task_run import TaskRunStore  # noqa: E402


class EngineeringLocalDiagnosisNetworkBoundaryTests(unittest.TestCase):
    def test_analyze_failure_is_local_read_only_and_cannot_be_network_dispatched(self) -> None:
        repository = "toctionyan/fristTest"
        branch = "feature/diagnosis-local-only"
        base_sha = "a" * 40
        head_sha = "b" * 40
        with tempfile.TemporaryDirectory() as temp:
            store = TaskRunStore.open_or_create(
                Path(temp) / "task-run.json",
                task_id="diagnosis-local-only",
                task_kind="engineering",
                binding={
                    "repository": repository,
                    "branch": branch,
                    "base_sha": base_sha,
                },
                required_conditions=("classification_complete",),
            )
            store.checkpoint(
                status="RUNNING",
                phase="DIAGNOSIS",
                workspace_fingerprint=None,
                evidence_refs=["failure-case.json"],
            )
            grant = create_autonomy_grant(
                task=store.payload,
                repository=repository,
                branch=branch,
                base_sha=base_sha,
                issued_by="repository-owner",
                allowed_actions=("analyze_failure",),
            )
            bind_autonomy_grant(
                store,
                grant,
                repository=repository,
                owner_authorization_ref="owner:test",
            )
            trusted_ref = ".github/workflows/engineering-autonomy-authorize.yml@" + ("c" * 40)
            authorization = build_owner_authorization_evidence(
                task=store.payload,
                grant=grant,
                repository=repository,
                source_run_id=9001,
                source_run_attempt=1,
                source_head_sha=head_sha,
                failure_signature="unknown:test",
                actor="toctionyan",
                event_name="workflow_dispatch",
                trusted_workflow_ref=trusted_ref,
                authorization_id="owner:test:9001",
            )
            outcome = {
                "schema": "engineering-reconcile-decision@1",
                "decision_id": "d" * 64,
                "task_id": "diagnosis-local-only",
                "delivery_key": f"9001:1:{head_sha}",
                "decision": "ANALYZE_FAILURE",
                "action": "analyze_failure",
                "allowed": True,
                "human_required": False,
                "failure_class": "INSUFFICIENT_EVIDENCE",
                "product_write_allowed": False,
                "authority_effect": "automation_continuation_only",
                "merge_allowed": False,
                "deploy_allowed": False,
                "production_closed": False,
                "duplicate": False,
            }
            with self.assertRaises(AutonomyDispatchError):
                compile_dispatch_plan(
                    store,
                    grant,
                    authorization,
                    reconcile_outcome=outcome,
                    repository=repository,
                    trusted_workflow_ref=trusted_ref,
                    current_head_sha=head_sha,
                )


if __name__ == "__main__":
    unittest.main()
