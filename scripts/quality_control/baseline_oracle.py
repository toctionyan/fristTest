from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .common import _python_selector_exists, _sha256_file
from .contracts import workspace_snapshot

SCHEMA_VERSION = 1
EXECUTION_MODE = "ephemeral_overlay_view"
EXPECTED_REPOSITORY = "toctionyan/fristTest"
EXPECTED_BASE_COMMIT = "e0e04d51e9da9790bef7bd0482584f60b8e975a9"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ORACLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class BaselineOracleError(ValueError):
    """Raised when a baseline oracle cannot be trusted or isolated."""


@dataclass(frozen=True)
class BaselineOracleOverlayIdentity:
    payload: dict[str, Any]

    @property
    def canonical_fingerprint(self) -> str:
        return str(self.payload["canonical_fingerprint"])


@dataclass
class BaselineOracleExecutionView:
    path: Path
    identity: BaselineOracleOverlayIdentity
    source_workspace_fingerprint: str


@dataclass(frozen=True)
class _ValidatedOracle:
    identity: BaselineOracleOverlayIdentity
    overlay_bytes: dict[str, bytes]
    source_workspace_fingerprint: str
    source_file_hashes: dict[str, str]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(canonical.encode("utf-8"))


def _require_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BaselineOracleError(f"{field} must be an object")
    return value


def _require_exact_keys(value: dict[str, Any], required: set[str], *, field: str) -> None:
    actual = set(value)
    if actual != required:
        missing = sorted(required - actual)
        extra = sorted(actual - required)
        detail = []
        if missing:
            detail.append(f"missing={missing}")
        if extra:
            detail.append(f"extra={extra}")
        raise BaselineOracleError(f"{field} has invalid fields: {', '.join(detail)}")


def _normalize_sha256(value: Any, *, field: str, allow_prefix: bool = False) -> str:
    if not isinstance(value, str):
        raise BaselineOracleError(f"{field} must be a sha256 string")
    normalized = value.strip().lower()
    if allow_prefix and normalized.startswith("sha256:"):
        normalized = normalized[7:]
    if not _SHA256_RE.fullmatch(normalized):
        raise BaselineOracleError(f"{field} must be lowercase 64-hex sha256")
    return normalized


def _normalize_workspace_path(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise BaselineOracleError(f"{field} must be a workspace-relative POSIX path")
    if not value or value != value.strip() or "\\" in value or "\x00" in value or "\n" in value or "\r" in value:
        raise BaselineOracleError(f"{field} must be a normalized workspace-relative POSIX path")
    if value.startswith("/") or value.endswith("/") or "//" in value:
        raise BaselineOracleError(f"{field} must be a normalized workspace-relative POSIX path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise BaselineOracleError(f"{field} must not contain traversal or empty segments")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise BaselineOracleError(f"{field} must be a normalized workspace-relative POSIX path")
    return value


def _normalize_selector(value: Any, *, field: str) -> tuple[str, str]:
    if not isinstance(value, str) or "\n" in value or "\r" in value or "::" not in value:
        raise BaselineOracleError(f"{field} must be a pytest path::selector reference")
    path_raw, selector = value.split("::", 1)
    path = _normalize_workspace_path(path_raw, field=f"{field} path")
    if not selector.strip():
        raise BaselineOracleError(f"{field} selector must not be empty")
    if Path(path).suffix.lower() != ".py":
        raise BaselineOracleError(f"{field} must reference a Python file")
    return path, f"{path}::{selector}"


def _normalize_manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    required_top = {
        "schema_version", "oracle_id", "base_source_identity", "base_workspace_fingerprint",
        "overlay_artifact_sha256", "overlay_file_map", "claim_bindings", "provenance",
        "execution_mode", "canonical_fingerprint",
    }
    _require_exact_keys(payload, required_top, field="baseline oracle manifest")

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise BaselineOracleError(f"unsupported baseline oracle schema_version: {payload.get('schema_version')!r}")
    oracle_id = payload.get("oracle_id")
    if not isinstance(oracle_id, str) or not oracle_id or len(oracle_id) > 200 or not _ORACLE_ID_RE.fullmatch(oracle_id):
        raise BaselineOracleError("oracle_id is invalid")
    if payload.get("execution_mode") != EXECUTION_MODE:
        raise BaselineOracleError(f"execution_mode must be {EXECUTION_MODE}")

    source_identity = _require_object(payload.get("base_source_identity"), field="base_source_identity")
    _require_exact_keys(source_identity, {"repository", "commit_sha"}, field="base_source_identity")
    if source_identity.get("repository") != EXPECTED_REPOSITORY:
        raise BaselineOracleError(f"base_source_identity.repository must be {EXPECTED_REPOSITORY}")
    if source_identity.get("commit_sha") != EXPECTED_BASE_COMMIT:
        raise BaselineOracleError(f"base_source_identity.commit_sha must be exact B36 {EXPECTED_BASE_COMMIT}")

    overlay_raw = payload.get("overlay_file_map")
    if not isinstance(overlay_raw, list) or not overlay_raw:
        raise BaselineOracleError("overlay_file_map must be a non-empty array")
    overlay_file_map: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for index, raw in enumerate(overlay_raw):
        item = _require_object(raw, field=f"overlay_file_map[{index}]")
        _require_exact_keys(item, {"path", "base_file_sha256", "overlay_file_sha256"}, field=f"overlay_file_map[{index}]")
        path = _normalize_workspace_path(item.get("path"), field=f"overlay_file_map[{index}].path")
        if path in seen_paths:
            raise BaselineOracleError(f"duplicate overlay path: {path}")
        seen_paths.add(path)
        overlay_file_map.append(
            {
                "path": path,
                "base_file_sha256": _normalize_sha256(item.get("base_file_sha256"), field=f"overlay_file_map[{index}].base_file_sha256"),
                "overlay_file_sha256": _normalize_sha256(item.get("overlay_file_sha256"), field=f"overlay_file_map[{index}].overlay_file_sha256"),
            }
        )
    overlay_file_map.sort(key=lambda item: item["path"])

    bindings_raw = payload.get("claim_bindings")
    if not isinstance(bindings_raw, list) or not bindings_raw:
        raise BaselineOracleError("claim_bindings must be a non-empty array")
    claim_bindings: list[dict[str, str]] = []
    seen_bindings: set[tuple[str, str]] = set()
    for index, raw in enumerate(bindings_raw):
        item = _require_object(raw, field=f"claim_bindings[{index}]")
        _require_exact_keys(item, {"claim_id", "selector"}, field=f"claim_bindings[{index}]")
        claim_id = item.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id.strip() or len(claim_id) > 240:
            raise BaselineOracleError(f"claim_bindings[{index}].claim_id is invalid")
        selector_path, selector = _normalize_selector(item.get("selector"), field=f"claim_bindings[{index}].selector")
        if selector_path not in seen_paths:
            raise BaselineOracleError(f"claim binding selector path is not declared in overlay_file_map: {selector_path}")
        key = (claim_id, selector)
        if key in seen_bindings:
            raise BaselineOracleError(f"duplicate claim binding: {claim_id} -> {selector}")
        seen_bindings.add(key)
        claim_bindings.append({"claim_id": claim_id, "selector": selector})
    claim_bindings.sort(key=lambda item: (item["claim_id"], item["selector"]))

    provenance = _require_object(payload.get("provenance"), field="provenance")
    _require_exact_keys(provenance, {"provider", "run_id", "job_id", "artifact_id", "artifact_digest"}, field="provenance")
    if provenance.get("provider") != "github-actions":
        raise BaselineOracleError("provenance.provider must be github-actions")
    for field in ("run_id", "job_id", "artifact_id"):
        value = provenance.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise BaselineOracleError(f"provenance.{field} must be a positive integer")

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "oracle_id": oracle_id,
        "base_source_identity": {
            "repository": EXPECTED_REPOSITORY,
            "commit_sha": EXPECTED_BASE_COMMIT,
        },
        "base_workspace_fingerprint": _normalize_sha256(payload.get("base_workspace_fingerprint"), field="base_workspace_fingerprint"),
        "overlay_artifact_sha256": _normalize_sha256(payload.get("overlay_artifact_sha256"), field="overlay_artifact_sha256"),
        "overlay_file_map": overlay_file_map,
        "claim_bindings": claim_bindings,
        "provenance": {
            "provider": "github-actions",
            "run_id": provenance["run_id"],
            "job_id": provenance["job_id"],
            "artifact_id": provenance["artifact_id"],
            "artifact_digest": _normalize_sha256(provenance.get("artifact_digest"), field="provenance.artifact_digest", allow_prefix=True),
        },
        "execution_mode": EXECUTION_MODE,
    }
    declared = _normalize_sha256(payload.get("canonical_fingerprint"), field="canonical_fingerprint")
    expected = _canonical_json_fingerprint(normalized)
    if declared != expected:
        raise BaselineOracleError("canonical_fingerprint mismatch")
    normalized["canonical_fingerprint"] = expected
    return normalized


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BaselineOracleError(f"baseline oracle manifest does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineOracleError(f"baseline oracle manifest is unreadable: {exc}") from exc
    return _require_object(payload, field="baseline oracle manifest")


def _normalize_origin_repo(url: str) -> str | None:
    value = url.strip()
    if value.startswith("git@github.com:"):
        value = value[len("git@github.com:"):]
    elif value.startswith("ssh://git@github.com/"):
        value = value[len("ssh://git@github.com/"):]
    elif value.startswith("https://github.com/"):
        value = value[len("https://github.com/"):]
    elif value.startswith("http://github.com/"):
        value = value[len("http://github.com/"):]
    else:
        return None
    if value.endswith(".git"):
        value = value[:-4]
    return value.strip("/") or None


def _git_output(source_workspace: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source_workspace), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BaselineOracleError(f"cannot verify baseline source git identity: {exc}") from exc
    return completed.stdout.strip()


def _current_source_identity(source_workspace: Path) -> dict[str, str]:
    commit = _git_output(source_workspace, "rev-parse", "HEAD")
    origin = _git_output(source_workspace, "config", "--get", "remote.origin.url")
    repository = _normalize_origin_repo(origin)
    if repository is None:
        raise BaselineOracleError("cannot normalize git remote.origin.url for baseline source")
    return {"repository": repository, "commit_sha": commit.lower()}


def _workspace_fingerprint(source_workspace: Path) -> str:
    snapshot = workspace_snapshot(source_workspace.resolve())
    fingerprint = snapshot.get("fingerprint")
    if not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint):
        raise BaselineOracleError("workspace snapshot did not produce a valid fingerprint")
    return fingerprint


def _path_has_symlink_component(root: Path, relative: str) -> bool:
    current = root.resolve()
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _assert_source_file_bindings(source_workspace: Path, file_map: list[dict[str, str]]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    root = source_workspace.resolve()
    for entry in file_map:
        relative = entry["path"]
        if _path_has_symlink_component(root, relative):
            raise BaselineOracleError(f"overlay source path may not traverse symlinks: {relative}")
        path = root.joinpath(*PurePosixPath(relative).parts)
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise BaselineOracleError(f"overlay source path escapes workspace: {relative}") from exc
        if not path.is_file():
            raise BaselineOracleError(f"overlay source file is missing: {relative}")
        actual = _sha256_file(path)
        if actual != entry["base_file_sha256"]:
            raise BaselineOracleError(f"base_file_sha256 mismatch for {relative}")
        hashes[relative] = actual
    return hashes


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _read_artifact_members(artifact_path: Path) -> dict[str, bytes]:
    if not artifact_path.is_file():
        raise BaselineOracleError(f"baseline oracle artifact does not exist: {artifact_path}")
    members: dict[str, bytes] = {}
    try:
        if zipfile.is_zipfile(artifact_path):
            with zipfile.ZipFile(artifact_path, "r") as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    if _zip_member_is_symlink(info):
                        raise BaselineOracleError(f"oracle artifact contains symlink: {info.filename}")
                    name = _normalize_workspace_path(info.filename, field="oracle artifact member")
                    if name in members:
                        raise BaselineOracleError(f"oracle artifact contains duplicate member: {name}")
                    members[name] = archive.read(info)
            return members
        if tarfile.is_tarfile(artifact_path):
            with tarfile.open(artifact_path, "r:*") as archive:
                for info in archive.getmembers():
                    if info.isdir():
                        continue
                    if not info.isfile() or info.issym() or info.islnk():
                        raise BaselineOracleError(f"oracle artifact contains non-regular member: {info.name}")
                    name = _normalize_workspace_path(info.name, field="oracle artifact member")
                    if name in members:
                        raise BaselineOracleError(f"oracle artifact contains duplicate member: {name}")
                    handle = archive.extractfile(info)
                    if handle is None:
                        raise BaselineOracleError(f"cannot read oracle artifact member: {name}")
                    members[name] = handle.read()
            return members
    except (OSError, tarfile.TarError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, BaselineOracleError):
            raise
        raise BaselineOracleError(f"baseline oracle artifact is unreadable: {exc}") from exc
    raise BaselineOracleError("baseline oracle artifact must be a ZIP or TAR archive")


def _validate_artifact(
    *, artifact_path: Path, manifest: dict[str, Any]
) -> dict[str, bytes]:
    actual_artifact_sha = _sha256_file(artifact_path) if artifact_path.is_file() else ""
    if actual_artifact_sha != manifest["overlay_artifact_sha256"]:
        raise BaselineOracleError("overlay_artifact_sha256 mismatch")
    if manifest["provenance"]["artifact_digest"] != actual_artifact_sha:
        raise BaselineOracleError("provenance.artifact_digest does not bind the supplied oracle artifact")

    members = _read_artifact_members(artifact_path)
    declared = {entry["path"]: entry for entry in manifest["overlay_file_map"]}
    if set(members) != set(declared):
        missing = sorted(set(declared) - set(members))
        extra = sorted(set(members) - set(declared))
        raise BaselineOracleError(f"oracle artifact member set mismatch: missing={missing}, extra={extra}")
    for path, data in members.items():
        if _sha256_bytes(data) != declared[path]["overlay_file_sha256"]:
            raise BaselineOracleError(f"overlay_file_sha256 mismatch for {path}")
    return members


def _validate_source_identity(source_workspace: Path, manifest: dict[str, Any]) -> str:
    actual_identity = _current_source_identity(source_workspace)
    if actual_identity != manifest["base_source_identity"]:
        raise BaselineOracleError(
            f"baseline source identity mismatch: expected={manifest['base_source_identity']}, actual={actual_identity}"
        )
    actual_fingerprint = _workspace_fingerprint(source_workspace)
    if actual_fingerprint != manifest["base_workspace_fingerprint"]:
        raise BaselineOracleError("base_workspace_fingerprint mismatch")
    return actual_fingerprint


def _validated_oracle(
    *, source_workspace: Path, manifest_path: Path, artifact_path: Path
) -> _ValidatedOracle:
    source_workspace = source_workspace.resolve()
    manifest_path = manifest_path.resolve()
    artifact_path = artifact_path.resolve()
    if not source_workspace.is_dir():
        raise BaselineOracleError(f"source workspace does not exist: {source_workspace}")
    manifest = _normalize_manifest_payload(_load_manifest(manifest_path))
    source_fingerprint = _validate_source_identity(source_workspace, manifest)
    source_file_hashes = _assert_source_file_bindings(source_workspace, manifest["overlay_file_map"])
    overlay_bytes = _validate_artifact(artifact_path=artifact_path, manifest=manifest)
    return _ValidatedOracle(
        identity=BaselineOracleOverlayIdentity(payload=manifest),
        overlay_bytes=overlay_bytes,
        source_workspace_fingerprint=source_fingerprint,
        source_file_hashes=source_file_hashes,
    )


def load_and_validate_baseline_oracle(
    *, source_workspace: Path, manifest_path: Path, artifact_path: Path
) -> BaselineOracleOverlayIdentity:
    """Validate immutable baseline-oracle identity without creating an execution view."""
    return _validated_oracle(
        source_workspace=source_workspace,
        manifest_path=manifest_path,
        artifact_path=artifact_path,
    ).identity


def _assert_source_unchanged(source_workspace: Path, validated: _ValidatedOracle) -> None:
    if _workspace_fingerprint(source_workspace) != validated.source_workspace_fingerprint:
        raise BaselineOracleError("source workspace mutated while baseline oracle was active")
    for relative, expected in validated.source_file_hashes.items():
        path = source_workspace.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file() or _sha256_file(path) != expected:
            raise BaselineOracleError(f"source overlay-bound file mutated while baseline oracle was active: {relative}")


def _prepare_execution_view(source_workspace: Path, validated: _ValidatedOracle) -> Path:
    parent = Path(tempfile.mkdtemp(prefix="baseline-oracle-parent-"))
    view = parent / "workspace"
    try:
        shutil.copytree(source_workspace, view, symlinks=False)
        before = workspace_snapshot(view.resolve())
        for relative, data in validated.overlay_bytes.items():
            destination = view.joinpath(*PurePosixPath(relative).parts)
            try:
                destination.resolve().relative_to(view.resolve())
            except ValueError as exc:
                raise BaselineOracleError(f"overlay destination escapes execution view: {relative}") from exc
            if not destination.is_file():
                raise BaselineOracleError(f"execution-view overlay destination is missing: {relative}")
            destination.write_bytes(data)
        after = workspace_snapshot(view.resolve())
        before_files = {str(k): str(v) for k, v in dict(before.get("files") or {}).items()}
        after_files = {str(k): str(v) for k, v in dict(after.get("files") or {}).items()}
        changed = sorted(path for path in set(before_files) | set(after_files) if before_files.get(path) != after_files.get(path))
        if changed != sorted(validated.overlay_bytes):
            raise BaselineOracleError(f"execution view changed outside declared overlay_file_map: {changed}")
        for entry in validated.identity.payload["overlay_file_map"]:
            relative = entry["path"]
            path = view.joinpath(*PurePosixPath(relative).parts)
            if _sha256_file(path) != entry["overlay_file_sha256"]:
                raise BaselineOracleError(f"execution-view overlay hash mismatch for {relative}")
        for binding in validated.identity.payload["claim_bindings"]:
            relative, selector = binding["selector"].split("::", 1)
            path = view.joinpath(*PurePosixPath(relative).parts)
            if not _python_selector_exists(path, selector):
                raise BaselineOracleError(f"claim binding selector is absent from execution view: {binding['selector']}")
        return view
    except Exception:
        shutil.rmtree(parent, ignore_errors=True)
        raise


@contextmanager
def baseline_oracle_execution_view(
    *, source_workspace: Path, manifest_path: Path, artifact_path: Path
) -> Iterator[BaselineOracleExecutionView]:
    """Yield an isolated exact-source + immutable-oracle execution view.

    The source workspace is validated before preparation and checked again both
    before yielding and after the caller returns.  Only the ephemeral copy is
    overlaid; oracle bytes are never persisted back into the source workspace.
    """
    source_workspace = source_workspace.resolve()
    validated = _validated_oracle(
        source_workspace=source_workspace,
        manifest_path=manifest_path,
        artifact_path=artifact_path,
    )
    view = _prepare_execution_view(source_workspace, validated)
    parent = view.parent
    try:
        _assert_source_unchanged(source_workspace, validated)
        yield BaselineOracleExecutionView(
            path=view,
            identity=validated.identity,
            source_workspace_fingerprint=validated.source_workspace_fingerprint,
        )
        _assert_source_unchanged(source_workspace, validated)
    finally:
        try:
            shutil.rmtree(parent)
        except OSError as exc:
            raise BaselineOracleError(f"failed to clean baseline oracle execution view: {exc}") from exc
