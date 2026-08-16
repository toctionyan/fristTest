from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

BASELINE_PATH = "skill-system/registry/product-source-baseline.json"
BASELINE_SCHEMA_VERSION = 2
IGNORED_PARTS = {".venv", "node_modules", "__pycache__", ".pytest_cache"}
MACHINE_LOCAL_PARTS = {"runtime"}
PERMIT_BASELINE_STATUSES = {"approved", "implementing", "review", "verified"}


class ProductSourcePolicyError(RuntimeError):
    pass


class BaselineMode(str, Enum):
    PR_CANDIDATE = "pr_candidate"
    ACCEPTED_REF = "accepted_ref"
    PERMIT_BOUND = "permit_bound"
    BASELINE_ACCEPTANCE = "baseline_acceptance"


class SnapshotSource(str, Enum):
    GIT_TRACKED = "git_tracked"
    OFFLINE_PACKAGE = "offline_package"


@dataclass(frozen=True)
class BaselineDocument:
    payload: dict[str, Any]
    files: dict[str, str]
    protected_roots: tuple[str, ...]
    generated_from: str


@dataclass(frozen=True)
class BaselineAuthority:
    name: str
    files: dict[str, str]
    protected_roots: tuple[str, ...]
    document: BaselineDocument
    mode: BaselineMode


@dataclass(frozen=True)
class BindingResult:
    mode: BaselineMode
    source: SnapshotSource
    current: dict[str, str]
    expected: dict[str, str]
    drift_paths: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def status(self) -> str:
        return "PASS" if not self.errors else "FAIL"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_root(raw: object) -> str:
    name = str(raw or "").strip().replace("\\", "/").rstrip("/")
    if not name or name.startswith("/") or ".." in Path(name).parts:
        raise ProductSourcePolicyError(f"invalid protected root: {raw!r}")
    return name


def path_is_under_roots(relative: str, protected_roots: tuple[str, ...]) -> bool:
    return any(
        relative == root or relative.startswith(root + "/")
        for root in protected_roots
    )


def recorded_paths_under_root(recorded: Mapping[str, str], root_name: str) -> list[str]:
    normalized = root_name.rstrip("/")
    prefix = normalized + "/"
    return sorted(
        path
        for path in recorded
        if path == normalized or path.startswith(prefix)
    )


def validate_baseline_document(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["baseline_not_object"]

    errors: list[str] = []
    if payload.get("schema_version") != BASELINE_SCHEMA_VERSION:
        errors.append("baseline_schema_invalid")

    roots_value = payload.get("protected_roots")
    protected_roots: tuple[str, ...] = ()
    if not isinstance(roots_value, list) or not roots_value:
        errors.append("baseline_protected_roots_missing")
    else:
        normalized: list[str] = []
        for raw in roots_value:
            try:
                normalized.append(_normalize_root(raw))
            except ProductSourcePolicyError:
                errors.append("baseline_protected_root_invalid")
        protected_roots = tuple(normalized)

    files_value = payload.get("files")
    if not isinstance(files_value, dict):
        errors.append("baseline_files_not_object")
        recorded: dict[str, str] = {}
    else:
        recorded = {str(key): str(value) for key, value in files_value.items()}

    try:
        file_count = int(payload.get("file_count"))
    except (TypeError, ValueError):
        file_count = -1
        errors.append("baseline_file_count_invalid")
    if file_count != len(recorded):
        errors.append("baseline_file_count_mismatch")

    generated_from = str(payload.get("generated_from") or "")
    if re.fullmatch(r"git:[0-9a-f]{40}", generated_from) is None:
        errors.append("baseline_generated_from_invalid")

    for relative, digest in recorded.items():
        if not path_is_under_roots(relative, protected_roots):
            errors.append(f"baseline_path_outside_protected_roots:{relative}")
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            errors.append(f"baseline_hash_invalid:{relative}")

    return list(dict.fromkeys(errors))


def load_baseline_document(workspace: Path) -> BaselineDocument:
    path = workspace.resolve() / BASELINE_PATH
    if not path.is_file():
        raise ProductSourcePolicyError("missing_product_source_baseline")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProductSourcePolicyError("invalid_product_source_baseline_json") from exc

    errors = validate_baseline_document(payload)
    if errors:
        raise ProductSourcePolicyError(
            "invalid_product_source_baseline:" + ",".join(errors)
        )

    assert isinstance(payload, dict)
    files = {str(key): str(value) for key, value in payload["files"].items()}
    roots = tuple(_normalize_root(value) for value in payload["protected_roots"])
    return BaselineDocument(
        payload=dict(payload),
        files=files,
        protected_roots=roots,
        generated_from=str(payload["generated_from"]),
    )


def detect_snapshot_source(workspace: Path) -> SnapshotSource:
    workspace = workspace.resolve()
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return SnapshotSource.OFFLINE_PACKAGE
    if completed.returncode != 0:
        return SnapshotSource.OFFLINE_PACKAGE
    try:
        if Path(completed.stdout.strip()).resolve() != workspace:
            return SnapshotSource.OFFLINE_PACKAGE
    except (OSError, RuntimeError):
        return SnapshotSource.OFFLINE_PACKAGE
    return SnapshotSource.GIT_TRACKED


def snapshot_protected_source(
    workspace: Path,
    protected_roots: tuple[str, ...],
    *,
    source: SnapshotSource,
) -> dict[str, str]:
    workspace = workspace.resolve()

    if source is SnapshotSource.GIT_TRACKED:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "ls-files",
                "-z",
                "--",
                *protected_roots,
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            detail = completed.stderr or completed.stdout or b"git ls-files failed"
            raise ProductSourcePolicyError(
                detail.decode("utf-8", errors="replace").strip()
            )
        tracked = sorted(
            item.decode("utf-8")
            for item in completed.stdout.split(b"\0")
            if item
        )
        return {
            relative: file_sha256(workspace / relative)
            for relative in tracked
            if (workspace / relative).is_file()
        }

    rows: dict[str, str] = {}
    for name in protected_roots:
        protected_root = workspace / name
        if not protected_root.exists():
            continue
        for path in sorted(
            item for item in protected_root.rglob("*") if item.is_file()
        ):
            relative = path.relative_to(workspace)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            if (
                any(part in MACHINE_LOCAL_PARTS for part in relative.parts)
                and path.name != ".gitkeep"
            ):
                continue
            rows[relative.as_posix()] = file_sha256(path)
    return rows


def _missing_root_errors(
    workspace: Path,
    protected_roots: tuple[str, ...],
    expected: Mapping[str, str],
) -> list[str]:
    errors: list[str] = []
    for root_name in protected_roots:
        if (workspace / root_name).is_dir():
            continue
        if recorded_paths_under_root(expected, root_name):
            errors.append(f"protected_root_missing:{root_name}")
    return errors


def evaluate_binding(
    workspace: Path,
    *,
    expected: Mapping[str, str],
    protected_roots: tuple[str, ...],
    mode: BaselineMode,
    source: SnapshotSource | None = None,
) -> BindingResult:
    workspace = workspace.resolve()
    chosen_source = source or detect_snapshot_source(workspace)
    current = snapshot_protected_source(
        workspace,
        protected_roots,
        source=chosen_source,
    )
    expected_map = {str(key): str(value) for key, value in expected.items()}
    drift = tuple(
        sorted(
            path
            for path in set(current) | set(expected_map)
            if current.get(path) != expected_map.get(path)
        )
    )
    errors = _missing_root_errors(workspace, protected_roots, expected_map)

    require_equality = mode in {
        BaselineMode.ACCEPTED_REF,
        BaselineMode.PERMIT_BOUND,
    }
    if require_equality:
        if len(current) != len(expected_map):
            errors.append("current_file_count_mismatch")
        if drift:
            errors.append("protected_baseline_drift")

    return BindingResult(
        mode=mode,
        source=chosen_source,
        current=current,
        expected=expected_map,
        drift_paths=drift,
        errors=tuple(dict.fromkeys(errors)),
    )


def _manifest_product_snapshot(
    workspace_files: object,
    protected_roots: tuple[str, ...],
) -> dict[str, str]:
    if not isinstance(workspace_files, dict):
        raise ProductSourcePolicyError(
            "permit baseline does not contain workspace_files"
        )
    rows: dict[str, str] = {}
    for raw_path, raw_record in workspace_files.items():
        path = str(raw_path)
        if not path_is_under_roots(path, protected_roots):
            continue
        if (
            not isinstance(raw_record, dict)
            or not isinstance(raw_record.get("sha256"), str)
        ):
            raise ProductSourcePolicyError(
                f"permit baseline has invalid protected file record: {path}"
            )
        digest = str(raw_record["sha256"])
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ProductSourcePolicyError(
                f"permit baseline has invalid protected file hash: {path}"
            )
        rows[path] = digest
    return rows


def baseline_mode_for_authority(
    authority_name: str,
    *,
    event_name: str | None = None,
) -> BaselineMode:
    if authority_name.startswith("change-permit:"):
        return BaselineMode.PERMIT_BOUND
    resolved_event = (
        os.environ.get("GITHUB_EVENT_NAME", "")
        if event_name is None
        else event_name
    )
    if str(resolved_event).strip().casefold() == "pull_request":
        return BaselineMode.PR_CANDIDATE
    return BaselineMode.ACCEPTED_REF


def resolve_baseline_authority(
    workspace: Path,
    *,
    event_name: str | None = None,
) -> BaselineAuthority:
    workspace = workspace.resolve()
    document = load_baseline_document(workspace)

    active = workspace / "governance/active-change.json"
    if active.is_file():
        try:
            active_payload = json.loads(active.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProductSourcePolicyError("invalid_active_change_json") from exc
        active_status = str(active_payload.get("status") or "").strip().lower()
        if active_status in PERMIT_BASELINE_STATUSES:
            from contract import load_contract  # type: ignore
            from repair_governance import TRANSITION_KINDS, load_chain  # type: ignore

            contract = load_contract(workspace, require_approved=False)
            if (
                contract.profile == "skill-only"
                and contract.target_kind.value in TRANSITION_KINDS
            ):
                chain = load_chain(workspace, contract.payload)
                files = _manifest_product_snapshot(
                    chain.baseline.get("workspace_files"),
                    document.protected_roots,
                )
                name = f"change-permit:{chain.permit_digest}"
                return BaselineAuthority(
                    name=name,
                    files=files,
                    protected_roots=document.protected_roots,
                    document=document,
                    mode=BaselineMode.PERMIT_BOUND,
                )

    name = "historical-registry-baseline"
    return BaselineAuthority(
        name=name,
        files=document.files,
        protected_roots=document.protected_roots,
        document=document,
        mode=baseline_mode_for_authority(name, event_name=event_name),
    )


def evaluate_product_source(
    workspace: Path,
    *,
    event_name: str | None = None,
    source: SnapshotSource | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    try:
        authority = resolve_baseline_authority(
            workspace,
            event_name=event_name,
        )
        binding = evaluate_binding(
            workspace,
            expected=authority.files,
            protected_roots=authority.protected_roots,
            mode=authority.mode,
            source=source,
        )
    except ProductSourcePolicyError as exc:
        return {
            "status": "FAIL",
            "errors": [str(exc)],
            "protected_file_count": 0,
            "baseline_file_count": 0,
            "baseline_authority": "unavailable",
            "baseline_mode": "unavailable",
            "drift_paths": [],
        }

    errors = list(binding.errors)
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "protected_file_count": len(binding.current),
        "baseline_file_count": len(binding.expected),
        "baseline_authority": authority.name,
        "baseline_mode": authority.mode.value,
        "snapshot_source": binding.source.value,
        "drift_paths": list(binding.drift_paths),
    }
