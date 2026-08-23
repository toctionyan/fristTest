from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from host_skill_bridge import DurableHostSkillBridge
from langgraph_workflow_runtime import (
    RUNTIME_STATUS_BLOCKED,
    RUNTIME_STATUS_END,
    RUNTIME_STATUS_HUMAN_GATE,
    RUNTIME_STATUS_WAITING_EXTERNAL,
    RUNTIME_STATUS_WAITING_HOST,
)
from starter_runtime import (
    HostStarterSelectionResolution,
    LoadedStarterRegistration,
    ResolvedStarterEntrypoint,
    StarterWorkflowRuntime,
    build_starter_host_selection_request,
    load_starter_registration,
    resolve_starter_host_selection,
)
from task_run import TaskRunStore, stable_task_id
from workflow_dispatcher import ProviderAdapterRegistry, WriteAuthorityGuard


STARTER_HOST_SESSION_SCHEMA = "starter-host-session@1"
STARTER_HOST_NEXT_ACTION_SCHEMA = "starter-host-next-action@1"

PHASE_AWAITING_SELECTION = "AWAITING_SELECTION"
PHASE_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
PHASE_READY_TO_START = "READY_TO_START"
PHASE_STARTING = "STARTING"
PHASE_WAITING_HOST = "WAITING_HOST"
PHASE_WAITING_EXTERNAL = "WAITING_EXTERNAL"
PHASE_HUMAN_GATE = "HUMAN_GATE"
PHASE_RESUMING_HOST = "RESUMING_HOST"
PHASE_RESUMING_EXTERNAL = "RESUMING_EXTERNAL"
PHASE_RESUMING_HUMAN = "RESUMING_HUMAN"
PHASE_VALIDATING = "VALIDATING"
PHASE_BLOCKED = "BLOCKED"

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")


class StarterHostOrchestrationError(RuntimeError):
    """Raised when a Host session transition is stale, invalid, or unsafe."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _identifier(value: object, *, field: str) -> str:
    text = _text(value)
    if not text or not _SAFE_ID.fullmatch(text):
        raise StarterHostOrchestrationError(f"{field} must be a stable identifier")
    return text


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded_root(workspace: Path, value: str | Path, *, field: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise StarterHostOrchestrationError(f"{field} must be workspace-relative and bounded")
    path = workspace / relative
    try:
        path.resolve().relative_to(workspace)
    except ValueError as exc:
        raise StarterHostOrchestrationError(f"{field} escapes the project workspace") from exc
    return path


def project_runtime_action(
    execution: Mapping[str, Any],
    *,
    session_id: str,
    task_id: str,
) -> tuple[str, dict[str, Any]]:
    """Project canonical runtime state into one closed Host-facing next action."""

    state = execution.get("runtime_state")
    if not isinstance(state, Mapping):
        raise StarterHostOrchestrationError("runtime execution requires runtime_state")
    workflow_id = _identifier(state.get("workflow_id"), field="workflow_id")
    if _text(state.get("task_id")) != task_id:
        raise StarterHostOrchestrationError("runtime state task_id does not match Host session")
    status = _text(state.get("runtime_status"))
    details: dict[str, Any]
    if status == RUNTIME_STATUS_WAITING_HOST:
        wait = state.get("host_wait")
        if not isinstance(wait, Mapping) or not wait:
            raise StarterHostOrchestrationError("WAITING_HOST requires an exact host_wait handle")
        phase = PHASE_WAITING_HOST
        kind = "EXECUTE_HOST_SKILL"
        details = {"host_wait": dict(wait)}
    elif status == RUNTIME_STATUS_WAITING_EXTERNAL:
        wait = state.get("external_wait")
        if not isinstance(wait, Mapping) or not wait:
            raise StarterHostOrchestrationError(
                "WAITING_EXTERNAL requires an exact external_wait handle"
            )
        phase = PHASE_WAITING_EXTERNAL
        kind = "WAIT_EXTERNAL_EVENT"
        details = {"external_wait": dict(wait)}
    elif status == RUNTIME_STATUS_HUMAN_GATE:
        gate = state.get("human_gate")
        if not isinstance(gate, Mapping) or not gate:
            raise StarterHostOrchestrationError("HUMAN_GATE requires an exact human_gate handle")
        phase = PHASE_HUMAN_GATE
        kind = "REQUEST_HUMAN_DECISION"
        details = {"human_gate": dict(gate)}
    elif status == RUNTIME_STATUS_END:
        if _text(execution.get("taskrun_status")) != "VALIDATING":
            raise StarterHostOrchestrationError(
                "Graph END must be projected through TaskRun VALIDATING"
            )
        phase = PHASE_VALIDATING
        kind = "EVALUATE_COMPLETION_POLICY"
        details = {
            "taskrun_status": "VALIDATING",
            "taskrun_phase": _text(execution.get("taskrun_phase")),
        }
    elif status == RUNTIME_STATUS_BLOCKED:
        phase = PHASE_BLOCKED
        kind = "INSPECT_BLOCKER"
        details = {"runtime_error": _text(state.get("runtime_error"))}
    else:
        raise StarterHostOrchestrationError(
            f"runtime returned a non-yielding status to Host orchestration: {status!r}"
        )
    action = {
        "schema": STARTER_HOST_NEXT_ACTION_SCHEMA,
        "kind": kind,
        "session_id": session_id,
        "task_id": task_id,
        "workflow_id": workflow_id,
        "details": details,
        "policy": {
            "selection_is_execution": False,
            "selection_grants_write_authority": False,
            "graph_end_completes_taskrun": False,
            "automatic_merge": False,
            "completion_authority": "TaskRun",
            "authority_effect": False,
        },
    }
    return phase, action


class StarterHostOrchestrator:
    """Durably order a ChatGPT/Codex interaction around the existing runtime.

    This controller owns only session order and correlation. Semantic selection,
    write authority, Skill evidence, Provider effects, graph execution, TaskRun
    lifecycle, and completion remain owned by their existing components.
    """

    def __init__(
        self,
        *,
        registry_workspace: Path,
        project_workspace: Path,
        registration: Path,
        host_id: str,
        provider_adapters: ProviderAdapterRegistry,
        checkpointer: Any,
        workspace_fingerprint: str | None,
        write_authority_guard: WriteAuthorityGuard | None = None,
        human_gate_adapter: Any | None = None,
        session_root: str | Path = ".harness/runtime/host-sessions",
        taskrun_root: str | Path = ".harness/taskruns",
        runtime_factory: Callable[..., StarterWorkflowRuntime] = StarterWorkflowRuntime,
    ) -> None:
        self.registry_workspace = Path(registry_workspace).resolve()
        self.project_workspace = Path(project_workspace).resolve()
        if not self.project_workspace.is_dir():
            raise StarterHostOrchestrationError("project_workspace must be a directory")
        self.registration = Path(registration)
        if not self.registration.is_absolute():
            self.registration = self.project_workspace / self.registration
        self.loaded: LoadedStarterRegistration = load_starter_registration(
            project_workspace=self.project_workspace,
            registration=self.registration,
            registry_workspace=self.registry_workspace,
        )
        self.host_id = _identifier(host_id.lower(), field="host_id")
        if self.host_id not in {"chatgpt", "codex"}:
            raise StarterHostOrchestrationError(f"unsupported Host: {self.host_id}")
        self.provider_adapters = provider_adapters
        self.checkpointer = checkpointer
        self.workspace_fingerprint = workspace_fingerprint
        self.write_authority_guard = write_authority_guard
        self.human_gate_adapter = human_gate_adapter
        self.runtime_factory = runtime_factory
        self.session_root = _bounded_root(
            self.project_workspace, session_root, field="session_root"
        )
        self.taskrun_root = _bounded_root(
            self.project_workspace, taskrun_root, field="taskrun_root"
        )
        self.skill_host = DurableHostSkillBridge(
            workspace=self.project_workspace,
            host_id=self.host_id,
            canonical_skill_paths=self.loaded.skill_paths,
        )

    def _session_path(self, session_id: str) -> Path:
        return self.session_root / f"{_identifier(session_id, field='session_id')}.json"

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise StarterHostOrchestrationError(f"Host session is missing: {path.name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StarterHostOrchestrationError(
                f"Host session is unreadable: {path.name}"
            ) from exc
        if not isinstance(payload, dict):
            raise StarterHostOrchestrationError("Host session must be a JSON object")
        return payload

    @staticmethod
    def _atomic_replace(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _validate_identity(self, payload: Mapping[str, Any], *, session_id: str) -> None:
        if payload.get("schema") != STARTER_HOST_SESSION_SCHEMA:
            raise StarterHostOrchestrationError("unsupported Host session schema")
        if payload.get("session_id") != session_id:
            raise StarterHostOrchestrationError("Host session identity mismatch")
        if payload.get("host_id") != self.host_id:
            raise StarterHostOrchestrationError("Host session host_id mismatch")
        if payload.get("registration_ref") != self.loaded.registration_ref:
            raise StarterHostOrchestrationError("Host session registration_ref drifted")
        if payload.get("registration_sha256") != self.loaded.payload["registration_sha256"]:
            raise StarterHostOrchestrationError("Host session registration digest drifted")
        if not isinstance(payload.get("revision"), int) or int(payload["revision"]) < 0:
            raise StarterHostOrchestrationError("Host session revision is invalid")
        request = payload.get("selection_request")
        if not isinstance(request, Mapping):
            raise StarterHostOrchestrationError("Host session selection_request is missing")
        expected = build_starter_host_selection_request(
            self.loaded,
            registry_workspace=self.registry_workspace,
            host_id=self.host_id,
            user_request=_text(request.get("user_request")),
        )
        if dict(request) != expected:
            raise StarterHostOrchestrationError("Host session selection request is stale or tampered")
        fingerprint = payload.get("session_fingerprint_sha256")
        identity = {
            "session_id": session_id,
            "host_id": self.host_id,
            "registration_ref": self.loaded.registration_ref,
            "registration_sha256": self.loaded.payload["registration_sha256"],
            "request_fingerprint_sha256": expected["request_fingerprint_sha256"],
        }
        if fingerprint != _digest(identity):
            raise StarterHostOrchestrationError("Host session fingerprint mismatch")

    def read(self, session_id: str) -> dict[str, Any]:
        session_id = _identifier(session_id, field="session_id")
        payload = self._load_json(self._session_path(session_id))
        self._validate_identity(payload, session_id=session_id)
        return payload

    def _update(
        self,
        session_id: str,
        *,
        expected_revision: int,
        allowed_phases: set[str],
        mutate: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        path = self._session_path(session_id)
        lock = path.with_suffix(path.suffix + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            payload = self._load_json(path)
            self._validate_identity(payload, session_id=session_id)
            if payload["revision"] != expected_revision:
                raise StarterHostOrchestrationError(
                    "Host session revision conflict: "
                    f"expected={expected_revision} actual={payload['revision']}"
                )
            phase = _text(payload.get("phase"))
            if phase not in allowed_phases:
                raise StarterHostOrchestrationError(
                    f"Host session phase {phase!r} does not allow this transition"
                )
            mutate(payload)
            payload["revision"] = expected_revision + 1
            self._atomic_replace(path, payload)
            return payload

    def open(self, *, session_id: str, user_request: str) -> dict[str, Any]:
        session_id = _identifier(session_id, field="session_id")
        request = build_starter_host_selection_request(
            self.loaded,
            registry_workspace=self.registry_workspace,
            host_id=self.host_id,
            user_request=user_request,
        )
        identity = {
            "session_id": session_id,
            "host_id": self.host_id,
            "registration_ref": self.loaded.registration_ref,
            "registration_sha256": self.loaded.payload["registration_sha256"],
            "request_fingerprint_sha256": request["request_fingerprint_sha256"],
        }
        payload = {
            "schema": STARTER_HOST_SESSION_SCHEMA,
            "session_id": session_id,
            "session_fingerprint_sha256": _digest(identity),
            "revision": 0,
            "phase": PHASE_AWAITING_SELECTION,
            "host_id": self.host_id,
            "registration_ref": self.loaded.registration_ref,
            "registration_sha256": self.loaded.payload["registration_sha256"],
            "selection_request": request,
            "selection": None,
            "confirmation": None,
            "resolution": None,
            "task_id": None,
            "taskrun_ref": None,
            "target_ref": None,
            "runtime_state": None,
            "taskrun_status": None,
            "taskrun_phase": None,
            "next_action": {
                "schema": STARTER_HOST_NEXT_ACTION_SCHEMA,
                "kind": "SELECT_EXACT_ENTRYPOINT",
                "session_id": session_id,
                "task_id": None,
                "workflow_id": None,
                "details": {"selection_request": request},
                "policy": {
                    "selection_is_execution": False,
                    "selection_grants_write_authority": False,
                    "graph_end_completes_taskrun": False,
                    "automatic_merge": False,
                    "completion_authority": "TaskRun",
                    "authority_effect": False,
                },
            },
            "last_error": None,
            "policy": {
                "host_interprets_language": True,
                "repository_keyword_router": False,
                "one_taskrun_per_session": True,
                "write_authority_granted": False,
                "automatic_merge": False,
                "completion_authority": "TaskRun",
                "authority_effect": False,
            },
        }
        path = self._session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(path.suffix + ".lock")
        with lock.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            if path.exists():
                existing = self._load_json(path)
                self._validate_identity(existing, session_id=session_id)
                if existing != payload:
                    raise StarterHostOrchestrationError(
                        "Host session already exists with a different state"
                    )
                return existing
            self._atomic_replace(path, payload)
        return payload

    def _selection_resolution(
        self,
        session: Mapping[str, Any],
        *,
        selection: Mapping[str, Any],
        confirmation: Mapping[str, Any] | None,
    ) -> HostStarterSelectionResolution:
        return resolve_starter_host_selection(
            self.loaded,
            registry_workspace=self.registry_workspace,
            request=dict(session["selection_request"]),
            selection=selection,
            confirmation=confirmation,
        )

    def select(
        self,
        *,
        session_id: str,
        expected_revision: int,
        selection: Mapping[str, Any],
        confirmation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        def apply(payload: dict[str, Any]) -> None:
            resolution = self._selection_resolution(
                payload, selection=selection, confirmation=confirmation
            )
            payload["selection"] = dict(selection)
            payload["confirmation"] = dict(confirmation) if confirmation is not None else None
            payload["resolution"] = resolution.record
            if resolution.resolved is None:
                payload["phase"] = PHASE_AWAITING_CONFIRMATION
                kind = "CONFIRM_EXACT_EFFECT_PREVIEW"
            else:
                payload["phase"] = PHASE_READY_TO_START
                kind = "START_TASKRUN"
            payload["next_action"] = {
                "schema": STARTER_HOST_NEXT_ACTION_SCHEMA,
                "kind": kind,
                "session_id": session_id,
                "task_id": None,
                "workflow_id": resolution.record["selected_workflow"],
                "details": {"resolution": resolution.record},
                "policy": dict(payload["next_action"]["policy"]),
            }

        return self._update(
            _identifier(session_id, field="session_id"),
            expected_revision=expected_revision,
            allowed_phases={PHASE_AWAITING_SELECTION},
            mutate=apply,
        )

    def confirm(
        self,
        *,
        session_id: str,
        expected_revision: int,
        confirmation: Mapping[str, Any],
    ) -> dict[str, Any]:
        def apply(payload: dict[str, Any]) -> None:
            selection = payload.get("selection")
            if not isinstance(selection, Mapping):
                raise StarterHostOrchestrationError(
                    "Host session has no selection to confirm"
                )
            resolution = self._selection_resolution(
                payload, selection=selection, confirmation=confirmation
            )
            if resolution.resolved is None:
                raise StarterHostOrchestrationError(
                    "exact confirmation did not resolve the Starter selection"
                )
            payload["confirmation"] = dict(confirmation)
            payload["resolution"] = resolution.record
            payload["phase"] = PHASE_READY_TO_START
            payload["next_action"] = {
                "schema": STARTER_HOST_NEXT_ACTION_SCHEMA,
                "kind": "START_TASKRUN",
                "session_id": session_id,
                "task_id": None,
                "workflow_id": resolution.record["selected_workflow"],
                "details": {"resolution": resolution.record},
                "policy": dict(payload["next_action"]["policy"]),
            }

        return self._update(
            _identifier(session_id, field="session_id"),
            expected_revision=expected_revision,
            allowed_phases={PHASE_AWAITING_CONFIRMATION},
            mutate=apply,
        )

    def _resolved(self, session: Mapping[str, Any]) -> ResolvedStarterEntrypoint:
        selection = session.get("selection")
        confirmation = session.get("confirmation")
        if not isinstance(selection, Mapping):
            raise StarterHostOrchestrationError("Host session selection is missing")
        resolution = self._selection_resolution(
            session,
            selection=selection,
            confirmation=confirmation if isinstance(confirmation, Mapping) else None,
        )
        if resolution.resolved is None:
            raise StarterHostOrchestrationError("Host session selection is not executable")
        if session.get("resolution") != resolution.record:
            raise StarterHostOrchestrationError("Host session resolution is stale or tampered")
        return resolution.resolved

    def _task_binding(
        self, session: Mapping[str, Any], resolved: ResolvedStarterEntrypoint
    ) -> dict[str, Any]:
        return {
            "host_session_id": session["session_id"],
            "host_session_fingerprint_sha256": session["session_fingerprint_sha256"],
            "registration_sha256": session["registration_sha256"],
            "entrypoint": resolved.entrypoint,
            "workflow_id": resolved.workflow.workflow_id,
        }

    def _task_store(
        self,
        session: Mapping[str, Any],
        resolved: ResolvedStarterEntrypoint,
    ) -> TaskRunStore:
        task_id = _identifier(session.get("task_id"), field="task_id")
        expected_ref = f"file:{self.taskrun_root.relative_to(self.project_workspace).as_posix()}/{task_id}.json"
        if session.get("taskrun_ref") != expected_ref:
            raise StarterHostOrchestrationError("Host session taskrun_ref is stale or tampered")
        return TaskRunStore.open_or_create(
            self.taskrun_root / f"{task_id}.json",
            task_id=task_id,
            task_kind="starter-host-session",
            binding=self._task_binding(session, resolved),
            required_conditions=("workflow-evidence", "completion-policy"),
            current_workspace_fingerprint=self.workspace_fingerprint,
        )

    def _runtime(
        self,
        session: Mapping[str, Any],
        resolved: ResolvedStarterEntrypoint,
    ) -> StarterWorkflowRuntime:
        return self.runtime_factory(
            registry_workspace=self.registry_workspace,
            resolved=resolved,
            skill_host=self.skill_host,
            provider_adapters=self.provider_adapters,
            checkpointer=self.checkpointer,
            taskrun_store=self._task_store(session, resolved),
            workspace_fingerprint=self.workspace_fingerprint,
            write_authority_guard=self.write_authority_guard,
            human_gate_adapter=self.human_gate_adapter,
        )

    def _record_execution(
        self,
        *,
        session_id: str,
        expected_revision: int,
        allowed_phase: str,
        execution: Mapping[str, Any],
    ) -> dict[str, Any]:
        task_id = _identifier(execution.get("runtime_state", {}).get("task_id"), field="task_id")
        phase, action = project_runtime_action(
            execution, session_id=session_id, task_id=task_id
        )

        def apply(payload: dict[str, Any]) -> None:
            if payload.get("task_id") != task_id:
                raise StarterHostOrchestrationError(
                    "runtime execution belongs to another TaskRun"
                )
            payload["phase"] = phase
            payload["runtime_state"] = dict(execution["runtime_state"])
            payload["taskrun_status"] = execution.get("taskrun_status")
            payload["taskrun_phase"] = execution.get("taskrun_phase")
            payload["next_action"] = action
            payload["last_error"] = None

        return self._update(
            session_id,
            expected_revision=expected_revision,
            allowed_phases={allowed_phase},
            mutate=apply,
        )

    def _record_runtime_failure(
        self,
        *,
        session_id: str,
        expected_revision: int,
        allowed_phase: str,
        error: Exception,
    ) -> None:
        def apply(payload: dict[str, Any]) -> None:
            payload["phase"] = PHASE_BLOCKED
            payload["last_error"] = f"{type(error).__name__}: {error}"
            payload["next_action"] = {
                "schema": STARTER_HOST_NEXT_ACTION_SCHEMA,
                "kind": "INSPECT_BLOCKER",
                "session_id": session_id,
                "task_id": payload.get("task_id"),
                "workflow_id": (payload.get("resolution") or {}).get("selected_workflow"),
                "details": {"error": payload["last_error"]},
                "policy": dict(payload["next_action"]["policy"]),
            }

        self._update(
            session_id,
            expected_revision=expected_revision,
            allowed_phases={allowed_phase},
            mutate=apply,
        )

    def start(
        self,
        *,
        session_id: str,
        expected_revision: int,
        target_ref: Mapping[str, Any],
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, field="session_id")
        initial = self.read(session_id)
        resolved = self._resolved(initial)
        task_id = stable_task_id(
            "starter-host",
            {
                "session_id": session_id,
                "session_fingerprint_sha256": initial["session_fingerprint_sha256"],
                "workflow_id": resolved.workflow.workflow_id,
            },
        )
        relative = (self.taskrun_root / f"{task_id}.json").relative_to(
            self.project_workspace
        )

        def claim(payload: dict[str, Any]) -> None:
            # Re-resolve while the CAS lock is held so a stale/tampered selection
            # cannot be used between the read and start claim.
            current = self._resolved(payload)
            if current.workflow.workflow_id != resolved.workflow.workflow_id:
                raise StarterHostOrchestrationError("Host session Workflow changed before start")
            payload["phase"] = PHASE_STARTING
            payload["task_id"] = task_id
            payload["taskrun_ref"] = f"file:{relative.as_posix()}"
            payload["target_ref"] = json.loads(_canonical(dict(target_ref)))
            payload["next_action"] = {
                "schema": STARTER_HOST_NEXT_ACTION_SCHEMA,
                "kind": "STARTING_TASKRUN",
                "session_id": session_id,
                "task_id": task_id,
                "workflow_id": resolved.workflow.workflow_id,
                "details": {},
                "policy": dict(payload["next_action"]["policy"]),
            }

        claimed = self._update(
            session_id,
            expected_revision=expected_revision,
            allowed_phases={PHASE_READY_TO_START},
            mutate=claim,
        )
        try:
            runtime = self._runtime(claimed, resolved)
            execution = runtime.start(target_ref=dict(target_ref))
        except Exception as exc:
            self._record_runtime_failure(
                session_id=session_id,
                expected_revision=claimed["revision"],
                allowed_phase=PHASE_STARTING,
                error=exc,
            )
            raise StarterHostOrchestrationError(
                f"Starter Host session start blocked: {type(exc).__name__}: {exc}"
            ) from exc
        return self._record_execution(
            session_id=session_id,
            expected_revision=claimed["revision"],
            allowed_phase=PHASE_STARTING,
            execution=execution,
        )

    def _claim_resume(
        self,
        *,
        session_id: str,
        expected_revision: int,
        waiting_phase: str,
        resuming_phase: str,
    ) -> dict[str, Any]:
        def apply(payload: dict[str, Any]) -> None:
            if not isinstance(payload.get("runtime_state"), Mapping):
                raise StarterHostOrchestrationError(
                    "Host session has no runtime state to resume"
                )
            payload["phase"] = resuming_phase
            payload["next_action"] = {
                "schema": STARTER_HOST_NEXT_ACTION_SCHEMA,
                "kind": resuming_phase,
                "session_id": session_id,
                "task_id": payload["task_id"],
                "workflow_id": (payload.get("resolution") or {}).get("selected_workflow"),
                "details": {},
                "policy": dict(payload["next_action"]["policy"]),
            }

        return self._update(
            session_id,
            expected_revision=expected_revision,
            allowed_phases={waiting_phase},
            mutate=apply,
        )

    def submit_host_result(
        self,
        *,
        session_id: str,
        expected_revision: int,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, field="session_id")
        current = self.read(session_id)
        state = current.get("runtime_state")
        wait = state.get("host_wait") if isinstance(state, Mapping) else None
        execution_id = _text(result.get("execution_id"))
        if not isinstance(wait, Mapping) or execution_id != wait.get("execution_id"):
            raise StarterHostOrchestrationError(
                "Host result does not match the active WAITING_HOST execution"
            )
        claimed = self._claim_resume(
            session_id=session_id,
            expected_revision=expected_revision,
            waiting_phase=PHASE_WAITING_HOST,
            resuming_phase=PHASE_RESUMING_HOST,
        )
        resolved = self._resolved(claimed)
        try:
            pointer = self.skill_host.submit_result(
                execution_id=execution_id, result=result
            )
            runtime = self._runtime(claimed, resolved)
            execution = runtime.resume(
                state=dict(claimed["runtime_state"]),
                host_execution_result=pointer,
                evidence_refs=(pointer["result_ref"],),
                correlation_ref=execution_id,
            )
        except Exception as exc:
            self._record_runtime_failure(
                session_id=session_id,
                expected_revision=claimed["revision"],
                allowed_phase=PHASE_RESUMING_HOST,
                error=exc,
            )
            raise StarterHostOrchestrationError(
                f"Host result resume blocked: {type(exc).__name__}: {exc}"
            ) from exc
        return self._record_execution(
            session_id=session_id,
            expected_revision=claimed["revision"],
            allowed_phase=PHASE_RESUMING_HOST,
            execution=execution,
        )

    def resume_external(
        self,
        *,
        session_id: str,
        expected_revision: int,
        event: Mapping[str, Any],
        evidence_refs: Iterable[str],
        correlation_ref: str,
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, field="session_id")
        refs = tuple(_text(ref) for ref in evidence_refs if _text(ref))
        if not refs:
            raise StarterHostOrchestrationError("external resume requires durable evidence")
        claimed = self._claim_resume(
            session_id=session_id,
            expected_revision=expected_revision,
            waiting_phase=PHASE_WAITING_EXTERNAL,
            resuming_phase=PHASE_RESUMING_EXTERNAL,
        )
        resolved = self._resolved(claimed)
        try:
            execution = self._runtime(claimed, resolved).resume(
                state=dict(claimed["runtime_state"]),
                external_event=event,
                evidence_refs=refs,
                correlation_ref=correlation_ref,
            )
        except Exception as exc:
            self._record_runtime_failure(
                session_id=session_id,
                expected_revision=claimed["revision"],
                allowed_phase=PHASE_RESUMING_EXTERNAL,
                error=exc,
            )
            raise StarterHostOrchestrationError(
                f"external resume blocked: {type(exc).__name__}: {exc}"
            ) from exc
        return self._record_execution(
            session_id=session_id,
            expected_revision=claimed["revision"],
            allowed_phase=PHASE_RESUMING_EXTERNAL,
            execution=execution,
        )

    def resume_human(
        self,
        *,
        session_id: str,
        expected_revision: int,
        decision: Mapping[str, Any],
        evidence_refs: Iterable[str],
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, field="session_id")
        refs = tuple(_text(ref) for ref in evidence_refs if _text(ref))
        if not refs:
            raise StarterHostOrchestrationError("Human Gate resume requires durable evidence")
        claimed = self._claim_resume(
            session_id=session_id,
            expected_revision=expected_revision,
            waiting_phase=PHASE_HUMAN_GATE,
            resuming_phase=PHASE_RESUMING_HUMAN,
        )
        resolved = self._resolved(claimed)
        try:
            execution = self._runtime(claimed, resolved).resume(
                state=dict(claimed["runtime_state"]),
                human_decision=decision,
                evidence_refs=refs,
            )
        except Exception as exc:
            self._record_runtime_failure(
                session_id=session_id,
                expected_revision=claimed["revision"],
                allowed_phase=PHASE_RESUMING_HUMAN,
                error=exc,
            )
            raise StarterHostOrchestrationError(
                f"Human Gate resume blocked: {type(exc).__name__}: {exc}"
            ) from exc
        return self._record_execution(
            session_id=session_id,
            expected_revision=claimed["revision"],
            allowed_phase=PHASE_RESUMING_HUMAN,
            execution=execution,
        )


__all__ = [
    "PHASE_AWAITING_CONFIRMATION",
    "PHASE_AWAITING_SELECTION",
    "PHASE_BLOCKED",
    "PHASE_HUMAN_GATE",
    "PHASE_READY_TO_START",
    "PHASE_VALIDATING",
    "PHASE_WAITING_EXTERNAL",
    "PHASE_WAITING_HOST",
    "STARTER_HOST_NEXT_ACTION_SCHEMA",
    "STARTER_HOST_SESSION_SCHEMA",
    "StarterHostOrchestrationError",
    "StarterHostOrchestrator",
    "project_runtime_action",
]
