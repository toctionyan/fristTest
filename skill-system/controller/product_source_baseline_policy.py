from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

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


CANONICAL_SNAPSHOT_FORMAT = "protected-git-tree@1"
V3_SNAPSHOT_SCHEMA_VERSION = 3
V3_SNAPSHOT_FIELDS = {
    "schema_version",
    "snapshot_format",
    "product_source_ref",
    "protected_roots",
    "entry_count",
    "entries",
    "protected_snapshot_digest",
}
V3_SNAPSHOT_ENTRY_FIELDS = {"path", "mode", "digest"}
GIT_COMMIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
SNAPSHOT_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
SUPPORTED_GIT_FILE_MODES = {"100644", "100755"}


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


def _normalize_snapshot_path(raw: object, *, label: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ProductSourcePolicyError(f"invalid {label}: {raw!r}")
    if (
        raw.startswith("/")
        or "\\" in raw
        or "\x00" in raw
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        raise ProductSourcePolicyError(f"invalid {label}: {raw!r}")
    return raw


def normalize_protected_roots(protected_roots: Iterable[str]) -> tuple[str, ...]:
    if isinstance(protected_roots, (str, bytes)):
        raise ProductSourcePolicyError("protected_roots_must_be_iterable_of_paths")
    try:
        normalized = tuple(
            _normalize_snapshot_path(root, label="protected root")
            for root in protected_roots
        )
    except TypeError as exc:
        raise ProductSourcePolicyError("protected_roots_must_be_iterable") from exc
    if not normalized:
        raise ProductSourcePolicyError("protected_roots_missing")
    if len(set(normalized)) != len(normalized):
        raise ProductSourcePolicyError("protected_roots_duplicate")
    return tuple(sorted(normalized, key=lambda value: value.encode("utf-8")))


def _canonical_snapshot_digest(
    protected_roots: tuple[str, ...],
    entries: list[dict[str, str]],
) -> str:
    canonical_payload = {
        "snapshot_format": CANONICAL_SNAPSHOT_FORMAT,
        "protected_roots": list(protected_roots),
        "entries": entries,
    }
    canonical_bytes = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()


def _git_command_error(completed: subprocess.CompletedProcess[bytes]) -> str:
    detail = completed.stderr or completed.stdout or b"git command failed"
    return detail.decode("utf-8", errors="replace").strip()


def _assert_commit_object(repository: Path, commit_sha: str) -> None:
    if GIT_COMMIT_SHA_PATTERN.fullmatch(commit_sha) is None:
        raise ProductSourcePolicyError("product_source_ref_must_be_full_commit_sha")
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), "cat-file", "-t", commit_sha],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProductSourcePolicyError("git_commit_lookup_failed") from exc
    if completed.returncode != 0:
        raise ProductSourcePolicyError(_git_command_error(completed))
    if completed.stdout.strip() != b"commit":
        raise ProductSourcePolicyError("product_source_ref_is_not_commit")


def _git_tree_records(
    repository: Path,
    commit_sha: str,
    protected_roots: tuple[str, ...],
) -> list[tuple[str, str, str, str]]:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "ls-tree",
                "-r",
                "-z",
                "--full-tree",
                commit_sha,
                "--",
                *protected_roots,
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProductSourcePolicyError("git_tree_read_failed") from exc
    if completed.returncode != 0:
        raise ProductSourcePolicyError(_git_command_error(completed))

    records: list[tuple[str, str, str, str]] = []
    for raw_record in completed.stdout.split(b"\0"):
        if not raw_record:
            continue
        try:
            raw_header, raw_path = raw_record.split(b"\t", 1)
            raw_mode, raw_type, raw_object = raw_header.split(b" ", 2)
            path = raw_path.decode("utf-8")
            mode = raw_mode.decode("ascii")
            entry_type = raw_type.decode("ascii")
            object_id = raw_object.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProductSourcePolicyError("invalid_git_tree_record") from exc
        _normalize_snapshot_path(path, label="git tree path")
        if not path_is_under_roots(path, protected_roots):
            raise ProductSourcePolicyError(
                f"git_tree_path_outside_protected_roots:{path}"
            )
        records.append((path, mode, entry_type, object_id))
    return records


def _git_blob_digests(
    repository: Path,
    object_ids: Iterable[str],
) -> dict[str, str]:
    unique_object_ids = tuple(dict.fromkeys(object_ids))
    if not unique_object_ids:
        return {}
    try:
        process = subprocess.Popen(
            ["git", "-C", str(repository), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        request = b"".join(
            object_id.encode("ascii") + b"\n" for object_id in unique_object_ids
        )
        output, error = process.communicate(request, timeout=30)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise ProductSourcePolicyError("git_blob_batch_read_timeout") from exc
    except OSError as exc:
        raise ProductSourcePolicyError("git_blob_batch_read_failed") from exc
    if process.returncode != 0:
        detail = error or output or b"git cat-file --batch failed"
        raise ProductSourcePolicyError(
            detail.decode("utf-8", errors="replace").strip()
        )

    digests: dict[str, str] = {}
    cursor = 0
    for object_id in unique_object_ids:
        header_end = output.find(b"\n", cursor)
        if header_end < 0:
            raise ProductSourcePolicyError("invalid_git_blob_batch_header")
        header = output[cursor:header_end].split(b" ")
        cursor = header_end + 1
        if len(header) != 3 or header[0].decode("ascii", errors="ignore") != object_id:
            raise ProductSourcePolicyError("git_blob_batch_object_mismatch")
        try:
            object_type = header[1].decode("ascii")
            size = int(header[2])
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProductSourcePolicyError("invalid_git_blob_batch_header") from exc
        if object_type != "blob" or size < 0:
            raise ProductSourcePolicyError(
                f"unsupported_git_blob_object:{object_id}:{object_type}"
            )
        blob_end = cursor + size
        if blob_end >= len(output) or output[blob_end:blob_end + 1] != b"\n":
            raise ProductSourcePolicyError("invalid_git_blob_batch_payload")
        blob = output[cursor:blob_end]
        digests[object_id] = "sha256:" + hashlib.sha256(blob).hexdigest()
        cursor = blob_end + 1
    if cursor != len(output):
        raise ProductSourcePolicyError("unexpected_git_blob_batch_output")
    return digests

def build_canonical_product_snapshot(
    repository: Path,
    commit_sha: str,
    protected_roots: Iterable[str],
) -> dict[str, Any]:
    """Build a v3 snapshot from Git objects, never from the working tree."""
    repository = repository.resolve()
    roots = normalize_protected_roots(protected_roots)
    _assert_commit_object(repository, commit_sha)

    entries: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    records = _git_tree_records(repository, commit_sha, roots)
    blob_records: list[tuple[str, str, str]] = []
    for path, mode, entry_type, object_id in records:
        if path in seen_paths:
            raise ProductSourcePolicyError(f"duplicate_git_tree_path:{path}")
        seen_paths.add(path)
        if mode not in SUPPORTED_GIT_FILE_MODES:
            raise ProductSourcePolicyError(
                f"unsupported_git_tree_mode:{path}:{mode}"
            )
        if entry_type != "blob":
            raise ProductSourcePolicyError(
                f"unsupported_git_tree_type:{path}:{entry_type}"
            )
        if re.fullmatch(r"[0-9a-f]{40}", object_id) is None:
            raise ProductSourcePolicyError(f"invalid_git_object_id:{path}")
        blob_records.append((path, mode, object_id))

    blob_digests = _git_blob_digests(
        repository,
        (object_id for _, _, object_id in blob_records),
    )
    for path, mode, object_id in blob_records:
        digest = blob_digests[object_id]
        entries.append({"path": path, "mode": mode, "digest": digest})

    entries.sort(key=lambda entry: entry["path"].encode("utf-8"))
    return {
        "schema_version": V3_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_format": CANONICAL_SNAPSHOT_FORMAT,
        "product_source_ref": "git-commit-sha1:" + commit_sha,
        "protected_roots": list(roots),
        "entry_count": len(entries),
        "entries": entries,
        "protected_snapshot_digest": _canonical_snapshot_digest(roots, entries),
    }


def validate_v3_product_snapshot(
    payload: object,
    *,
    expected_commit_sha: str | None = None,
    expected_protected_roots: Iterable[str] | None = None,
) -> list[str]:
    if not isinstance(payload, dict):
        return ["v3_snapshot_not_object"]

    errors: list[str] = []
    unknown_fields = set(payload) - V3_SNAPSHOT_FIELDS
    errors.extend(f"v3_unknown_field:{field}" for field in sorted(unknown_fields))
    missing_fields = V3_SNAPSHOT_FIELDS - set(payload)
    errors.extend(f"v3_missing_field:{field}" for field in sorted(missing_fields))

    if payload.get("schema_version") != V3_SNAPSHOT_SCHEMA_VERSION:
        errors.append("v3_schema_version_invalid")
    if payload.get("snapshot_format") != CANONICAL_SNAPSHOT_FORMAT:
        errors.append("v3_snapshot_format_invalid")

    source_ref = payload.get("product_source_ref")
    if not isinstance(source_ref, str) or re.fullmatch(
        r"git-commit-sha1:[0-9a-f]{40}", source_ref
    ) is None:
        errors.append("v3_product_source_ref_invalid")
    elif expected_commit_sha is not None:
        if GIT_COMMIT_SHA_PATTERN.fullmatch(expected_commit_sha) is None:
            errors.append("v3_expected_commit_sha_invalid")
        elif source_ref != "git-commit-sha1:" + expected_commit_sha:
            errors.append("v3_product_source_ref_mismatch")

    roots: tuple[str, ...] = ()
    roots_value = payload.get("protected_roots")
    roots_valid = isinstance(roots_value, list) and bool(roots_value)
    if not roots_valid:
        errors.append("v3_protected_roots_invalid")
    else:
        try:
            roots = normalize_protected_roots(roots_value)
        except ProductSourcePolicyError:
            roots_valid = False
            errors.append("v3_protected_roots_invalid")
        else:
            if roots_value != list(roots):
                errors.append("v3_protected_roots_not_canonical")
    if expected_protected_roots is not None and roots_valid:
        try:
            expected_roots = normalize_protected_roots(expected_protected_roots)
        except ProductSourcePolicyError:
            errors.append("v3_expected_protected_roots_invalid")
        else:
            if roots != expected_roots:
                errors.append("v3_protected_roots_mismatch")

    entries: list[dict[str, str]] = []
    entries_value = payload.get("entries")
    entries_valid = isinstance(entries_value, list)
    if not entries_valid:
        errors.append("v3_entries_invalid")
    else:
        for index, raw_entry in enumerate(entries_value):
            if not isinstance(raw_entry, dict):
                entries_valid = False
                errors.append(f"v3_entry_not_object:{index}")
                continue
            unknown_entry_fields = set(raw_entry) - V3_SNAPSHOT_ENTRY_FIELDS
            errors.extend(
                f"v3_entry_unknown_field:{index}:{field}"
                for field in sorted(unknown_entry_fields)
            )
            if set(raw_entry) != V3_SNAPSHOT_ENTRY_FIELDS:
                entries_valid = False
                errors.append(f"v3_entry_fields_invalid:{index}")
                continue
            path = raw_entry.get("path")
            mode = raw_entry.get("mode")
            digest = raw_entry.get("digest")
            try:
                normalized_path = _normalize_snapshot_path(
                    path, label="v3 entry path"
                )
            except ProductSourcePolicyError:
                entries_valid = False
                errors.append(f"v3_entry_path_invalid:{index}")
                continue
            if roots_valid and not path_is_under_roots(normalized_path, roots):
                entries_valid = False
                errors.append(f"v3_entry_path_outside_roots:{index}")
            if mode not in SUPPORTED_GIT_FILE_MODES:
                entries_valid = False
                errors.append(f"v3_entry_mode_invalid:{index}")
            if not isinstance(digest, str) or SNAPSHOT_DIGEST_PATTERN.fullmatch(
                digest
            ) is None:
                entries_valid = False
                errors.append(f"v3_entry_digest_invalid:{index}")
            if isinstance(mode, str) and isinstance(digest, str):
                entries.append(
                    {"path": normalized_path, "mode": mode, "digest": digest}
                )

    entry_count = payload.get("entry_count")
    if not isinstance(entry_count, int) or isinstance(entry_count, bool):
        errors.append("v3_entry_count_invalid")
    elif isinstance(entries_value, list) and entry_count != len(entries_value):
        errors.append("v3_entry_count_mismatch")

    if entries_valid and entries_value == sorted(
        entries_value,
        key=lambda entry: entry["path"].encode("utf-8"),
    ):
        if len({entry["path"] for entry in entries}) != len(entries):
            errors.append("v3_entry_path_duplicate")
    elif isinstance(entries_value, list):
        errors.append("v3_entries_not_canonical")

    snapshot_digest = payload.get("protected_snapshot_digest")
    if not isinstance(snapshot_digest, str) or SNAPSHOT_DIGEST_PATTERN.fullmatch(
        snapshot_digest
    ) is None:
        errors.append("v3_protected_snapshot_digest_invalid")
    elif roots_valid and entries_valid:
        expected_digest = _canonical_snapshot_digest(roots, entries)
        if snapshot_digest != expected_digest:
            errors.append("v3_protected_snapshot_digest_mismatch")

    return list(dict.fromkeys(errors))


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


def _event_default_branch(explicit: str | None) -> str:
    if explicit is not None:
        return str(explicit).strip()
    event_path = str(os.environ.get("GITHUB_EVENT_PATH") or "").strip()
    if not event_path:
        return ""
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    repository = payload.get("repository") if isinstance(payload, dict) else None
    if not isinstance(repository, dict):
        return ""
    return str(repository.get("default_branch") or "").strip()


def baseline_mode_for_authority(
    authority_name: str,
    *,
    event_name: str | None = None,
    ref_type: str | None = None,
    ref_name: str | None = None,
    default_branch: str | None = None,
) -> BaselineMode:
    if authority_name.startswith("change-permit:"):
        return BaselineMode.PERMIT_BOUND

    resolved_event = (
        os.environ.get("GITHUB_EVENT_NAME", "")
        if event_name is None
        else event_name
    )
    event = str(resolved_event).strip().casefold()
    if event == "pull_request":
        return BaselineMode.PR_CANDIDATE

    if event == "push":
        resolved_ref_type = (
            os.environ.get("GITHUB_REF_TYPE", "")
            if ref_type is None
            else ref_type
        )
        resolved_ref_name = (
            os.environ.get("GITHUB_REF_NAME", "")
            if ref_name is None
            else ref_name
        )
        branch_type = str(resolved_ref_type).strip().casefold()
        branch_name = str(resolved_ref_name).strip()
        accepted_branch = _event_default_branch(default_branch)

        # A push to a non-default branch is still a candidate observation. It
        # must not be promoted to accepted-ref authority merely because the
        # workflow trigger is `push`. Default-branch pushes and all ambiguous
        # push identities remain fail-closed as ACCEPTED_REF.
        if (
            branch_type == "branch"
            and branch_name
            and accepted_branch
            and branch_name != accepted_branch
        ):
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
