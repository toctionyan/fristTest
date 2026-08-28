#!/usr/bin/env python3
"""Verify one real P4.8 producer bundle as a read-only acceptance preview.

This command intentionally has no governance-decision, receipt, human-gate,
or generic attested-artifact input. Those caller-assembled objects were an
unsafe trust root. It constructs a read-only receipt and obtains the protected
approval proof from the fixed GitHub environment verifier; the only accepted
source evidence is the server-owned P4.8 producer bundle plus the exact
artifact archive selected by its run and artifact metadata.

The result is evidence readiness only.  ``ACCEPTABLE_PREVIEW`` never writes
TaskRun, active-change, or any other governance state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from stage2b1_external_issuer import (  # type: ignore  # noqa: E402
    P48SourceArtifactProof,
    ExternalIssuerProof,
    verify_p4_8_payload_attestation,
    verify_p4_8_source_artifact,
)
from stage2b1_protected_human_gate import (  # type: ignore  # noqa: E402
    Stage2B1ProtectedApprovalProof,
    Stage2B1ProtectedHumanGateError,
    verify_stage2b1_protected_approval,
)
from stage2b1_provenance import (  # type: ignore  # noqa: E402
    VerifiedArtifactProvenance,
    verify_artifact_provenance,
)
from product_source_baseline_policy import (  # type: ignore  # noqa: E402
    BaselineDocument,
    ProductSourcePolicyError,
    load_baseline_document,
)
from stage_acceptance_reducer import (  # type: ignore  # noqa: E402
    ACCEPTABLE_PREVIEW,
    reduce_trusted_stage_acceptance,
    validate_trusted_stage_acceptance_decision,
)
from stage_evidence_receipt import (  # type: ignore  # noqa: E402
    StageEvidenceReceiptError,
    build_stage_evidence_receipt,
    validate_stage_evidence_receipt,
)


VERIFICATION_SCHEMA = "stage2b1-acceptance-verification@2"
PRODUCER_SCHEMA = "p4-8-evidence-producer@2"
PROVENANCE_SCHEMA = "stage2b1-github-provenance@1"
PRODUCER_WORKFLOW = ".github/workflows/p4-8-evidence-producer.yml"
PRODUCER_WORKFLOW_NAME = "p4-8-evidence-producer"
PRODUCER_REPOSITORY = "toctionyan/fristTest"
PRODUCER_EVENT = "workflow_dispatch"
PRODUCER_REF = "refs/heads/main"
PAYLOAD_FILES = frozenset(
    {"producer-run.json", "product-binding.json", "attestation-policy.json"}
)
BUNDLE_FILES = frozenset(
    {
        *PAYLOAD_FILES,
        "provenance.json",
        "workflow-run.json",
        "payload-artifact.json",
        "manifest.json",
    }
)
COMMON_BINDING_FIELDS = (
    "stage_id",
    "accepted_state_id",
    "product_source_ref",
    "protected_snapshot_digest",
    "control_plane_ref",
    "execution_repo_ref",
)
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
RECEIPT_POLICY = "stage2b1-p3-evidence-receipt@1"

# This is the closed projection accepted by the trusted reducer. The complete
# sealed approval remains in the preview output; only these stable fields are
# used as the reducer's expected approval contract.
_EXPECTED_APPROVAL_FIELDS = (
    "stage_id",
    "accepted_state_id",
    "product_source_ref",
    "protected_snapshot_digest",
    "control_plane_ref",
    "execution_repo_ref",
    "gate_id",
    "gate_sha256",
    "task_id",
    "repository",
    "workflow_id",
    "workflow_path",
    "workflow_ref",
    "ref",
    "head_sha",
    "run_id",
    "run_attempt",
    "environment",
    "environment_id",
    "reviewer_login",
    "reviewer_id",
    "run_actor_login",
    "run_actor_id",
    "status",
    "approval_sha256",
    "evidence_bindings",
)


class Stage2B1AcceptanceCommandError(ValueError):
    """Raised when a producer bundle or archive is incomplete or inconsistent."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise Stage2B1AcceptanceCommandError("json_not_canonicalizable") from exc


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Stage2B1AcceptanceCommandError(f"{field}_invalid")
    return value.strip()


def _positive(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise Stage2B1AcceptanceCommandError(f"{field}_invalid")
    return value


def _sha1(value: object, *, field: str) -> str:
    text = _text(value, field=field)
    if _SHA1.fullmatch(text) is None:
        raise Stage2B1AcceptanceCommandError(f"{field}_invalid")
    return text


def _sha256(value: object, *, field: str) -> str:
    text = _text(value, field=field)
    if _SHA256.fullmatch(text) is None:
        raise Stage2B1AcceptanceCommandError(f"{field}_invalid")
    return text


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Stage2B1AcceptanceCommandError(f"{field}_not_object")
    return dict(value)


def _closed(value: object, fields: frozenset[str], *, field: str) -> dict[str, Any]:
    payload = _object(value, field=field)
    if set(payload) != set(fields):
        raise Stage2B1AcceptanceCommandError(f"{field}_fields_invalid")
    return payload


def _bundle_dir(workspace: Path, raw_path: Path) -> Path:
    path = Path(raw_path)
    if path.is_symlink() or not path.is_dir():
        raise Stage2B1AcceptanceCommandError("producer_bundle_missing_or_unsafe")
    try:
        resolved = path.resolve()
        resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise Stage2B1AcceptanceCommandError("producer_bundle_outside_workspace") from exc
    if resolved.is_symlink():
        raise Stage2B1AcceptanceCommandError("producer_bundle_missing_or_unsafe")
    return resolved


def _file(bundle: Path, name: str) -> Path:
    path = bundle / name
    if path.is_symlink() or not path.is_file():
        raise Stage2B1AcceptanceCommandError(f"producer_bundle_file_missing_or_unsafe:{name}")
    return path


def _load_json(bundle: Path, name: str, *, canonical: bool = True) -> dict[str, Any]:
    path = _file(bundle, name)
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage2B1AcceptanceCommandError(f"producer_bundle_json_invalid:{name}") from exc
    result = _object(value, field=name)
    if canonical and raw != _canonical(result):
        raise Stage2B1AcceptanceCommandError(f"producer_bundle_json_not_canonical:{name}")
    return result


def _validate_bundle_files(bundle: Path) -> None:
    actual = {path.name for path in bundle.iterdir()}
    if actual != set(BUNDLE_FILES):
        raise Stage2B1AcceptanceCommandError("producer_bundle_files_invalid")
    for name in BUNDLE_FILES:
        _file(bundle, name)


def _validate_run_document(run: Mapping[str, Any], producer_run: Mapping[str, Any]) -> dict[str, Any]:
    identity = _closed(
        producer_run.get("run"),
        frozenset(
            {
                "repository",
                "workflow",
                "workflow_path",
                "event",
                "ref",
                "head_sha",
                "run_id",
                "run_attempt",
                "ref_protected",
            }
        ),
        field="producer_run.run",
    )
    if producer_run.get("schema") != PRODUCER_SCHEMA:
        raise Stage2B1AcceptanceCommandError("producer_run_schema_invalid")
    if identity["repository"] != PRODUCER_REPOSITORY:
        raise Stage2B1AcceptanceCommandError("producer_run_repository_invalid")
    if identity["workflow"] != PRODUCER_WORKFLOW_NAME or identity["workflow_path"] != PRODUCER_WORKFLOW:
        raise Stage2B1AcceptanceCommandError("producer_run_workflow_invalid")
    if identity["event"] != PRODUCER_EVENT or identity["ref"] != PRODUCER_REF:
        raise Stage2B1AcceptanceCommandError("producer_run_scope_invalid")
    if identity["ref_protected"] != "true":
        raise Stage2B1AcceptanceCommandError("producer_run_not_protected")
    head_sha = _sha1(identity["head_sha"], field="producer_run.head_sha")
    run_id = _positive(identity["run_id"], field="producer_run.run_id")
    run_attempt = _positive(identity["run_attempt"], field="producer_run.run_attempt")
    workflow_id = _positive(run.get("workflow_id"), field="workflow_run.workflow_id")
    if (
        run.get("id") != run_id
        or run.get("run_attempt") != run_attempt
        or run.get("name") != PRODUCER_WORKFLOW_NAME
        or run.get("path") != PRODUCER_WORKFLOW
        or run.get("event") != PRODUCER_EVENT
        or run.get("head_sha") != head_sha
        or run.get("head_branch") != "main"
        or run.get("workflow_id") != workflow_id
        or not isinstance(run.get("repository"), Mapping)
        or run["repository"].get("full_name") != PRODUCER_REPOSITORY
    ):
        raise Stage2B1AcceptanceCommandError("producer_run_document_mismatch")
    return {"identity": identity, "workflow_id": workflow_id}


def _validate_artifact_metadata(
    artifact: Mapping[str, Any],
    *,
    run_identity: Mapping[str, Any],
) -> dict[str, str]:
    artifact_id = _text(str(artifact.get("id")), field="payload_artifact.id")
    if not artifact_id.isdigit() or artifact_id.startswith("0"):
        raise Stage2B1AcceptanceCommandError("payload_artifact.id_invalid")
    digest = _sha256(artifact.get("digest"), field="payload_artifact.digest")
    name = _text(artifact.get("name"), field="payload_artifact.name")
    expected_name = f"p4-8-evidence-payload-{run_identity['run_id']}-{run_identity['run_attempt']}"
    workflow_run = artifact.get("workflow_run")
    if (
        name != expected_name
        or artifact.get("expired") is not False
        or not isinstance(workflow_run, Mapping)
        or workflow_run.get("id") != run_identity["run_id"]
        or (
            workflow_run.get("run_attempt") is not None
            and workflow_run.get("run_attempt") != run_identity["run_attempt"]
        )
        or workflow_run.get("head_branch") != "main"
        or workflow_run.get("head_sha") != run_identity["head_sha"]
    ):
        raise Stage2B1AcceptanceCommandError("payload_artifact_identity_invalid")
    return {"id": artifact_id, "name": name, "digest": digest}


def _validate_policy(policy: Mapping[str, Any]) -> None:
    expected = {
        "schema",
        "platform_proof_required",
        "signer_workflow",
        "predicate_type",
        "subject_source",
        "self_generated_attestation_accepted",
    }
    if set(policy) != expected or policy["schema"] != PRODUCER_SCHEMA:
        raise Stage2B1AcceptanceCommandError("attestation_policy_invalid")
    if policy["platform_proof_required"] is not True or policy["self_generated_attestation_accepted"] is not False:
        raise Stage2B1AcceptanceCommandError("attestation_policy_not_fail_closed")
    if policy["signer_workflow"] != "toctionyan/fristTest/" + PRODUCER_WORKFLOW:
        raise Stage2B1AcceptanceCommandError("attestation_policy_workflow_invalid")
    if policy["predicate_type"] != "https://slsa.dev/provenance/v1":
        raise Stage2B1AcceptanceCommandError("attestation_policy_predicate_invalid")
    if policy["subject_source"] != "upload-artifact.artifact-digest":
        raise Stage2B1AcceptanceCommandError("attestation_policy_subject_invalid")


def _validate_producer_bundle(
    bundle_path: Path,
    *,
    trusted_baseline: BaselineDocument,
) -> tuple[dict[str, Any], VerifiedArtifactProvenance, dict[str, Any]]:
    _validate_bundle_files(bundle_path)
    producer_run = _load_json(bundle_path, "producer-run.json")
    workflow_run = _load_json(bundle_path, "workflow-run.json", canonical=False)
    run_info = _validate_run_document(workflow_run, producer_run)
    identity = run_info["identity"]
    binding = _closed(
        _load_json(bundle_path, "product-binding.json"),
        frozenset(COMMON_BINDING_FIELDS),
        field="product_binding",
    )
    if binding["stage_id"] != "stage2b1" or binding["accepted_state_id"] != "p4-8-evidence-produced":
        raise Stage2B1AcceptanceCommandError("product_binding_stage_invalid")
    _text(binding["product_source_ref"], field="product_binding.product_source_ref")
    _sha256(binding["protected_snapshot_digest"], field="product_binding.protected_snapshot_digest")
    control_plane_ref = _text(
        binding["control_plane_ref"], field="product_binding.control_plane_ref"
    )
    execution_repo_ref = _text(
        binding["execution_repo_ref"], field="product_binding.execution_repo_ref"
    )
    expected_execution_ref = "git-commit-sha1:" + identity["head_sha"]
    if control_plane_ref != expected_execution_ref:
        raise Stage2B1AcceptanceCommandError(
            "product_binding.control_plane_ref_not_server_owned"
        )
    if execution_repo_ref != expected_execution_ref:
        raise Stage2B1AcceptanceCommandError(
            "product_binding.execution_repo_ref_not_server_owned"
        )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        workflow_head_sha = _sha1(
            os.environ.get("GITHUB_SHA"), field="GITHUB_SHA"
        )
        expected_workflow_ref = "git-commit-sha1:" + workflow_head_sha
        if control_plane_ref != expected_workflow_ref:
            raise Stage2B1AcceptanceCommandError(
                "product_binding.control_plane_ref_not_acceptance_head"
            )
    if (
        binding["product_source_ref"] != trusted_baseline.product_source_ref
        or binding["protected_snapshot_digest"]
        != trusted_baseline.protected_snapshot_digest
    ):
        raise Stage2B1AcceptanceCommandError(
            "product_binding_does_not_match_trusted_baseline"
        )
    artifact_document = _load_json(bundle_path, "payload-artifact.json", canonical=False)
    artifact = _validate_artifact_metadata(artifact_document, run_identity=identity)
    provenance = _load_json(bundle_path, "provenance.json")
    content_digest = _sha256(provenance.get("artifact", {}).get("content_digest"), field="provenance.content_digest")
    expected_provenance = {
        # The reducer indexes a receipt by the exact artifact ID. Keep the
        # provenance proof and the P3 receipt on that same identity.
        "receipt_id": artifact["id"],
        "artifact_id": artifact["id"],
        "artifact_name": artifact["name"],
        "artifact_digest": artifact["digest"],
        "content_digest": content_digest,
        "repository": PRODUCER_REPOSITORY,
        "workflow_path": PRODUCER_WORKFLOW,
        "workflow_id": run_info["workflow_id"],
        "event": PRODUCER_EVENT,
        "ref": PRODUCER_REF,
        "head_sha": identity["head_sha"],
        "run_id": identity["run_id"],
        "run_attempt": identity["run_attempt"],
    }
    try:
        verified = verify_artifact_provenance(provenance, expected=expected_provenance)
    except (TypeError, ValueError) as exc:
        raise Stage2B1AcceptanceCommandError("producer_provenance_invalid") from exc
    policy = _load_json(bundle_path, "attestation-policy.json")
    _validate_policy(policy)
    manifest = _closed(
        _load_json(bundle_path, "manifest.json"),
        frozenset(
            {
                "schema",
                "status",
                "provenance",
                "provenance_observation",
                "attestation_verification_request",
                "artifact",
                "content_digest",
                "authority_effect",
                "repository_state_mutated",
            }
        ),
        field="manifest",
    )
    if (
        manifest["schema"] != PRODUCER_SCHEMA
        or manifest["status"] != "READY_FOR_EXTERNAL_VERIFICATION"
        or manifest["authority_effect"] is not False
        or manifest["repository_state_mutated"] is not False
        or manifest["provenance_observation"] != "provenance.json"
        or manifest["artifact"] != artifact
        or manifest["content_digest"] != content_digest
    ):
        raise Stage2B1AcceptanceCommandError("producer_manifest_invalid")
    request = _closed(
        manifest["attestation_verification_request"],
        frozenset(
            {
                "platform_proof_required",
                "repository",
                "signer_workflow",
                "subject_digest",
                "predicate_type",
                "source_digest",
                "source_ref",
                "self_generated_attestation_accepted",
            }
        ),
        field="manifest.attestation_verification_request",
    )
    expected_request = {
        "platform_proof_required": True,
        "repository": PRODUCER_REPOSITORY,
        "signer_workflow": "toctionyan/fristTest/" + PRODUCER_WORKFLOW,
        "subject_digest": artifact["digest"],
        "predicate_type": "https://slsa.dev/provenance/v1",
        "source_digest": identity["head_sha"],
        "source_ref": PRODUCER_REF,
        "self_generated_attestation_accepted": False,
    }
    if request != expected_request:
        raise Stage2B1AcceptanceCommandError("producer_attestation_request_invalid")
    return {
        "binding": binding,
        "artifact": artifact,
        "identity": identity,
        "workflow_id": run_info["workflow_id"],
        "content_digest": content_digest,
        "manifest": manifest,
    }, verified, artifact_document


def _expected_protected_approval(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return {field: payload[field] for field in _EXPECTED_APPROVAL_FIELDS}
    except KeyError as exc:
        raise Stage2B1AcceptanceCommandError(
            "protected_approval_expected_fields_incomplete"
        ) from exc


def _trusted_preview(
    *,
    metadata: Mapping[str, Any],
    provenance: VerifiedArtifactProvenance,
    source_proof: P48SourceArtifactProof,
    external_proof: ExternalIssuerProof,
) -> dict[str, Any]:
    """Build the trusted, read-only acceptance preview from verifier outputs."""

    binding = dict(metadata["binding"])
    artifact = dict(metadata["artifact"])
    identity = dict(metadata["identity"])
    artifact_id = artifact["id"]
    workflow_run_attempt = {
        "run_id": identity["run_id"],
        "attempt": identity["run_attempt"],
    }
    try:
        receipt = build_stage_evidence_receipt(
            **binding,
            workflow_run_attempt=workflow_run_attempt,
            artifact={"id": artifact_id, "digest": artifact["digest"]},
            result="PASS",
            producer="p4-8-evidence-producer",
            policy=RECEIPT_POLICY,
        )
        receipt = validate_stage_evidence_receipt(receipt)
    except (StageEvidenceReceiptError, TypeError, ValueError) as exc:
        raise Stage2B1AcceptanceCommandError("trusted_receipt_invalid") from exc

    receipt_id = artifact_id
    expected_receipt_bindings = {
        receipt_id: {
            "artifact": dict(receipt["artifact"]),
            "workflow_run_attempt": dict(receipt["workflow_run_attempt"]),
            "policy": receipt["policy"],
        }
    }
    approval_binding = {
        **binding,
        "evidence_bindings": [
            {
                "receipt_id": receipt_id,
                "artifact_id": artifact_id,
                "artifact_digest": artifact["digest"],
                "run_id": identity["run_id"],
                "run_attempt": identity["run_attempt"],
            }
        ],
    }
    try:
        protected_approval: Stage2B1ProtectedApprovalProof = (
            verify_stage2b1_protected_approval(binding=approval_binding)
        )
        approval_payload = protected_approval.as_dict()
    except (
        AttributeError,
        Stage2B1ProtectedHumanGateError,
        TypeError,
        ValueError,
    ) as exc:
        raise Stage2B1AcceptanceCommandError(
            "trusted_protected_approval_invalid"
        ) from exc

    expected_external = {
        "repository": PRODUCER_REPOSITORY,
        "signer_workflow": "toctionyan/fristTest/" + PRODUCER_WORKFLOW,
        "predicate_type": "https://slsa.dev/provenance/v1",
        "subject_digest": artifact["digest"],
        "source_digest": identity["head_sha"],
        "source_ref": PRODUCER_REF,
    }
    try:
        decision = reduce_trusted_stage_acceptance(
            [receipt],
            required_receipt_ids=[receipt_id],
            **binding,
            expected_receipt_bindings=expected_receipt_bindings,
            verified_provenance={receipt_id: provenance},
            verified_external_issuers={receipt_id: external_proof},
            expected_external_issuer_bindings={receipt_id: expected_external},
            verified_protected_approval=protected_approval,
            expected_protected_approval=_expected_protected_approval(
                approval_payload
            ),
            task_id=protected_approval.task_id,
        )
        decision_payload = validate_trusted_stage_acceptance_decision(decision)
    except (TypeError, ValueError, StageEvidenceReceiptError) as exc:
        raise Stage2B1AcceptanceCommandError("trusted_reducer_invalid") from exc
    if decision_payload["status"] != ACCEPTABLE_PREVIEW:
        reasons = ",".join(decision_payload.get("reasons", []))
        raise Stage2B1AcceptanceCommandError(
            "trusted_reducer_blocked" + (":" + reasons if reasons else "")
        )

    proof_refs = {
        provenance.proof_ref,
        source_proof.proof_ref,
        external_proof.proof_ref,
        "protected-approval:" + approval_payload["approval_sha256"],
    }
    return {
        "receipt": receipt,
        "protected_approval": approval_payload,
        "trusted_reducer": decision_payload,
        "proof_refs": sorted(proof_refs, key=lambda value: value.encode("utf-8")),
    }


def record_stage_acceptance(
    *,
    workspace: Path,
    producer_bundle_path: Path,
    artifact_archive_path: Path,
    source_run_id: int,
    source_run_attempt: int,
    artifact_id: str,
) -> dict[str, Any]:
    """Verify producer-owned evidence without writing governance state."""

    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise Stage2B1AcceptanceCommandError("workspace_missing")
    bundle = _bundle_dir(workspace, producer_bundle_path)
    try:
        trusted_baseline = load_baseline_document(workspace)
    except (OSError, UnicodeError, ProductSourcePolicyError) as exc:
        raise Stage2B1AcceptanceCommandError(
            "trusted_product_source_baseline_unavailable"
        ) from exc
    metadata, provenance, _ = _validate_producer_bundle(
        bundle,
        trusted_baseline=trusted_baseline,
    )
    identity = metadata["identity"]
    if (
        source_run_id != identity["run_id"]
        or source_run_attempt != identity["run_attempt"]
        or str(artifact_id) != metadata["artifact"]["id"]
    ):
        raise Stage2B1AcceptanceCommandError("explicit_source_selector_mismatch")
    archive = Path(artifact_archive_path)
    if archive.is_symlink() or not archive.is_file():
        raise Stage2B1AcceptanceCommandError("artifact_archive_missing_or_unsafe")
    try:
        archive.resolve().relative_to(workspace)
    except ValueError as exc:
        raise Stage2B1AcceptanceCommandError("artifact_archive_outside_workspace") from exc
    expected_digest = metadata["artifact"]["digest"]
    try:
        actual_digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    except OSError as exc:
        raise Stage2B1AcceptanceCommandError("artifact_archive_unreadable") from exc
    if actual_digest != expected_digest:
        raise Stage2B1AcceptanceCommandError("artifact_archive_digest_mismatch")
    try:
        source_proof: P48SourceArtifactProof = verify_p4_8_source_artifact(
            source_run_id=source_run_id,
            source_run_attempt=source_run_attempt,
            artifact_id=metadata["artifact"]["id"],
            expected_head_sha=identity["head_sha"],
            expected_artifact_digest=expected_digest,
        )
        external_proof: ExternalIssuerProof = verify_p4_8_payload_attestation(
            archive,
            source_run_id=source_proof.source_run_id,
            source_run_attempt=source_proof.source_run_attempt,
            source_digest=identity["head_sha"],
            subject_digest=expected_digest,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise Stage2B1AcceptanceCommandError("producer_external_proof_invalid") from exc
    if provenance.artifact_id != metadata["artifact"]["id"] or provenance.artifact_digest != expected_digest:
        raise Stage2B1AcceptanceCommandError("producer_provenance_artifact_mismatch")
    trusted = _trusted_preview(
        metadata=metadata,
        provenance=provenance,
        source_proof=source_proof,
        external_proof=external_proof,
    )
    return {
        "schema": VERIFICATION_SCHEMA,
        "status": "ACCEPTABLE_PREVIEW",
        "preview_kind": "p4-8-trusted-acceptance",
        "source_run": {
            "id": source_proof.source_run_id,
            "attempt": source_proof.source_run_attempt,
            "head_sha": source_proof.source_head_sha,
        },
        "artifact": metadata["artifact"],
        "product_binding": metadata["binding"],
        "proof_refs": sorted(
            set(trusted["proof_refs"]),
            key=lambda value: value.encode("utf-8"),
        ),
        "trusted_receipt": trusted["receipt"],
        "protected_approval": trusted["protected_approval"],
        "trusted_reducer": trusted["trusted_reducer"],
        "active_change_written": False,
        "task_run_written": False,
        "governance_state_changed": False,
        "authority_effect": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify P4.8 producer evidence without mutation.")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--producer-bundle", required=True, type=Path)
    parser.add_argument("--artifact-archive", required=True, type=Path)
    parser.add_argument("--source-run-id", required=True, type=int)
    parser.add_argument("--source-run-attempt", required=True, type=int)
    parser.add_argument("--artifact-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = record_stage_acceptance(
            workspace=args.workspace,
            producer_bundle_path=args.producer_bundle,
            artifact_archive_path=args.artifact_archive,
            source_run_id=args.source_run_id,
            source_run_attempt=args.source_run_attempt,
            artifact_id=args.artifact_id,
        )
    except (
        OSError,
        Stage2B1AcceptanceCommandError,
        Stage2B1ProtectedHumanGateError,
        ValueError,
        TypeError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema": VERIFICATION_SCHEMA,
                    "status": "BLOCKED",
                    "error": str(exc),
                    "active_change_written": False,
                    "task_run_written": False,
                    "governance_state_changed": False,
                    "authority_effect": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
