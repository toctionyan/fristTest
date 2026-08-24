from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from capability_registry import CapabilityBinding
from repair_governance import load_chain, permit_path_decision
from workflow_graph_contract import WorkflowStepSpec


class GovernedWriteAuthorityError(RuntimeError):
    """Raised when a mutating dispatch is not covered by the active ChangePermit."""


_SUPPORTED = frozenset(
    {
        "workspace.write",
        "vcs.commit.create",
        "code_review.pull_request.create",
    }
)
_GENERIC_MERGE = "code_review.pull_request.merge"


def _text(value: object) -> str:
    return str(value or "").strip()


def _safe_id(value: object, *, field: str) -> str:
    text = _text(value)
    if not text or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-" for char in text):
        raise GovernedWriteAuthorityError(f"{field} must be a stable identifier")
    return text


def _relative_path(value: object, *, field: str) -> str:
    text = _text(value).replace("\\", "/")
    path = PurePosixPath(text)
    if (
        not text
        or text.startswith("/")
        or path.as_posix() != text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise GovernedWriteAuthorityError(f"{field} must be a canonical repository path")
    return path.as_posix()


def _bounded(workspace: Path, relative: str, *, field: str, must_exist: bool) -> Path:
    path = workspace / relative
    current = workspace
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise GovernedWriteAuthorityError(f"{field} cannot traverse a symlink")
    try:
        path.resolve().relative_to(workspace)
    except ValueError as exc:
        raise GovernedWriteAuthorityError(f"{field} escapes the project workspace") from exc
    if must_exist and not path.is_file():
        raise GovernedWriteAuthorityError(f"{field} is missing: {relative}")
    return path


def _load_contract(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernedWriteAuthorityError("active change contract is unreadable") from exc
    if not isinstance(raw, dict):
        raise GovernedWriteAuthorityError("active change contract must be an object")
    return raw


def _request(
    target: Mapping[str, Any],
    *,
    bucket_name: str,
    step_id: str,
    capability_id: str,
) -> dict[str, Any]:
    bucket = target.get(bucket_name)
    if not isinstance(bucket, Mapping):
        raise GovernedWriteAuthorityError(
            f"target_ref.{bucket_name} is required for {capability_id}"
        )
    raw = bucket.get(step_id)
    if not isinstance(raw, Mapping):
        raw = bucket.get(capability_id)
    if not isinstance(raw, Mapping):
        raise GovernedWriteAuthorityError(
            f"target_ref.{bucket_name} requires request for step={step_id!r} "
            f"or capability={capability_id!r}"
        )
    return dict(raw)


def _workspace_paths(request: Mapping[str, Any]) -> tuple[str, ...]:
    operations = request.get("operations")
    if not isinstance(operations, list) or not operations:
        raise GovernedWriteAuthorityError("workspace.write requires non-empty operations")
    paths: list[str] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            raise GovernedWriteAuthorityError(
                f"workspace operation {index} must be an object"
            )
        paths.append(
            _relative_path(operation.get("path"), field=f"operations[{index}].path")
        )
    return _unique(paths, field="workspace operation paths")


def _publication_paths(request: Mapping[str, Any], *, capability_id: str) -> tuple[str, ...]:
    raw = request.get("changed_paths")
    if not isinstance(raw, list) or not raw:
        raise GovernedWriteAuthorityError(
            f"{capability_id} requires non-empty changed_paths for pre-effect authorization"
        )
    return _unique(
        (_relative_path(value, field="changed_path") for value in raw),
        field="publication changed_paths",
    )


def _unique(values: Iterable[str], *, field: str) -> tuple[str, ...]:
    result = tuple(values)
    if not result or len(result) != len(set(result)):
        raise GovernedWriteAuthorityError(f"{field} must be non-empty and unique")
    return result


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ChangePermitWriteAuthorityGuard:
    """Adapt the repository's existing ChangePermit into runtime write checks.

    The guard grants nothing itself.  Every call reloads and validates the one
    active governance chain, binds it to the Workflow target, extracts exact
    paths before any Provider effect, and delegates path semantics to
    ``repair_governance.permit_path_decision``.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        active_contract_path: str = "governance/active-change.json",
        audit_root: str = ".harness/runtime/authority-checks",
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise GovernedWriteAuthorityError("workspace must be an existing directory")
        self.active_contract_path = _relative_path(
            active_contract_path, field="active_contract_path"
        )
        self.audit_root = _relative_path(audit_root, field="audit_root")
        if not self.audit_root.startswith(".harness/"):
            raise GovernedWriteAuthorityError("audit_root must stay beneath .harness")

    def _paths(
        self,
        *,
        capability_id: str,
        step: WorkflowStepSpec,
        target: Mapping[str, Any],
    ) -> tuple[str, ...]:
        if capability_id == _GENERIC_MERGE:
            raise GovernedWriteAuthorityError(
                "generic WriteAuthorityGuard never authorizes pull-request merge"
            )
        if capability_id not in _SUPPORTED:
            raise GovernedWriteAuthorityError(
                f"mutating capability has no exact-scope authority adapter: {capability_id}"
            )
        if capability_id == "workspace.write":
            return _workspace_paths(
                _request(
                    target,
                    bucket_name="workspace_requests",
                    step_id=step.step_id,
                    capability_id=capability_id,
                )
            )
        return _publication_paths(
            _request(
                target,
                bucket_name="publication_requests",
                step_id=step.step_id,
                capability_id=capability_id,
            ),
            capability_id=capability_id,
        )

    def assert_allowed(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: Mapping[str, Any],
    ) -> None:
        if not binding.mutates:
            raise GovernedWriteAuthorityError("write guard received a non-mutating binding")
        capability_id = _text(binding.capability_id)
        task_id = _safe_id(state.get("task_id"), field="task_id")
        workflow_id = _safe_id(state.get("workflow_id"), field="workflow_id")
        target = state.get("target_ref")
        if not isinstance(target, Mapping):
            raise GovernedWriteAuthorityError("mutating dispatch requires target_ref")

        contract_path = _bounded(
            self.workspace,
            self.active_contract_path,
            field="active_contract_path",
            must_exist=True,
        )
        contract = _load_contract(contract_path)
        if contract.get("status") != "implementing":
            raise GovernedWriteAuthorityError(
                "active change contract must be in implementing state"
            )
        change_id = _safe_id(contract.get("change_id"), field="contract.change_id")
        if _text(target.get("change_id")) != change_id:
            raise GovernedWriteAuthorityError("target_ref change_id does not match active contract")
        try:
            chain = load_chain(self.workspace, contract)
        except ValueError as exc:
            raise GovernedWriteAuthorityError(f"active ChangePermit is invalid: {exc}") from exc
        if _text(target.get("permit_digest")) != chain.permit_digest:
            raise GovernedWriteAuthorityError(
                "target_ref permit_digest does not match active ChangePermit"
            )

        paths = self._paths(capability_id=capability_id, step=step, target=target)
        for path in paths:
            allowed, reason = permit_path_decision(self.workspace, contract, path)
            if not allowed:
                raise GovernedWriteAuthorityError(reason)

        attempts = state.get("step_attempts")
        attempt = int(attempts.get(step.step_id) or 0) + 1 if isinstance(attempts, Mapping) else 1
        audit = {
            "schema": "change-permit-write-authority-check@1",
            "task_id": task_id,
            "workflow_id": workflow_id,
            "step_id": step.step_id,
            "attempt": attempt,
            "capability_id": capability_id,
            "change_id": change_id,
            "permit_digest": chain.permit_digest,
            "paths": list(paths),
            "decision": "ALLOW",
            "generic_merge_authority": False,
            "completion_authority_changed": False,
            "authority_effect": False,
        }
        capability_file = _safe_id(capability_id, field="capability_id").replace(":", "-")
        audit_path = _bounded(
            self.workspace,
            f"{self.audit_root}/{task_id}/{step.step_id}-{capability_file}-{attempt}.json",
            field="authority audit path",
            must_exist=False,
        )
        _atomic_json(audit_path, audit)


__all__ = [
    "ChangePermitWriteAuthorityGuard",
    "GovernedWriteAuthorityError",
]
