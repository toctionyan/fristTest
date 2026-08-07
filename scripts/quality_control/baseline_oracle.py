from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ORACLE_SCHEMA_VERSION = 1
EXECUTION_MODE = "ephemeral_overlay_view"
ORACLE_ROOT = Path(".quality/baseline-oracles")
_RUNTIME_LINK_NAMES = {".venv", "node_modules"}
_COPY_IGNORE_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "coverage",
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _safe_relative_path(raw: object, *, field: str) -> Path:
    text = str(raw or "").strip()
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"baseline oracle {field} must be a safe workspace-relative path")
    return path


def _under_oracle_root(path: Path) -> bool:
    return path == ORACLE_ROOT or ORACLE_ROOT in path.parents


def _is_acceptance_test_path(path: Path) -> bool:
    parts = path.parts
    return "tests" in parts and path.suffix.lower() in {".py", ".json", ".yaml", ".yml"}


def _git_head(workspace: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    value = completed.stdout.strip().casefold()
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("baseline oracle requires a real git checkout with an exact 40-hex HEAD")
    return value


def _validate_zip_member(info: zipfile.ZipInfo, expected_path: str) -> None:
    member = Path(info.filename)
    if info.is_dir() or member.is_absolute() or ".." in member.parts:
        raise ValueError(f"baseline oracle artifact contains unsafe member: {info.filename}")
    if member.as_posix() != expected_path:
        raise ValueError(
            f"baseline oracle artifact member path mismatch: expected {expected_path}, got {info.filename}"
        )
    unix_mode = (info.external_attr >> 16) & 0o170000
    if unix_mode == stat.S_IFLNK:
        raise ValueError(f"baseline oracle artifact cannot contain symlinks: {info.filename}")


def _copy_execution_workspace(source: Path, destination: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        directory_path = Path(directory)
        try:
            relative = directory_path.resolve().relative_to(source.resolve())
        except ValueError:
            relative = Path()
        ignored = {name for name in names if name in _COPY_IGNORE_NAMES}
        if relative == Path() and ".quality" in names:
            ignored.add(".quality")
        return ignored

    shutil.copytree(source, destination, symlinks=True, ignore=ignore)

    # Installed dependency trees are runtime inputs, not governed source identity.
    # Link them into the isolated execution view instead of duplicating gigabytes.
    for root, directories, _files in os.walk(source):
        root_path = Path(root)
        relative_root = root_path.relative_to(source)
        selected = [name for name in directories if name in _RUNTIME_LINK_NAMES]
        for name in selected:
            src = root_path / name
            dst = destination / relative_root / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() and not dst.is_symlink():
                os.symlink(src, dst, target_is_directory=True)
        directories[:] = [
            name
            for name in directories
            if name not in _RUNTIME_LINK_NAMES
            and name not in {".git", "__pycache__", ".pytest_cache", "coverage", ".quality"}
        ]

    # Some verification helpers inspect git metadata. A readonly baseline run may
    # observe the source checkout's metadata, while all regular files remain copies.
    git_entry = source / ".git"
    if git_entry.exists() and not (destination / ".git").exists():
        os.symlink(git_entry, destination / ".git", target_is_directory=git_entry.is_dir())


class BaselineOracleSession:
    def __init__(self, temporary: tempfile.TemporaryDirectory[str], execution_workspace: Path, identity: dict[str, Any]):
        self._temporary = temporary
        self.execution_workspace = execution_workspace
        self.identity = identity
        self._closed = False

    def cleanup(self) -> None:
        if not self._closed:
            self._temporary.cleanup()
            self._closed = True

    def __del__(self) -> None:  # pragma: no cover - defensive cleanup on exceptional exits
        try:
            self.cleanup()
        except Exception:
            pass


def materialize_baseline_oracle(
    source_workspace: Path,
    manifest_path: Path,
    *,
    source_snapshot: dict[str, Any],
) -> BaselineOracleSession:
    source_workspace = source_workspace.resolve()
    manifest_path = manifest_path.resolve()
    try:
        manifest_relative = manifest_path.relative_to(source_workspace)
    except ValueError as exc:
        raise ValueError("baseline oracle manifest must live inside the workspace") from exc
    if not _under_oracle_root(manifest_relative):
        raise ValueError("baseline oracle manifest must live under .quality/baseline-oracles")
    if not manifest_path.is_file():
        raise ValueError("baseline oracle manifest does not exist")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("baseline oracle manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("baseline oracle manifest must be a JSON object")
    if manifest.get("schema_version") != ORACLE_SCHEMA_VERSION:
        raise ValueError(f"baseline oracle schema_version must be {ORACLE_SCHEMA_VERSION}")

    oracle_id = str(manifest.get("oracle_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", oracle_id):
        raise ValueError("baseline oracle oracle_id is invalid")
    if manifest.get("execution_mode") != EXECUTION_MODE:
        raise ValueError(f"baseline oracle execution_mode must be {EXECUTION_MODE}")

    base_source_identity = str(manifest.get("base_source_identity") or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{40}", base_source_identity):
        raise ValueError("baseline oracle base_source_identity must be an exact 40-hex git commit")
    if _git_head(source_workspace) != base_source_identity:
        raise ValueError("baseline oracle base_source_identity does not match workspace HEAD")

    source_fingerprint = str(source_snapshot.get("fingerprint") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", source_fingerprint):
        raise ValueError("baseline oracle source snapshot fingerprint is invalid")
    if str(manifest.get("base_workspace_fingerprint") or "").casefold() != source_fingerprint:
        raise ValueError("baseline oracle base_workspace_fingerprint does not match source workspace")
    snapshot_files = source_snapshot.get("files")
    if not isinstance(snapshot_files, dict):
        raise ValueError("baseline oracle source snapshot files are invalid")

    artifact_relative = _safe_relative_path(manifest.get("overlay_artifact"), field="overlay_artifact")
    if not _under_oracle_root(artifact_relative):
        raise ValueError("baseline oracle overlay_artifact must live under .quality/baseline-oracles")
    artifact_path = (source_workspace / artifact_relative).resolve()
    try:
        artifact_path.relative_to(source_workspace)
    except ValueError as exc:
        raise ValueError("baseline oracle overlay_artifact escapes workspace") from exc
    if not artifact_path.is_file():
        raise ValueError("baseline oracle overlay_artifact does not exist")
    artifact_sha = _sha256_file(artifact_path)
    declared_artifact_sha = str(manifest.get("overlay_artifact_sha256") or "").casefold()
    if declared_artifact_sha != artifact_sha:
        raise ValueError("baseline oracle overlay_artifact_sha256 does not match artifact bytes")

    raw_map = manifest.get("overlay_file_map")
    if not isinstance(raw_map, list) or not raw_map:
        raise ValueError("baseline oracle overlay_file_map must be non-empty")
    overlay_file_map: list[dict[str, str]] = []
    paths: set[str] = set()
    for index, raw in enumerate(raw_map, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"baseline oracle overlay_file_map #{index} must be an object")
        relative = _safe_relative_path(raw.get("path"), field=f"overlay_file_map[{index}].path")
        path_text = relative.as_posix()
        if path_text in paths:
            raise ValueError(f"baseline oracle overlay path is duplicated: {path_text}")
        if not _is_acceptance_test_path(relative):
            raise ValueError(f"baseline oracle may overlay acceptance-test files only: {path_text}")
        base_sha = str(raw.get("base_file_sha256") or "").casefold()
        overlay_sha = str(raw.get("overlay_file_sha256") or "").casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", base_sha):
            raise ValueError(f"baseline oracle base_file_sha256 is invalid for {path_text}")
        if not re.fullmatch(r"[0-9a-f]{64}", overlay_sha):
            raise ValueError(f"baseline oracle overlay_file_sha256 is invalid for {path_text}")
        if str(snapshot_files.get(path_text) or "").casefold() != base_sha:
            raise ValueError(f"baseline oracle base file is not bound to source snapshot: {path_text}")
        physical = source_workspace / relative
        if not physical.is_file() or _sha256_file(physical) != base_sha:
            raise ValueError(f"baseline oracle base file bytes do not match source snapshot: {path_text}")
        paths.add(path_text)
        overlay_file_map.append(
            {"path": path_text, "base_file_sha256": base_sha, "overlay_file_sha256": overlay_sha}
        )

    raw_bindings = manifest.get("claim_bindings")
    if not isinstance(raw_bindings, list) or not raw_bindings:
        raise ValueError("baseline oracle claim_bindings must be non-empty")
    claim_bindings: list[dict[str, str]] = []
    seen_claims: set[str] = set()
    bound_paths: set[str] = set()
    for index, raw in enumerate(raw_bindings, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"baseline oracle claim_bindings #{index} must be an object")
        claim_id = str(raw.get("claim_id") or "").strip()
        selector = str(raw.get("selector") or "").strip()
        if not claim_id or claim_id in seen_claims:
            raise ValueError("baseline oracle claim_bindings claim_id must be unique and non-empty")
        path_text, separator, test_selector = selector.partition("::")
        relative = _safe_relative_path(path_text, field=f"claim_bindings[{index}].selector")
        if not separator or not test_selector.strip():
            raise ValueError("baseline oracle claim binding selector must use path::test_selector")
        if relative.as_posix() not in paths:
            raise ValueError("baseline oracle claim binding must reference an overlay file")
        seen_claims.add(claim_id)
        bound_paths.add(relative.as_posix())
        claim_bindings.append({"claim_id": claim_id, "selector": f"{relative.as_posix()}::{test_selector.strip()}"})
    if bound_paths != paths:
        raise ValueError("every baseline oracle overlay file must be directly bound to an acceptance claim")

    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("baseline oracle provenance must be an object")
    required_provenance = ("repository", "run_id", "artifact_id", "artifact_digest")
    missing = [field for field in required_provenance if not str(provenance.get(field) or "").strip()]
    if missing:
        raise ValueError("baseline oracle provenance is incomplete: " + ", ".join(missing))

    identity: dict[str, Any] = {
        "schema_version": ORACLE_SCHEMA_VERSION,
        "oracle_id": oracle_id,
        "base_source_identity": base_source_identity,
        "base_workspace_fingerprint": source_fingerprint,
        "overlay_artifact_sha256": artifact_sha,
        "overlay_file_map": overlay_file_map,
        "claim_bindings": claim_bindings,
        "provenance": provenance,
        "execution_mode": EXECUTION_MODE,
    }
    canonical_fingerprint = _canonical_fingerprint(identity)
    if str(manifest.get("canonical_fingerprint") or "").casefold() != canonical_fingerprint:
        raise ValueError("baseline oracle canonical_fingerprint does not match canonical identity")
    identity["canonical_fingerprint"] = canonical_fingerprint

    try:
        archive = zipfile.ZipFile(artifact_path)
    except zipfile.BadZipFile as exc:
        raise ValueError("baseline oracle overlay_artifact must be a valid ZIP archive") from exc
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) != len(overlay_file_map):
            raise ValueError("baseline oracle artifact file set does not match overlay_file_map")
        info_by_name = {Path(info.filename).as_posix(): info for info in infos}
        if set(info_by_name) != paths:
            raise ValueError("baseline oracle artifact paths do not exactly match overlay_file_map")
        overlay_bytes: dict[str, bytes] = {}
        for item in overlay_file_map:
            path_text = item["path"]
            info = info_by_name[path_text]
            _validate_zip_member(info, path_text)
            payload = archive.read(info)
            if _sha256_bytes(payload) != item["overlay_file_sha256"]:
                raise ValueError(f"baseline oracle overlay file digest mismatch: {path_text}")
            overlay_bytes[path_text] = payload

    temporary = tempfile.TemporaryDirectory(prefix="quality-baseline-oracle-")
    execution_workspace = Path(temporary.name) / "workspace"
    try:
        _copy_execution_workspace(source_workspace, execution_workspace)
        for path_text, payload in overlay_bytes.items():
            destination = execution_workspace / path_text
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink():
                destination.unlink()
            destination.write_bytes(payload)
            if _sha256_file(destination) != next(
                item["overlay_file_sha256"] for item in overlay_file_map if item["path"] == path_text
            ):
                raise ValueError(f"baseline oracle materialized overlay digest mismatch: {path_text}")
    except Exception:
        temporary.cleanup()
        raise
    return BaselineOracleSession(temporary, execution_workspace, identity)


def validate_oracle_claim_bindings(target: dict[str, Any], identity: dict[str, Any]) -> None:
    claims = {str(claim.get("id") or ""): claim for claim in target.get("claims") or []}
    bindings = identity.get("claim_bindings") or []
    bound_ids = {str(item.get("claim_id") or "") for item in bindings if isinstance(item, dict)}
    transition_ids = {
        claim_id
        for claim_id, claim in claims.items()
        if str(claim.get("closure_requirement") or "") == "regression-transition"
    }
    if bound_ids != transition_ids:
        raise ValueError(
            "baseline oracle claim_bindings must exactly match regression-transition claims"
        )
    for item in bindings:
        claim_id = str(item["claim_id"])
        selector = str(item["selector"])
        refs = {str(value) for value in claims[claim_id].get("evidence_refs") or []}
        if selector not in refs:
            raise ValueError(
                f"baseline oracle selector is not declared by transition claim {claim_id}: {selector}"
            )
