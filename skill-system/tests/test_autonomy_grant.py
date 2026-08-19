from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from autonomy_grant import (  # noqa: E402
    ABSOLUTE_FORBIDDEN_ACTIONS,
    AutonomyGrantError,
    authorize_autonomous_action,
    bind_autonomy_grant,
    create_autonomy_grant,
    revoke_autonomy_grant,
    validate_autonomy_grant,
)
from task_run import TaskRunStore  # noqa: E402


class AutonomyGrantTests(unittest.TestCase):
    def _store(self, root: Path, *, task_id: str = "autonomy-test") -> TaskRunStore:
        return TaskRunStore.open_or_create(
            root / f"{task_id}.json",
            task_id=task_id,
            task_kind="local-first-repair",
            binding={
                "change_id": "change-autonomy-test",
                "base_sha": "a" * 40,
                "branch": "agent/autonomy-test",
                "patch_owner": "product-implementer",
                "allowed_paths": ["services/agent-service/app/runtime.py"],
                "target_fingerprint": "target-1",
            },
            required_conditions=["done"],
        )

    def _grant(self, store: TaskRunStore, **overrides):
        values = {
            "task": store.payload,
            "repository": "toctionyan/fristTest",
            "branch": "agent/autonomy-test",
            "base_sha": "a" * 40,
            "issued_by": "repository-owner",
            "allowed_actions": [
                "analyze_failure",
                "edit_authorized_source",
                "add_authorized_counterexample_tests",
                "commit_current_branch",
                "push_current_branch",
                "dispatch_ci",
                "retry_transient_ci",
                "repair_meaningful_product_red",
                "advance_verified_milestone",
            ],
        }
        values.update(overrides)
        return create_autonomy_grant(**values)

    def _bind(self, store: TaskRunStore, grant: dict) -> None:
        bind_autonomy_grant(
            store,
            grant,
            repository="toctionyan/fristTest",
            owner_authorization_ref="github-owner-ack:123",
        )

    def test_grant_is_exact_task_bound_and_carries_no_write_merge_or_production_authority(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            validated = validate_autonomy_grant(
                grant,
                task=store.payload,
                repository="toctionyan/fristTest",
                branch="agent/autonomy-test",
                base_sha="a" * 40,
            )
            self.assertEqual(validated["schema"], "engineering-autonomy-grant@1")
            self.assertEqual(validated["authority_effect"], "automation_continuation_only")
            self.assertFalse(validated["write_authority_effect"])
            self.assertFalse(validated["test_authority_effect"])
            self.assertFalse(validated["merge_allowed"])
            self.assertFalse(validated["deploy_allowed"])
            self.assertFalse(validated["production_closed"])
            self.assertEqual(set(validated["forbidden_actions"]), set(ABSOLUTE_FORBIDDEN_ACTIONS))

    def test_forbidden_or_unknown_action_cannot_be_put_into_grant(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            with self.assertRaises(AutonomyGrantError):
                self._grant(store, allowed_actions=["analyze_failure", "merge"])
            with self.assertRaises(AutonomyGrantError):
                self._grant(store, allowed_actions=["invent_new_authority"])

    def test_budget_cannot_exceed_repository_autonomy_policy(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            with self.assertRaises(AutonomyGrantError):
                self._grant(store, max_repair_rounds=9)
            with self.assertRaises(AutonomyGrantError):
                self._grant(store, max_validation_retries=4)

    def test_wrong_task_branch_base_or_tampered_grant_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = self._store(root)
            grant = self._grant(store)
            with self.assertRaises(AutonomyGrantError):
                validate_autonomy_grant(
                    grant,
                    task=store.payload,
                    repository="toctionyan/fristTest",
                    branch="agent/other",
                    base_sha="a" * 40,
                )
            other = self._store(root, task_id="another-task")
            with self.assertRaises(AutonomyGrantError):
                validate_autonomy_grant(
                    grant,
                    task=other.payload,
                    repository="toctionyan/fristTest",
                    branch="agent/autonomy-test",
                    base_sha="a" * 40,
                )
            tampered = dict(grant)
            tampered["budgets"] = dict(grant["budgets"])
            tampered["budgets"]["max_repair_rounds"] = 1
            with self.assertRaises(AutonomyGrantError):
                validate_autonomy_grant(
                    tampered,
                    task=store.payload,
                    repository="toctionyan/fristTest",
                    branch="agent/autonomy-test",
                    base_sha="a" * 40,
                )

    def test_binding_requires_owner_authorization_evidence_and_is_single_grant(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            with self.assertRaises(AutonomyGrantError):
                bind_autonomy_grant(
                    store,
                    grant,
                    repository="toctionyan/fristTest",
                    owner_authorization_ref="",
                )
            first = bind_autonomy_grant(
                store,
                grant,
                repository="toctionyan/fristTest",
                owner_authorization_ref="github-owner-ack:123",
            )
            repeated = bind_autonomy_grant(
                store,
                grant,
                repository="toctionyan/fristTest",
                owner_authorization_ref="github-owner-ack:123",
            )
            self.assertEqual(first, repeated)
            competing = self._grant(store, grant_id="autonomy:competing")
            with self.assertRaises(AutonomyGrantError):
                bind_autonomy_grant(
                    store,
                    competing,
                    repository="toctionyan/fristTest",
                    owner_authorization_ref="github-owner-ack:124",
                )

    def test_product_repair_requires_real_product_red_and_existing_write_authority(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            self._bind(store, grant)

            denied = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="repair_meaningful_product_red",
                context={
                    "failure_class": "ENVIRONMENT_FAILURE",
                    "underlying_write_authority": True,
                    "exact_write_scope": True,
                    "repair_round": 1,
                },
            )
            self.assertFalse(denied.allowed)
            self.assertFalse(denied.human_required)

            no_authority = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="repair_meaningful_product_red",
                context={
                    "failure_class": "PRODUCT_SOURCE_FAILURE",
                    "underlying_write_authority": False,
                    "exact_write_scope": True,
                    "repair_round": 1,
                },
            )
            self.assertFalse(no_authority.allowed)
            self.assertTrue(no_authority.human_required)

            allowed = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="repair_meaningful_product_red",
                context={
                    "failure_class": "PRODUCT_SOURCE_FAILURE",
                    "underlying_write_authority": True,
                    "exact_write_scope": True,
                    "repair_round": 1,
                },
            )
            self.assertTrue(allowed.allowed)
            self.assertFalse(allowed.human_required)

    def test_product_repair_requires_positive_well_formed_round(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            self._bind(store, grant)
            for invalid in (0, -1, "not-a-number"):
                decision = authorize_autonomous_action(
                    store,
                    grant,
                    repository="toctionyan/fristTest",
                    action="repair_meaningful_product_red",
                    context={
                        "failure_class": "PRODUCT_SOURCE_FAILURE",
                        "underlying_write_authority": True,
                        "exact_write_scope": True,
                        "repair_round": invalid,
                    },
                )
                self.assertFalse(decision.allowed)
                self.assertTrue(decision.human_required)

    def test_test_changes_require_separate_existing_test_write_authority(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            self._bind(store, grant)
            denied = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="add_authorized_counterexample_tests",
                context={"underlying_test_write_authority": False, "exact_test_scope": True},
            )
            self.assertFalse(denied.allowed)
            self.assertTrue(denied.human_required)
            allowed = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="add_authorized_counterexample_tests",
                context={"underlying_test_write_authority": True, "exact_test_scope": True},
            )
            self.assertTrue(allowed.allowed)

    def test_transient_retry_requires_same_candidate_and_does_not_consume_product_write_authority(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            self._bind(store, grant)
            denied = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="retry_transient_ci",
                context={"failure_class": "TRANSIENT_INFRA_FAILURE", "same_candidate": False},
            )
            self.assertFalse(denied.allowed)
            allowed = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="retry_transient_ci",
                context={
                    "failure_class": "ENVIRONMENT_FAILURE",
                    "same_candidate": True,
                    "validation_retry": 2,
                },
            )
            self.assertTrue(allowed.allowed)

    def test_negative_or_malformed_validation_retry_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            self._bind(store, grant)
            for invalid in (-1, "broken"):
                decision = authorize_autonomous_action(
                    store,
                    grant,
                    repository="toctionyan/fristTest",
                    action="retry_transient_ci",
                    context={
                        "failure_class": "TRANSIENT_INFRA_FAILURE",
                        "same_candidate": True,
                        "validation_retry": invalid,
                    },
                )
                self.assertFalse(decision.allowed)
                self.assertTrue(decision.human_required)

    def test_milestone_advance_requires_terminal_pass_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            self._bind(store, grant)
            denied = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="advance_verified_milestone",
                context={"verification_status": "RUNNING", "required_gates_terminal": False},
            )
            self.assertFalse(denied.allowed)
            allowed = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="advance_verified_milestone",
                context={"verification_status": "PASS", "required_gates_terminal": True},
            )
            self.assertTrue(allowed.allowed)

    def test_merge_is_never_authorized_and_revocation_stops_all_continuation(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            self._bind(store, grant)
            merge = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="merge",
            )
            self.assertFalse(merge.allowed)
            self.assertTrue(merge.human_required)

            revoke_autonomy_grant(
                store,
                reason="owner stopped autonomous execution",
                evidence_ref="github-owner-revoke:123",
            )
            after_revoke = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="analyze_failure",
            )
            self.assertFalse(after_revoke.allowed)
            self.assertTrue(after_revoke.human_required)

    def test_grant_budget_exhaustion_requires_human_intervention(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store, max_repair_rounds=2, max_validation_retries=1)
            self._bind(store, grant)
            repair = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="repair_meaningful_product_red",
                context={
                    "failure_class": "PRODUCT_SOURCE_FAILURE",
                    "underlying_write_authority": True,
                    "exact_write_scope": True,
                    "repair_round": 3,
                },
            )
            self.assertFalse(repair.allowed)
            self.assertTrue(repair.human_required)
            retry = authorize_autonomous_action(
                store,
                grant,
                repository="toctionyan/fristTest",
                action="retry_transient_ci",
                context={
                    "failure_class": "TRANSIENT_INFRA_FAILURE",
                    "same_candidate": True,
                    "validation_retry": 2,
                },
            )
            self.assertFalse(retry.allowed)
            self.assertTrue(retry.human_required)


if __name__ == "__main__":
    unittest.main()
