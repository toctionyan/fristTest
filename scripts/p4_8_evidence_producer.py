#!/usr/bin/env python3
"""Produce P4.8 evidence from one server-owned GitHub Actions run.

This module creates an unsigned observation package.  The only trusted
attestation is the GitHub platform attestation created by ``actions/attest``
in the workflow after ``upload-artifact`` returns its artifact digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping


CONTROLLER = Path(__file__).resolve().parents[1] / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))


SCHEMA = "p4-8-evidence-producer@2"
PROVENANCE_SCHEMA = "stage2b1-github-provenance@1"
REPOSITORY = "toctionyan/fristTest"
WORKFLOW_NAME = "p4-8-evidence-producer"
SIGNER_WORKFLOW = "toctionyan/fristTest/.github/workflows/p4-8-evidence-producer.yml"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
WORKFLOW_PATH = ".github/workflows/p4-8-evidence-producer.yml"
WORKFLOW_EVENT = "workflow_dispatch"
WORKFLOW_REF = "refs/heads/main"
WORKFLOW_BRANCH = "main"
PAYLOAD_FILES = ("producer-run.json", "product-binding.json", "attestation-policy.json")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class EvidenceProducerBlocked(ValueError):
    """Raised when a server-owned producer identity is not provable."""


def _canonical(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise EvidenceProducerBlocked("evidence_payload_not_canonicalizable") from exc
    if encoded.endswith(b"\n"):
        raise EvidenceProducerBlocked("evidence_payload_has_trailing_newline")
    return encoded


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _file_digest(path: Path, *, field: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise EvidenceProducerBlocked(f"{field}_missing_or_unsafe")
    try:
        return _bytes_digest(path.read_bytes())
    except OSError as exc:
        raise EvidenceProducerBlocked(f"{field}_unreadable") from exc


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceProducerBlocked(f"{field}_invalid")
    return value.strip()


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise EvidenceProducerBlocked(f"{field}_invalid")
    text = str(value or "").strip()
    if not text.isdigit() or text.startswith("0"):
        raise EvidenceProducerBlocked(f"{field}_invalid")
    return int(text)


def _required_env(environ: Mapping[str, str], field: str) -> str:
    return _text(environ.get(field), field=field)


def _run_gh_json(endpoint: str) -> object:
    """Read fixed producer metadata from GitHub, never from caller input."""

    if not endpoint.startswith(f"repos/{REPOSITORY}/"):
        raise EvidenceProducerBlocked("github_endpoint_not_producer_policy")
    try:
        completed = subprocess.run(
            ["gh", "api", endpoint],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvidenceProducerBlocked("github_metadata_unavailable") from exc
    if completed.returncode != 0:
        raise EvidenceProducerBlocked("github_metadata_request_failed")
    try:
        return json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise EvidenceProducerBlocked("github_metadata_invalid_json") from exc


def _server_run(environ: Mapping[str, str]) -> dict[str, Any]:
    run_id = _positive_int(environ.get("GITHUB_RUN_ID"), field="run_id")
    run_attempt = _positive_int(environ.get("GITHUB_RUN_ATTEMPT"), field="run_attempt")
    run = _run_gh_json(
        f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{run_attempt}"
    )
    if not isinstance(run, Mapping):
        raise EvidenceProducerBlocked("workflow_run_api_response_not_object")
    return dict(run)


def _identity(environ: Mapping[str, str], run: Mapping[str, Any]) -> dict[str, Any]:
    identity = {
        "repository": _required_env(environ, "GITHUB_REPOSITORY"),
        "workflow": _required_env(environ, "GITHUB_WORKFLOW"),
        "workflow_path": WORKFLOW_PATH,
        "event": _required_env(environ, "GITHUB_EVENT_NAME"),
        "ref": _required_env(environ, "GITHUB_REF"),
        "head_sha": _required_env(environ, "GITHUB_SHA").lower(),
        "run_id": _positive_int(environ.get("GITHUB_RUN_ID"), field="run_id"),
        "run_attempt": _positive_int(environ.get("GITHUB_RUN_ATTEMPT"), field="run_attempt"),
        "ref_protected": _required_env(environ, "GITHUB_REF_PROTECTED").lower(),
    }
    if _REPOSITORY.fullmatch(identity["repository"]) is None:
        raise EvidenceProducerBlocked("repository_invalid")
    if identity["repository"] != REPOSITORY:
        raise EvidenceProducerBlocked("repository_not_governed")
    if identity["workflow"] != WORKFLOW_NAME:
        raise EvidenceProducerBlocked("workflow_not_governed")
    if identity["event"] != WORKFLOW_EVENT:
        raise EvidenceProducerBlocked("event_must_be_workflow_dispatch")
    if identity["ref"] != WORKFLOW_REF:
        raise EvidenceProducerBlocked("ref_must_be_refs_heads_main")
    if identity["ref_protected"] != "true":
        raise EvidenceProducerBlocked("ref_must_be_protected")
    if _SHA1.fullmatch(identity["head_sha"]) is None:
        raise EvidenceProducerBlocked("head_sha_invalid")
    _verify_run(run, identity)
    return identity


def _verify_run(run: Mapping[str, Any], identity: Mapping[str, Any]) -> None:
    if not isinstance(run, Mapping):
        raise EvidenceProducerBlocked("workflow_run_api_response_not_object")
    if run.get("id") != identity["run_id"]:
        raise EvidenceProducerBlocked("workflow_run_id_mismatch")
    if run.get("run_attempt") != identity["run_attempt"]:
        raise EvidenceProducerBlocked("workflow_run_attempt_mismatch")
    if run.get("name") != WORKFLOW_NAME:
        raise EvidenceProducerBlocked("workflow_run_name_mismatch")
    if run.get("path") != WORKFLOW_PATH:
        raise EvidenceProducerBlocked("workflow_run_path_mismatch")
    workflow_id = run.get("workflow_id")
    if isinstance(workflow_id, bool) or not isinstance(workflow_id, int) or workflow_id < 1:
        raise EvidenceProducerBlocked("workflow_run_workflow_id_invalid")
    if run.get("event") != identity["event"]:
        raise EvidenceProducerBlocked("workflow_run_event_mismatch")
    if run.get("head_sha") != identity["head_sha"]:
        raise EvidenceProducerBlocked("workflow_run_head_sha_mismatch")
    if run.get("head_branch") != WORKFLOW_BRANCH:
        raise EvidenceProducerBlocked("workflow_run_head_branch_mismatch")
    if run.get("status") not in {"queued", "in_progress"}:
        raise EvidenceProducerBlocked("workflow_run_not_in_progress")
    repository = run.get("repository")
    if not isinstance(repository, Mapping) or repository.get("full_name") != REPOSITORY:
        raise EvidenceProducerBlocked("workflow_run_repository_mismatch")


def load_run_identity(environ: Mapping[str, str], run: Mapping[str, Any]) -> dict[str, Any]:
    """Validate Actions context against the exact server-side run response."""

    return _identity(environ, run)


def _load_object(path: Path, *, field: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise EvidenceProducerBlocked(f"{field}_missing_or_unsafe")
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceProducerBlocked(f"{field}_invalid_json") from exc
    if not isinstance(value, dict):
        raise EvidenceProducerBlocked(f"{field}_not_object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(dict(value)))


def _binding(workspace: Path, identity: Mapping[str, Any]) -> dict[str, str]:
    baseline = _load_object(
        workspace / "skill-system/registry/product-source-baseline.json",
        field="product_source_baseline",
    )
    try:
        from product_source_baseline_policy import validate_baseline_document
        baseline_errors = validate_baseline_document(baseline)
    except (ImportError, TypeError, ValueError) as exc:
        raise EvidenceProducerBlocked("product_source_baseline_validation_unavailable") from exc
    if baseline_errors:
        raise EvidenceProducerBlocked(
            "product_source_baseline_invalid:" + ",".join(baseline_errors)
        )
    source_ref = baseline.get("product_source_ref")
    snapshot = baseline.get("protected_snapshot_digest")
    if not isinstance(source_ref, str) or not source_ref.startswith("git-commit-sha1:"):
        raise EvidenceProducerBlocked("product_source_ref_invalid")
    if _SHA1.fullmatch(source_ref.removeprefix("git-commit-sha1:")) is None:
        raise EvidenceProducerBlocked("product_source_ref_invalid")
    if not isinstance(snapshot, str) or _SHA256.fullmatch(snapshot) is None:
        raise EvidenceProducerBlocked("protected_snapshot_digest_invalid")
    control_plane_ref = "git-commit-sha1:" + identity["head_sha"]
    return {
        "stage_id": "stage2b1",
        "accepted_state_id": "p4-8-evidence-produced",
        "product_source_ref": source_ref,
        "protected_snapshot_digest": snapshot,
        "control_plane_ref": control_plane_ref,
        "execution_repo_ref": control_plane_ref,
    }


def _payload(*, identity: Mapping[str, Any], binding: Mapping[str, str]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "producer": "github-actions-server-owned-p4-8",
        "policy": "platform-attestation-required",
        "run": dict(identity),
        "binding": dict(binding),
    }


def _produce_payload_from_run(
    *,
    workspace: Path,
    output: Path,
    environ: Mapping[str, str],
    run_document: Mapping[str, Any],
) -> dict[str, Any]:
    """Create only unsigned inputs for the attested artifact."""

    identity = load_run_identity(environ, run_document)
    workspace, output = workspace.resolve(), output.resolve()
    if not workspace.is_dir():
        raise EvidenceProducerBlocked("producer_workspace_invalid")
    if output == workspace or output.exists() and not output.is_dir():
        raise EvidenceProducerBlocked("producer_output_directory_invalid")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise EvidenceProducerBlocked("producer_output_directory_must_be_empty")
    binding = _binding(workspace, identity)
    _write_json(output / "producer-run.json", {"schema": SCHEMA, "run": identity})
    _write_json(output / "product-binding.json", binding)
    _write_json(
        output / "attestation-policy.json",
        {
            "schema": SCHEMA,
            "platform_proof_required": True,
            "signer_workflow": SIGNER_WORKFLOW,
            "predicate_type": PREDICATE_TYPE,
            "subject_source": "upload-artifact.artifact-digest",
            "self_generated_attestation_accepted": False,
        },
    )
    return {
        "schema": SCHEMA,
        "status": "READY_FOR_EXTERNAL_VERIFICATION",
        "unsigned_payload": True,
        "run": identity,
        "binding": binding,
        "files": list(PAYLOAD_FILES),
    }


def produce_payload(
    *,
    workspace: Path,
    output: Path,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    """Produce payload after resolving the run from the GitHub API."""

    return _produce_payload_from_run(
        workspace=workspace,
        output=output,
        environ=environ,
        run_document=_server_run(environ),
    )


def _artifact(artifact: Mapping[str, Any], identity: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(artifact, Mapping):
        raise EvidenceProducerBlocked("artifact_api_response_not_object")
    artifact_id = _positive_int(artifact.get("id"), field="artifact_id")
    name = _text(artifact.get("name"), field="artifact_name")
    digest = _text(artifact.get("digest"), field="artifact_digest")
    if _SHA256.fullmatch(digest) is None:
        raise EvidenceProducerBlocked("artifact_digest_invalid")
    expected_name = f"p4-8-evidence-payload-{identity['run_id']}-{identity['run_attempt']}"
    if name != expected_name:
        raise EvidenceProducerBlocked("artifact_name_mismatch")
    if artifact.get("expired") is not False:
        raise EvidenceProducerBlocked("artifact_expired_or_unknown")
    workflow_run = artifact.get("workflow_run")
    if (
        not isinstance(workflow_run, Mapping)
        or workflow_run.get("id") != identity["run_id"]
        or workflow_run.get("head_branch") != WORKFLOW_BRANCH
        or workflow_run.get("head_sha") != identity["head_sha"]
    ):
        raise EvidenceProducerBlocked("artifact_workflow_run_mismatch")
    # GitHub's artifact REST response may omit run_attempt. The artifact name
    # is server-generated from the exact run ID and attempt, so an omitted
    # field is acceptable; a present field must still match.
    if (
        workflow_run.get("run_attempt") is not None
        and workflow_run.get("run_attempt") != identity["run_attempt"]
    ):
        raise EvidenceProducerBlocked("artifact_workflow_run_mismatch")
    return {"id": str(artifact_id), "name": name, "digest": digest}


def _server_artifact(*, identity: Mapping[str, Any], artifact_id: str) -> dict[str, Any]:
    """Read and uniquely bind one artifact selected by the upload step."""

    requested_id = _positive_int(artifact_id, field="artifact_id")
    artifact = _run_gh_json(f"repos/{REPOSITORY}/actions/artifacts/{requested_id}")
    if not isinstance(artifact, Mapping):
        raise EvidenceProducerBlocked("artifact_api_response_not_object")
    selected = _artifact(artifact, identity)

    matches: list[Mapping[str, Any]] = []
    expected_name = selected["name"]
    page = 1
    while True:
        listing = _run_gh_json(
            f"repos/{REPOSITORY}/actions/runs/{identity['run_id']}/artifacts"
            f"?per_page=100&page={page}"
        )
        if not isinstance(listing, Mapping):
            raise EvidenceProducerBlocked("artifact_list_api_response_not_object")
        rows = listing.get("artifacts")
        total_count = listing.get("total_count")
        if (
            isinstance(total_count, bool)
            or not isinstance(total_count, int)
            or total_count < 0
            or not isinstance(rows, list)
        ):
            raise EvidenceProducerBlocked("artifact_list_response_invalid")
        matches.extend(
            row for row in rows
            if isinstance(row, Mapping) and row.get("name") == expected_name
        )
        if len(rows) > total_count:
            raise EvidenceProducerBlocked("artifact_list_response_invalid")
        if page * 100 < total_count:
            # A short page before the advertised final page cannot prove that
            # no duplicate artifact name exists later in the run.
            if len(rows) != 100:
                raise EvidenceProducerBlocked("artifact_list_pagination_gap")
            page += 1
            if page > 100:
                raise EvidenceProducerBlocked("artifact_list_pagination_limit")
            continue
        break

    if len(matches) != 1:
        raise EvidenceProducerBlocked("artifact_name_not_unique_for_run")
    listed = _artifact(matches[0], identity)
    if listed != selected or listed["id"] != str(requested_id):
        raise EvidenceProducerBlocked("artifact_metadata_duplicate_or_mismatch")
    normalized = dict(artifact)
    normalized.update(
        {
            "id": int(selected["id"]),
            "name": selected["name"],
            "digest": selected["digest"],
            "expired": False,
            "workflow_run": {
                "id": identity["run_id"],
                "run_attempt": identity["run_attempt"],
                "head_branch": WORKFLOW_BRANCH,
                "head_sha": identity["head_sha"],
            },
        }
    )
    return normalized


def _content_digest(payload: Path) -> str:
    entries: list[dict[str, str]] = []
    for name in PAYLOAD_FILES:
        path = payload / name
        if path.is_symlink() or not path.is_file():
            raise EvidenceProducerBlocked(f"payload_file_unsafe:{name}")
        entries.append({"path": name, "digest": _bytes_digest(path.read_bytes())})
    return _digest(entries)


def _validate_payload_files(payload: Path) -> None:
    if sorted(path.name for path in payload.iterdir()) != sorted(PAYLOAD_FILES):
        raise EvidenceProducerBlocked("payload_must_contain_exactly_expected_files")
    for filename in PAYLOAD_FILES:
        path = payload / filename
        if path.is_symlink() or not path.is_file():
            raise EvidenceProducerBlocked(f"payload_file_unsafe:{filename}")


def _finalize_bundle_from_metadata(
    *,
    payload: Path,
    output: Path,
    run_document: Mapping[str, Any],
    artifact_document: Mapping[str, Any],
    environ: Mapping[str, str],
    upload_artifact_digest: str,
    downloaded_artifact: Path,
) -> dict[str, Any]:
    """Bind the local observation to the API artifact metadata.

    This does not verify the platform attestation.  The verifier later runs
    ``gh attestation verify`` against the uploaded payload artifact.
    """

    identity = load_run_identity(environ, run_document)
    artifact = _artifact(artifact_document, identity)
    if upload_artifact_digest != artifact["digest"]:
        raise EvidenceProducerBlocked("upload_artifact_digest_mismatch")
    downloaded_digest = _file_digest(
        Path(downloaded_artifact), field="downloaded_artifact"
    )
    if downloaded_digest != artifact["digest"]:
        raise EvidenceProducerBlocked("downloaded_artifact_digest_mismatch")
    payload, output = payload.resolve(), output.resolve()
    if not payload.is_dir() or output == payload or output.exists() and not output.is_dir():
        raise EvidenceProducerBlocked("bundle_directory_invalid")
    _validate_payload_files(payload)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise EvidenceProducerBlocked("bundle_output_directory_must_be_empty")
    for filename in PAYLOAD_FILES:
        source = payload / filename
        if source.is_symlink() or not source.is_file():
            raise EvidenceProducerBlocked(f"payload_file_unsafe:{filename}")
        shutil.copyfile(source, output / filename)
    content_digest = _content_digest(payload)
    provenance = {
        "schema": PROVENANCE_SCHEMA,
        "repository": identity["repository"],
        "workflow_path": identity["workflow_path"],
        "workflow_id": int(run_document["workflow_id"]),
        "event": identity["event"],
        "ref": identity["ref"],
        "head_sha": identity["head_sha"],
        "run_id": identity["run_id"],
        "run_attempt": identity["run_attempt"],
        "artifact": {
            "id": artifact["id"],
            "name": artifact["name"],
            "digest": artifact["digest"],
            "archive_digest": downloaded_digest,
            "content_digest": content_digest,
            "source_run_id": identity["run_id"],
            "source_run_attempt": identity["run_attempt"],
        },
    }
    _write_json(output / "provenance.json", provenance)
    _write_json(output / "workflow-run.json", dict(run_document))
    _write_json(output / "payload-artifact.json", dict(artifact_document))
    manifest = {
        "schema": SCHEMA,
        "status": "READY_FOR_EXTERNAL_VERIFICATION",
        "provenance": {"run": dict(identity), "artifact": artifact},
        "provenance_observation": "provenance.json",
        "attestation_verification_request": {
            "platform_proof_required": True,
            "repository": identity["repository"],
            "signer_workflow": SIGNER_WORKFLOW,
            "subject_digest": artifact["digest"],
            "predicate_type": PREDICATE_TYPE,
            "source_digest": identity["head_sha"],
            "source_ref": identity["ref"],
            "self_generated_attestation_accepted": False,
        },
        "artifact": artifact,
        "content_digest": content_digest,
        "authority_effect": False,
        "repository_state_mutated": False,
    }
    _write_json(output / "manifest.json", manifest)
    return manifest


def finalize_bundle(
    *,
    payload: Path,
    output: Path,
    environ: Mapping[str, str],
    artifact_id: str,
    upload_artifact_digest: str,
    downloaded_artifact: Path,
) -> dict[str, Any]:
    """Finalize a bundle from server-owned run and artifact metadata."""

    run_document = _server_run(environ)
    identity = load_run_identity(environ, run_document)
    artifact_document = _server_artifact(
        identity=identity,
        artifact_id=artifact_id,
    )
    return _finalize_bundle_from_metadata(
        payload=payload,
        output=output,
        run_document=run_document,
        artifact_document=artifact_document,
        environ=environ,
        upload_artifact_digest=upload_artifact_digest,
        downloaded_artifact=downloaded_artifact,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Produce server-owned P4.8 evidence inputs.")
    parser.add_argument("--phase", choices=("payload", "finalize"), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-id")
    parser.add_argument("--upload-artifact-digest")
    parser.add_argument("--downloaded-artifact", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise EvidenceProducerBlocked("producer_must_run_in_github_actions")
        if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
            raise EvidenceProducerBlocked("producer_github_token_missing")
        if args.phase == "payload":
            result = produce_payload(
                workspace=args.workspace,
                output=args.output,
                environ=os.environ,
            )
        else:
            if (
                not args.artifact_id
                or not args.upload_artifact_digest
                or args.downloaded_artifact is None
            ):
                raise EvidenceProducerBlocked(
                    "artifact_id_upload_digest_and_download_required"
                )
            result = finalize_bundle(
                payload=args.workspace,
                output=args.output,
                environ=os.environ,
                artifact_id=args.artifact_id,
                upload_artifact_digest=args.upload_artifact_digest,
                downloaded_artifact=args.downloaded_artifact,
            )
    except (EvidenceProducerBlocked, OSError, ValueError) as exc:
        print(
            json.dumps({"schema": SCHEMA, "status": "BLOCKED", "error": str(exc)},
                       ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
