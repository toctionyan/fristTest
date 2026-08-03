#!/usr/bin/env python3
"""Consume one protected production release result and exact artifact set."""
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

CONTRACT = "production-closure-consumption@1"
RESULT_CONTRACT = "production-release-execution@2"
REQUIRED_KINDS = {"protected-source", "quality-evidence", "source-sha256-sidecar"}


class ClosureArtifactError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClosureArtifactError("json_invalid", f"invalid JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ClosureArtifactError("json_not_object", f"JSON must be object: {path.name}")
    return payload


def _safe_zip(path: Path) -> list[str]:
    if not zipfile.is_zipfile(path):
        raise ClosureArtifactError("artifact_not_zip", f"artifact is not ZIP: {path.name}")
    names: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename
            pure = PurePosixPath(name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if pure.is_absolute() or ".." in pure.parts or stat.S_ISLNK(mode):
                raise ClosureArtifactError("artifact_unsafe_entry", f"unsafe ZIP entry: {name}")
            names.append(name)
    return names


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _validate_toolchain_evidence(path: Path, *, repository: str, commit_sha: str, run_id: str, run_attempt: str) -> dict[str, Any]:
    payload = _json(path)
    if payload.get("contract") != "release-toolchain-provenance@1" or payload.get("status") != "PASS":
        raise ClosureArtifactError("toolchain_evidence_invalid", "release toolchain evidence is not a PASS contract")
    fingerprint = str(payload.get("toolchain_fingerprint_sha256") or "").casefold()
    unsigned = dict(payload)
    unsigned.pop("toolchain_fingerprint_sha256", None)
    if len(fingerprint) != 64 or _canonical_sha256(unsigned) != fingerprint:
        raise ClosureArtifactError("toolchain_fingerprint_invalid", "release toolchain fingerprint is invalid")
    identity = payload.get("ci_run_identity")
    if not isinstance(identity, Mapping) or identity.get("contract") != "release-run-identity@1" or identity.get("status") != "PASS":
        raise ClosureArtifactError("run_identity_invalid", "protected run identity contract is invalid")
    identity_fingerprint = str(identity.get("run_identity_fingerprint_sha256") or "").casefold()
    identity_unsigned = dict(identity)
    identity_unsigned.pop("run_identity_fingerprint_sha256", None)
    if len(identity_fingerprint) != 64 or _canonical_sha256(identity_unsigned) != identity_fingerprint:
        raise ClosureArtifactError("run_identity_fingerprint_invalid", "protected run identity fingerprint is invalid")
    expected = {
        "repository": repository,
        "commit_sha": commit_sha.casefold(),
        "run_id": str(run_id),
        "run_attempt": str(run_attempt),
        "workflow": "production-certification-release",
        "workflow_file": ".github/workflows/release.yml",
        "job": "protected-release",
        "git_ref": "refs/heads/main",
        "ref_protected": True,
    }
    for key, value in expected.items():
        if identity.get(key) != value:
            raise ClosureArtifactError(f"run_identity_mismatch:{key}", f"protected run identity mismatch: {key}")
    return payload


def verify(
    result_path: Path,
    artifact_dir: Path,
    *,
    toolchain_evidence_path: Path,
    expected_repository: str,
    expected_commit: str,
    expected_run_id: str,
    expected_run_attempt: str,
) -> dict[str, Any]:
    result = _json(Path(result_path))
    directory = Path(artifact_dir).resolve()
    if result.get("contract") != RESULT_CONTRACT or result.get("status") != "PASS" or result.get("stage") != "closed":
        raise ClosureArtifactError("release_result_not_closed", "production release result is not a closed PASS")
    if result.get("reason") != "production_release_closed" or result.get("authority_gate") != "production-certification-bundle":
        raise ClosureArtifactError("release_authority_invalid", "production release authority is invalid")
    toolchain = _validate_toolchain_evidence(
        Path(toolchain_evidence_path), repository=expected_repository, commit_sha=expected_commit,
        run_id=expected_run_id, run_attempt=expected_run_attempt,
    )
    run_identity = toolchain["ci_run_identity"]
    identity = result.get("identity")
    if not isinstance(identity, Mapping):
        raise ClosureArtifactError("release_identity_missing", "production release result has no identity")
    expected_result_identity = {
        "repository": expected_repository,
        "commit_sha": expected_commit.casefold(),
        "workflow_run_id": str(expected_run_id),
        "workflow_run_attempt": str(expected_run_attempt),
        "git_ref": "refs/heads/main",
        "run_identity_fingerprint_sha256": run_identity["run_identity_fingerprint_sha256"],
        "toolchain_fingerprint_sha256": toolchain["toolchain_fingerprint_sha256"],
    }
    for key, value in expected_result_identity.items():
        if identity.get(key) != value:
            raise ClosureArtifactError(f"release_identity_mismatch:{key}", f"release identity mismatch: {key}")

    rows = result.get("artifacts")
    if not isinstance(rows, list) or {str(item.get("kind")) for item in rows if isinstance(item, dict)} != REQUIRED_KINDS:
        raise ClosureArtifactError("artifact_manifest_invalid", "artifact manifest is not exact")
    expected_names = {str(item.get("filename")) for item in rows if isinstance(item, dict)}
    actual_entries = list(directory.iterdir()) if directory.is_dir() else []
    if any(path.is_symlink() or not path.is_file() for path in actual_entries):
        raise ClosureArtifactError("artifact_directory_unsafe", "artifact directory contains unsafe entries")
    if {path.name for path in actual_entries} != expected_names:
        raise ClosureArtifactError("artifact_set_mismatch", "artifact directory does not match release result")

    by_kind = {str(item["kind"]): item for item in rows}
    for row in rows:
        path = directory / str(row["filename"])
        if _sha256(path) != str(row.get("sha256") or "").casefold() or path.stat().st_size != int(row.get("size_bytes") or -1):
            raise ClosureArtifactError("artifact_digest_mismatch", f"artifact digest mismatch: {path.name}")

    source = directory / str(by_kind["protected-source"]["filename"])
    evidence = directory / str(by_kind["quality-evidence"]["filename"])
    sidecar = directory / str(by_kind["source-sha256-sidecar"]["filename"])
    source_names = _safe_zip(source)
    evidence_names = _safe_zip(evidence)
    if not any(name.endswith("/VERSION") for name in source_names):
        raise ClosureArtifactError("source_version_missing", "protected source archive has no VERSION")
    if "quality-evidence/run-summary.json" not in evidence_names:
        raise ClosureArtifactError("quality_summary_missing", "quality evidence archive has no run-summary.json")
    with zipfile.ZipFile(evidence) as archive:
        summary = json.loads(archive.read("quality-evidence/run-summary.json"))
    if summary.get("decision") != "PASS" or summary.get("loop_status") not in {"CI_VERIFIED", "CONVERGED"}:
        raise ClosureArtifactError("quality_summary_not_closed", "quality summary is not closed")
    if str(summary.get("ci_run_identity_fingerprint_sha256") or "") != str(run_identity.get("run_identity_fingerprint_sha256") or ""):
        raise ClosureArtifactError("quality_run_identity_mismatch", "quality evidence belongs to another run")

    try:
        digest, filename = sidecar.read_text(encoding="utf-8").strip().split("  ", 1)
    except (OSError, ValueError) as exc:
        raise ClosureArtifactError("source_sidecar_invalid", "source SHA256 sidecar is invalid") from exc
    if filename != source.name or digest.casefold() != _sha256(source):
        raise ClosureArtifactError("source_sidecar_mismatch", "source SHA256 sidecar does not match")

    return {
        "contract": CONTRACT,
        "status": "PASS",
        "reason": "production_closure_artifacts_verified",
        "repository": expected_repository,
        "commit_sha": expected_commit.casefold(),
        "run_id": str(expected_run_id),
        "run_attempt": str(expected_run_attempt),
        "run_identity_fingerprint_sha256": run_identity["run_identity_fingerprint_sha256"],
        "toolchain_fingerprint_sha256": toolchain["toolchain_fingerprint_sha256"],
        "artifacts": rows,
        "production_closed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--toolchain-evidence", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        payload = verify(
            Path(args.result), Path(args.artifact_dir), toolchain_evidence_path=Path(args.toolchain_evidence), expected_repository=args.repository,
            expected_commit=args.commit, expected_run_id=args.run_id, expected_run_attempt=args.run_attempt,
        )
        code = 0
    except ClosureArtifactError as exc:
        payload = {"contract": CONTRACT, "status": "FAIL", "reason": exc.code, "error": str(exc), "production_closed": False}
        code = 1
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).resolve(); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
