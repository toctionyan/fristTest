from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from capability_registry import CapabilityBinding
from langgraph_workflow_runtime import StepDispatchResult, WorkflowRuntimeState
from workflow_graph_contract import WorkflowStepSpec


class WorkspaceProviderError(RuntimeError):
    """Raised when a structured workspace mutation cannot be applied safely."""


_PROTECTED = (
    ".git",
    ".git/**",
    ".harness",
    ".harness/**",
    ".quality",
    ".quality/**",
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _attempt(state: Mapping[str, Any], step: WorkflowStepSpec) -> int:
    attempts = state.get("step_attempts") if isinstance(state.get("step_attempts"), Mapping) else {}
    return int(attempts.get(step.step_id) or 0) + 1


def _safe_component(value: object, *, fallback: str) -> str:
    text = "".join(char if char.isalnum() or char in "_.-" else "-" for char in _text(value))
    return text.strip("-.") or fallback


def _evidence(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = _text(value)
        if ref and ref not in seen:
            result.append(ref)
            seen.add(ref)
    return tuple(result)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _relative_path(value: object) -> str:
    text = _text(value).replace("\\", "/")
    if not text or text.startswith("/"):
        raise WorkspaceProviderError("workspace operation path must be relative")
    path = PurePosixPath(text)
    if path.as_posix() != text or any(part in {"", ".", ".."} for part in path.parts):
        raise WorkspaceProviderError(f"workspace operation path is not canonical: {text!r}")
    return path.as_posix()


@dataclass(frozen=True)
class _Operation:
    operation: str
    relative_path: str
    path: Path
    expected_sha256: str | None
    content: bytes | None
    content_sha256: str | None
    original: bytes | None
    original_mode: int | None


class StructuredWorkspaceProviderAdapter:
    """Execute a closed, digest-bound workspace mutation transaction.

    The existing dispatcher remains responsible for calling WriteAuthorityGuard
    before this adapter. Constructor path patterns are trusted embedding policy;
    request payloads cannot broaden them.
    """

    provider_id = "local.workspace"
    provider_type = "executor"
    _SUPPORTED = frozenset({"workspace.write"})
    _OPERATIONS = frozenset({"create", "replace", "delete"})

    def __init__(
        self,
        *,
        workspace: Path,
        allowed_path_patterns: Iterable[str],
        max_operations: int = 256,
        max_content_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.workspace = workspace.resolve()
        if not self.workspace.is_dir():
            raise WorkspaceProviderError(f"workspace is not a directory: {self.workspace}")
        self.allowed_path_patterns = tuple(
            dict.fromkeys(_text(value).replace("\\", "/") for value in allowed_path_patterns if _text(value))
        )
        if not self.allowed_path_patterns:
            raise WorkspaceProviderError("local.workspace requires non-empty allowed_path_patterns")
        if not isinstance(max_operations, int) or isinstance(max_operations, bool) or max_operations < 1:
            raise WorkspaceProviderError("max_operations must be a positive integer")
        if not isinstance(max_content_bytes, int) or isinstance(max_content_bytes, bool) or max_content_bytes < 1:
            raise WorkspaceProviderError("max_content_bytes must be a positive integer")
        self.max_operations = max_operations
        self.max_content_bytes = max_content_bytes

    def _summary_path(self, state: WorkflowRuntimeState, step: WorkflowStepSpec) -> Path:
        task = _safe_component(state.get("task_id"), fallback="task")
        step_id = _safe_component(step.step_id, fallback="step")
        return (
            self.workspace
            / ".quality"
            / "workflow-provider-runs"
            / task
            / f"{step_id}-{_attempt(state, step)}.workspace.json"
        )

    def _request(self, state: WorkflowRuntimeState, step: WorkflowStepSpec) -> dict[str, Any]:
        target = state.get("target_ref") if isinstance(state.get("target_ref"), Mapping) else {}
        raw_requests = target.get("workspace_requests") if isinstance(target, Mapping) else None
        requests = raw_requests if isinstance(raw_requests, Mapping) else {}
        raw = requests.get(step.step_id)
        if not isinstance(raw, Mapping):
            raw = requests.get("workspace.write")
        if not isinstance(raw, Mapping):
            raise WorkspaceProviderError(
                f"target_ref.workspace_requests requires request for step={step.step_id!r} "
                "or capability='workspace.write'"
            )
        request = dict(raw)
        declared = _text(request.get("capability_id"))
        if declared and declared != "workspace.write":
            raise WorkspaceProviderError("workspace request capability_id must be 'workspace.write'")
        unknown = sorted(set(request) - {"schema", "capability_id", "operations"})
        if unknown:
            raise WorkspaceProviderError(f"workspace request has unknown fields: {unknown}")
        if request.get("schema") not in {None, "workflow-workspace-mutation-request@1"}:
            raise WorkspaceProviderError("workspace request schema is unsupported")
        return request

    def _path_allowed(self, relative_path: str) -> bool:
        if any(fnmatch.fnmatchcase(relative_path, pattern) for pattern in _PROTECTED):
            return False
        return any(fnmatch.fnmatchcase(relative_path, pattern) for pattern in self.allowed_path_patterns)

    def _assert_no_symlink(self, path: Path) -> None:
        current = self.workspace
        for part in path.relative_to(self.workspace).parts:
            current = current / part
            if current.is_symlink():
                raise WorkspaceProviderError(
                    f"workspace operation cannot traverse or replace a symlink: "
                    f"{current.relative_to(self.workspace).as_posix()}"
                )

    @staticmethod
    def _required_digest(value: object, *, field: str) -> str:
        digest = _text(value)
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise WorkspaceProviderError(f"{field} must be an exact lowercase SHA-256")
        return digest

    def _operations(self, request: Mapping[str, Any]) -> tuple[_Operation, ...]:
        rows = request.get("operations")
        if not isinstance(rows, list) or not rows:
            raise WorkspaceProviderError("workspace request requires non-empty operations")
        if len(rows) > self.max_operations:
            raise WorkspaceProviderError("workspace request exceeds max_operations")

        operations: list[_Operation] = []
        seen: set[str] = set()
        for index, raw in enumerate(rows):
            if not isinstance(raw, Mapping):
                raise WorkspaceProviderError(f"workspace operation {index} must be an object")
            unknown = sorted(
                set(raw) - {"operation", "path", "expected_sha256", "content", "content_sha256"}
            )
            if unknown:
                raise WorkspaceProviderError(f"workspace operation {index} has unknown fields: {unknown}")
            operation = _text(raw.get("operation"))
            if operation not in self._OPERATIONS:
                raise WorkspaceProviderError(f"workspace operation {index} is unsupported: {operation!r}")
            relative_path = _relative_path(raw.get("path"))
            if relative_path in seen:
                raise WorkspaceProviderError(f"workspace operation path is duplicated: {relative_path}")
            seen.add(relative_path)
            if not self._path_allowed(relative_path):
                raise WorkspaceProviderError(f"workspace operation path is outside allowed scope: {relative_path}")

            path = self.workspace / relative_path
            self._assert_no_symlink(path)
            if path.exists() and not path.is_file():
                raise WorkspaceProviderError(f"workspace operation target is not a regular file: {relative_path}")

            expected_sha256: str | None = None
            original: bytes | None = None
            original_mode: int | None = None
            if operation == "create":
                if path.exists():
                    raise WorkspaceProviderError(f"create target already exists: {relative_path}")
                if raw.get("expected_sha256") not in {None, ""}:
                    raise WorkspaceProviderError("create operation cannot declare expected_sha256")
            else:
                if not path.is_file():
                    raise WorkspaceProviderError(f"{operation} target does not exist: {relative_path}")
                expected_sha256 = self._required_digest(
                    raw.get("expected_sha256"), field=f"operations[{index}].expected_sha256"
                )
                original = path.read_bytes()
                original_mode = path.stat().st_mode & 0o777
                if _sha256_bytes(original) != expected_sha256:
                    raise WorkspaceProviderError(f"workspace precondition digest is stale: {relative_path}")

            content: bytes | None = None
            content_sha256: str | None = None
            if operation in {"create", "replace"}:
                if not isinstance(raw.get("content"), str):
                    raise WorkspaceProviderError(f"{operation} operation requires UTF-8 text content")
                content = str(raw["content"]).encode("utf-8")
                if len(content) > self.max_content_bytes:
                    raise WorkspaceProviderError(f"workspace content exceeds max_content_bytes: {relative_path}")
                content_sha256 = self._required_digest(
                    raw.get("content_sha256"), field=f"operations[{index}].content_sha256"
                )
                if _sha256_bytes(content) != content_sha256:
                    raise WorkspaceProviderError(f"workspace content digest mismatch: {relative_path}")
            elif raw.get("content") is not None or raw.get("content_sha256") is not None:
                raise WorkspaceProviderError("delete operation cannot declare content")

            operations.append(
                _Operation(
                    operation=operation,
                    relative_path=relative_path,
                    path=path,
                    expected_sha256=expected_sha256,
                    content=content,
                    content_sha256=content_sha256,
                    original=original,
                    original_mode=original_mode,
                )
            )
        return tuple(operations)

    @staticmethod
    def _atomic_write(path: Path, content: bytes, *, mode: int = 0o644) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(raw_temp)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, mode)
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _recheck_precondition(self, operation: _Operation) -> None:
        self._assert_no_symlink(operation.path)
        if operation.operation == "create":
            if operation.path.exists():
                raise WorkspaceProviderError(
                    f"workspace changed after preflight: {operation.relative_path} now exists"
                )
            return
        if not operation.path.is_file() or _file_sha256(operation.path) != operation.expected_sha256:
            raise WorkspaceProviderError(
                f"workspace changed after preflight: {operation.relative_path}"
            )

    def _apply(self, operations: tuple[_Operation, ...]) -> list[dict[str, Any]]:
        applied: list[_Operation] = []
        existing_dirs = {
            path
            for operation in operations
            for path in operation.path.parents
            if path == self.workspace or self.workspace in path.parents
            if path.exists()
        }
        try:
            for operation in operations:
                self._recheck_precondition(operation)
                if operation.operation == "delete":
                    operation.path.unlink()
                else:
                    self._atomic_write(
                        operation.path,
                        operation.content or b"",
                        mode=operation.original_mode or 0o644,
                    )
                applied.append(operation)

            receipt: list[dict[str, Any]] = []
            for operation in operations:
                if operation.operation == "delete":
                    if operation.path.exists():
                        raise WorkspaceProviderError(
                            f"delete postcondition failed: {operation.relative_path}"
                        )
                    after = None
                else:
                    self._assert_no_symlink(operation.path)
                    if not operation.path.is_file():
                        raise WorkspaceProviderError(
                            f"write postcondition failed: {operation.relative_path}"
                        )
                    after = _file_sha256(operation.path)
                    if after != operation.content_sha256:
                        raise WorkspaceProviderError(
                            f"write postcondition digest failed: {operation.relative_path}"
                        )
                receipt.append(
                    {
                        "operation": operation.operation,
                        "path": operation.relative_path,
                        "before_sha256": operation.expected_sha256,
                        "after_sha256": after,
                    }
                )
            return receipt
        except Exception as exc:
            rollback_errors: list[str] = []
            for operation in reversed(applied):
                try:
                    if operation.original is None:
                        if operation.path.exists() and not operation.path.is_symlink():
                            operation.path.unlink()
                    else:
                        self._atomic_write(
                            operation.path,
                            operation.original,
                            mode=operation.original_mode or 0o644,
                        )
                except Exception as rollback_exc:  # pragma: no cover - exceptional filesystem failure
                    rollback_errors.append(
                        f"{operation.relative_path}:{type(rollback_exc).__name__}"
                    )
            candidate_dirs = sorted(
                {
                    path
                    for operation in operations
                    for path in operation.path.parents
                    if path != self.workspace and self.workspace in path.parents
                },
                key=lambda value: len(value.parts),
                reverse=True,
            )
            for directory in candidate_dirs:
                if directory not in existing_dirs:
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            if rollback_errors:
                raise WorkspaceProviderError(
                    f"workspace transaction failed and rollback was incomplete: {rollback_errors}"
                ) from exc
            raise WorkspaceProviderError(f"workspace transaction rolled back: {exc}") from exc

    def invoke(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        summary_path = self._summary_path(state, step)
        try:
            if binding.provider_id != self.provider_id or binding.provider_type != self.provider_type:
                raise WorkspaceProviderError("local.workspace adapter received another provider binding")
            if binding.capability_id not in self._SUPPORTED or not binding.mutates or binding.external_wait:
                raise WorkspaceProviderError("local.workspace only implements mutating workspace.write")
            request = self._request(state, step)
            receipt = self._apply(self._operations(request))
            payload = {
                "schema": "workflow-workspace-provider-result@1",
                "provider_id": self.provider_id,
                "capability_id": binding.capability_id,
                "step_id": step.step_id,
                "attempt": _attempt(state, step),
                "status": "PASS",
                "outcome": "green",
                "operations": receipt,
                "authority_effect": False,
                "completion_authority_changed": False,
                "quality_authority_changed": False,
                "merge_allowed": False,
                "production_closed": False,
            }
            _write_json(summary_path, payload)
            summary_ref = f"file:{summary_path.relative_to(self.workspace).as_posix()}"
            path_refs = tuple(f"workspace:{row['path']}@{row['after_sha256'] or 'deleted'}" for row in receipt)
            return StepDispatchResult(
                outcome="green",
                evidence_refs=_evidence((*path_refs, summary_ref)),
                payload=payload,
            )
        except Exception as exc:
            payload = {
                "schema": "workflow-workspace-provider-result@1",
                "provider_id": self.provider_id,
                "capability_id": binding.capability_id,
                "step_id": step.step_id,
                "attempt": _attempt(state, step),
                "status": "BLOCKED",
                "outcome": "blocked",
                "error": f"{type(exc).__name__}: {exc}",
                "authority_effect": False,
                "completion_authority_changed": False,
                "quality_authority_changed": False,
                "merge_allowed": False,
                "production_closed": False,
            }
            _write_json(summary_path, payload)
            return StepDispatchResult(
                outcome="blocked",
                evidence_refs=(f"file:{summary_path.relative_to(self.workspace).as_posix()}",),
                payload=payload,
            )


__all__ = ["StructuredWorkspaceProviderAdapter", "WorkspaceProviderError"]
