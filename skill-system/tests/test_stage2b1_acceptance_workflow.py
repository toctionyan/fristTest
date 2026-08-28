"""Contract tests for the read-only source-artifact acceptance workflow."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-stage2b1-acceptance.yml"
PRODUCER = ROOT / ".github" / "workflows" / "p4-8-evidence-producer.yml"


class Stage2B1AcceptanceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.producer = PRODUCER.read_text(encoding="utf-8")

    def test_acceptance_takes_only_explicit_source_identity_and_artifacts(self) -> None:
        for field in (
            "source_run_id:",
            "source_run_attempt:",
            "payload_artifact_id:",
            "bundle_artifact_id:",
        ):
            self.assertIn(field, self.source)
        for forbidden in (
            "acceptance_artifact_id:",
            "decision_artifact_id:",
            "evidence_bundle_artifact_id:",
            "human_gate_artifact_id:",
            "provenance_artifact_id:",
            "receipt_artifact_id:",
            "attested_artifact_id:",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_source_run_and_acceptance_run_are_distinct(self) -> None:
        self.assertIn("actions/runs/${SOURCE_RUN_ID}/attempts/${SOURCE_RUN_ATTEMPT}", self.source)
        self.assertIn("stage2b1-source-verification-${{ github.run_id }}-${{ github.run_attempt }}", self.source)
        self.assertIn("github.run_id", self.source)
        self.assertIn("github.run_attempt", self.source)
        self.assertIn("source_run_id", self.source)
        self.assertIn("source_run_attempt", self.source)

    def test_source_run_is_fixed_server_owned_producer_attempt(self) -> None:
        for fragment in (
            'name == "p4-8-evidence-producer"',
            'path == ".github/workflows/p4-8-evidence-producer.yml"',
            '.event == "workflow_dispatch"',
            '.head_branch == "main"',
            '.status == "completed"',
            '.conclusion == "success"',
            'run_attempt == ($attempt|tonumber)',
            '(.repository.full_name // "") == $repository',
        ):
            self.assertIn(fragment, self.source)

    def test_both_artifacts_are_owned_by_the_same_exact_source_attempt(self) -> None:
        for fragment in (
            'PAYLOAD_NAME="p4-8-evidence-payload-${SOURCE_RUN_ID}-${SOURCE_RUN_ATTEMPT}"',
            'BUNDLE_NAME="p4-8-evidence-bundle-${SOURCE_RUN_ID}-${SOURCE_RUN_ATTEMPT}"',
            '(.workflow_run.id|tostring) == $run_id',
            '(.workflow_run.head_branch // "") == "main"',
            '(.workflow_run.head_sha // "") == $head_sha',
            'ACTUAL_DIGEST="sha256:$(sha256sum',
            'test "${ACTUAL_DIGEST}" = "${EXPECTED_DIGEST}"',
        ):
            self.assertIn(fragment, self.source)
        self.assertIn("p4-8-evidence-payload-", self.producer)
        self.assertIn("p4-8-evidence-bundle-", self.producer)
        self.assertNotIn(".workflow_run.run_attempt", self.source)

    def test_attestation_verifies_the_downloaded_payload_archive(self) -> None:
        self.assertIn("control/scripts/stage2b1_acceptance.py", self.source)
        self.assertIn("--artifact-archive incoming/payload.zip", self.source)
        self.assertIn("--source-run-id", self.source)
        self.assertIn("--source-run-attempt", self.source)
        self.assertIn("--artifact-id", self.source)
        self.assertNotIn("gh attestation verify", self.source)
        self.assertNotIn("attested-artifact.bin", self.source)
        self.assertNotIn("actions/attest", self.source)

    def test_verification_uses_the_trusted_control_plane_baseline(self) -> None:
        self.assertIn(
            "cp control/skill-system/registry/product-source-baseline.json",
            self.source,
        )
        self.assertIn('git -C control fetch --no-tags origin "${SOURCE_SHA}"', self.source)
        self.assertIn("control/scripts/verify_product_source_baseline.py", self.source)
        self.assertIn('.source_snapshot_match == true', self.source)

    def test_bundle_is_observation_only_and_cannot_supply_acceptance_material(self) -> None:
        for fragment in (
            'required = {',
            '"producer-run.json"',
            '"product-binding.json"',
            '"attestation-policy.json"',
            '"provenance.json"',
            '"workflow-run.json"',
            '"payload-artifact.json"',
            '"manifest.json"',
            'producer bundle must contain exactly the expected files',
            'file_mode not in (0, 0o100000)',
            'control/scripts/stage2b1_acceptance.py',
            '.status == "ACCEPTABLE_PREVIEW"',
            '.authority_effect == false',
            '.task_run_written == false',
            '.governance_state_changed == false',
        ):
            self.assertIn(fragment, self.source)
        for forbidden in (
            "decision.json",
            "human-gate.json",
            "receipt.json",
            "active-change.json",
            "TaskRun",
            "contents: write",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_no_latest_lookup_or_package_wrapper_remains(self) -> None:
        for fragment in ("sort_by(.created_at)", "| .[0]", "latest", "gh workflow run", "acceptance-inputs"):
            self.assertNotIn(fragment, self.source)
        self.assertNotIn("governed-stage2b1-acceptance-inputs.yml", self.source)


if __name__ == "__main__":
    unittest.main()
