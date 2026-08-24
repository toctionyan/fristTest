from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from starter_host_orchestrator import (
    PHASE_RESUMING_EXTERNAL,
    PHASE_WAITING_EXTERNAL,
)
from starter_host_transport import (
    OP_RECONCILE,
    OP_RESUME_EXTERNAL,
    STARTER_HOST_COMMAND_SCHEMA,
    StarterHostCommandTransport,
)


EXTERNAL_EVENT_INGEST_REQUEST_SCHEMA = "external-event-ingest-request@1"
EXTERNAL_EVENT_SCHEMA = "external-wakeup-event@1"
EXTERNAL_EVENT_RESERVATION_SCHEMA = "external-wakeup-reservation@1"
EXTERNAL_EVENT_RECEIPT_SCHEMA = "external-wakeup-receipt@1"
EXTERNAL_EVENT_RESULT_SCHEMA = "external-wakeup-result@1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")
_EVENT_FIELDS = {
    "schema",
    "event_id",
    "session_id",
    "host_id",
    "task_id",
    "workflow_id",
    "expected_revision",
    "taskrun_ref",
    "wait_checkpoint_sequence",
    "external_wait",
    "event",
    "evidence_refs",
    "received_at",
    "authority_effect",
    "event_sha256",
}
_RESERVATION_FIELDS = {
    "schema",
    "event_id",
    "event_sha256",
    "session_id",
    "expected_revision",
    "created_at",
    "authority_effect",
    "reservation_sha256",
}
_RECEIPT_FIELDS = {
    "schema",
    "event_id",
    "event_sha256",
    "session_id",
    "task_id",
    "workflow_id",
    "status",
    "delivery_method",
    "recovered",
    "pre_revision",
    "post_revision",
    "taskrun_status",
    "taskrun_phase",
    "next_action",
    "reason",
    "created_at",
    "authority_effect",
    "completion_authority_changed",
    "merge_authority_changed",
    "receipt_sha256",
}


class DurableExternalEventSchedulerError(RuntimeError):
    """Raised when an event cannot be bound or delivered without ambiguity."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DurableExternalEventSchedulerError(
            "external event must contain only JSON values"
        ) from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object) -> str:
    return str(value or "").strip()


def _identifier(value: object, *, field: str) -> str:
    text = _text(value)
    if not text or not _SAFE_ID.fullmatch(text):
        raise DurableExternalEventSchedulerError(
            f"{field} must be a stable identifier"
        )
    return text


def _closed(payload: Mapping[str, Any], fields: set[str], *, field: str) -> None:
    missing = sorted(fields - set(payload))
    unexpected = sorted(set(payload) - fields)
    if missing or unexpected:
        raise DurableExternalEventSchedulerError(
            f"{field} fields are not closed: missing={missing} unexpected={unexpected}"
        )


def _refs(values: object, *, field: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise DurableExternalEventSchedulerError(f"{field} must be non-empty")
    refs = [str(value) for value in values]
    if (
        any(not value or value != value.strip() for value in refs)
        or len(refs) != len(set(refs))
    ):
        raise DurableExternalEventSchedulerError(
            f"{field} must contain unique non-empty durable references"
        )
    return refs


def _bounded_root(workspace: Path, value: str | Path, *, field: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise DurableExternalEventSchedulerError(
            f"{field} must be a bounded workspace-relative path"
        )
    path = workspace / relative
    try:
        path.resolve().relative_to(workspace)
    except ValueError as exc:
        raise DurableExternalEventSchedulerError(
            f"{field} escapes the project workspace"
        ) from exc
    current = path
    while current != workspace:
        if current.is_symlink():
            raise DurableExternalEventSchedulerError(f"{field} cannot use symlinks")
        current = current.parent
    return path


def _read_object(path: Path, *, field: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DurableExternalEventSchedulerError(f"{field} is missing or unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DurableExternalEventSchedulerError(f"{field} is unreadable") from exc
    if not isinstance(payload, dict):
        raise DurableExternalEventSchedulerError(f"{field} must be an object")
    return payload


def _atomic_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise DurableExternalEventSchedulerError(
            f"refusing to replace unsafe scheduler artifact: {path.name}"
        )
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _seal(payload: Mapping[str, Any], *, digest_field: str) -> dict[str, Any]:
    result = dict(payload)
    result.pop(digest_field, None)
    result[digest_field] = _digest(result)
    return result


def validate_ingest_request(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise DurableExternalEventSchedulerError("ingest request must be an object")
    payload = dict(raw)
    _closed(
        payload,
        {"schema", "host_id", "session_id", "event", "authority_effect"},
        field="ingest request",
    )
    if payload.get("schema") != EXTERNAL_EVENT_INGEST_REQUEST_SCHEMA:
        raise DurableExternalEventSchedulerError("unsupported ingest request schema")
    host_id = _identifier(payload.get("host_id"), field="host_id")
    if host_id not in {"chatgpt", "codex"}:
        raise DurableExternalEventSchedulerError("unsupported Host identity")
    session_id = _identifier(payload.get("session_id"), field="session_id")
    event = payload.get("event")
    if not isinstance(event, Mapping) or not event:
        raise DurableExternalEventSchedulerError("ingest request event must be non-empty")
    if payload.get("authority_effect") is not False:
        raise DurableExternalEventSchedulerError(
            "ingest request authority_effect must be false"
        )
    _canonical(event)
    return {
        "schema": EXTERNAL_EVENT_INGEST_REQUEST_SCHEMA,
        "host_id": host_id,
        "session_id": session_id,
        "event": dict(event),
        "authority_effect": False,
    }


class DurableExternalEventScheduler:
    """Project-local one-shot delivery for existing Host external waits.

    This component persists and correlates events. It never polls a Provider,
    selects a Workflow route, writes TaskRun/session state directly, or judges
    completion. Delivery goes through the existing closed Host transport.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        orchestrator: Any,
        event_root: str | Path = ".harness/runtime/external-events",
        receipt_root: str | Path = ".harness/runtime/external-wakeup-receipts",
        lock_root: str | Path = ".harness/runtime/external-wakeup-locks",
        max_events_per_run: int = 100,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise DurableExternalEventSchedulerError("workspace must be a directory")
        self.orchestrator = orchestrator
        self.host_id = _identifier(getattr(orchestrator, "host_id", ""), field="host_id")
        self.event_root = _bounded_root(
            self.workspace, event_root, field="scheduler.event_root"
        )
        self.receipt_root = _bounded_root(
            self.workspace, receipt_root, field="scheduler.receipt_root"
        )
        self.lock_root = _bounded_root(
            self.workspace, lock_root, field="scheduler.lock_root"
        )
        if len({self.event_root, self.receipt_root, self.lock_root}) != 3:
            raise DurableExternalEventSchedulerError(
                "scheduler event, receipt, and lock roots must be distinct"
            )
        if (
            not isinstance(max_events_per_run, int)
            or isinstance(max_events_per_run, bool)
            or not 1 <= max_events_per_run <= 1000
        ):
            raise DurableExternalEventSchedulerError(
                "max_events_per_run must be 1..1000"
            )
        self.max_events_per_run = max_events_per_run
        self.transport = StarterHostCommandTransport(orchestrator)

    def _taskrun_path(self, ref: object) -> Path:
        value = _text(ref)
        if not value.startswith("file:"):
            raise DurableExternalEventSchedulerError(
                "Host session taskrun_ref must use file:"
            )
        relative = Path(value.removeprefix("file:"))
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
            raise DurableExternalEventSchedulerError("taskrun_ref is unbounded")
        path = self.workspace / relative
        try:
            path.resolve().relative_to(self.workspace)
        except ValueError as exc:
            raise DurableExternalEventSchedulerError(
                "taskrun_ref escapes the project workspace"
            ) from exc
        current = path
        while current != self.workspace:
            if current.is_symlink():
                raise DurableExternalEventSchedulerError(
                    "taskrun_ref cannot use symlinks"
                )
            current = current.parent
        return path

    @staticmethod
    def _wait_from_session(session: Mapping[str, Any]) -> dict[str, Any]:
        if session.get("phase") != PHASE_WAITING_EXTERNAL:
            raise DurableExternalEventSchedulerError(
                "event ingest requires Host session WAITING_EXTERNAL"
            )
        state = session.get("runtime_state")
        wait = state.get("external_wait") if isinstance(state, Mapping) else None
        if not isinstance(wait, Mapping) or not wait:
            raise DurableExternalEventSchedulerError(
                "Host session has no exact external_wait handle"
            )
        required = ("provider", "correlation_ref", "resume_event")
        if any(not _text(wait.get(field)) for field in required):
            raise DurableExternalEventSchedulerError(
                "external_wait requires provider, correlation_ref, and resume_event"
            )
        return dict(wait)

    def _wait_checkpoint(
        self, session: Mapping[str, Any], wait: Mapping[str, Any]
    ) -> tuple[Path, dict[str, Any]]:
        path = self._taskrun_path(session.get("taskrun_ref"))
        taskrun = _read_object(path, field="TaskRun")
        if taskrun.get("task_id") != session.get("task_id"):
            raise DurableExternalEventSchedulerError(
                "Host session and TaskRun identity differ"
            )
        checkpoints = taskrun.get("checkpoints")
        latest = checkpoints[-1] if isinstance(checkpoints, list) and checkpoints else None
        metadata = latest.get("metadata") if isinstance(latest, Mapping) else None
        state = session.get("runtime_state")
        if (
            taskrun.get("status") != "WAITING_EXTERNAL_RESULT"
            or taskrun.get("phase") != "WORKFLOW_WAITING_EXTERNAL"
            or not isinstance(latest, Mapping)
            or latest.get("status") != "WAITING_EXTERNAL_RESULT"
            or latest.get("phase") != "WORKFLOW_WAITING_EXTERNAL"
            or not isinstance(metadata, Mapping)
            or metadata.get("external_wait") != dict(wait)
            or metadata.get("workflow_id")
            != (state.get("workflow_id") if isinstance(state, Mapping) else None)
            or not isinstance(latest.get("sequence"), int)
        ):
            raise DurableExternalEventSchedulerError(
                "TaskRun does not contain the exact active external wait checkpoint"
            )
        return path, dict(latest)

    @staticmethod
    def _event_identity(
        *,
        session: Mapping[str, Any],
        wait: Mapping[str, Any],
        wait_sequence: int,
        event: Mapping[str, Any],
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        state = session.get("runtime_state")
        workflow_id = state.get("workflow_id") if isinstance(state, Mapping) else None
        return {
            "session_id": session.get("session_id"),
            "host_id": session.get("host_id"),
            "task_id": session.get("task_id"),
            "workflow_id": workflow_id,
            "expected_revision": session.get("revision"),
            "taskrun_ref": session.get("taskrun_ref"),
            "wait_checkpoint_sequence": wait_sequence,
            "external_wait": dict(wait),
            "event": dict(event),
            "evidence_refs": evidence_refs,
        }

    def _validate_event(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = dict(payload)
        _closed(event, _EVENT_FIELDS, field="external event")
        if event.get("schema") != EXTERNAL_EVENT_SCHEMA:
            raise DurableExternalEventSchedulerError("unsupported external event schema")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not _SHA256.fullmatch(event_id):
            raise DurableExternalEventSchedulerError("external event_id is invalid")
        if event.get("host_id") != self.host_id:
            raise DurableExternalEventSchedulerError(
                "external event belongs to another Host"
            )
        _identifier(event.get("session_id"), field="session_id")
        _identifier(event.get("task_id"), field="task_id")
        _identifier(event.get("workflow_id"), field="workflow_id")
        if (
            not isinstance(event.get("expected_revision"), int)
            or isinstance(event.get("expected_revision"), bool)
            or int(event["expected_revision"]) < 0
            or not isinstance(event.get("wait_checkpoint_sequence"), int)
            or int(event["wait_checkpoint_sequence"]) < 1
        ):
            raise DurableExternalEventSchedulerError(
                "external event revision/checkpoint identity is invalid"
            )
        wait = event.get("external_wait")
        raw_event = event.get("event")
        if not isinstance(wait, Mapping) or not isinstance(raw_event, Mapping):
            raise DurableExternalEventSchedulerError(
                "external event wait and payload must be objects"
            )
        refs = _refs(event.get("evidence_refs"), field="event.evidence_refs")
        if raw_event.get("evidence_refs") != refs:
            raise DurableExternalEventSchedulerError(
                "event payload evidence_refs must exactly match the sealed evidence"
            )
        identity = {
            field: event[field]
            for field in (
                "session_id",
                "host_id",
                "task_id",
                "workflow_id",
                "expected_revision",
                "taskrun_ref",
                "wait_checkpoint_sequence",
                "external_wait",
                "event",
                "evidence_refs",
            )
        }
        if _digest(identity) != event_id:
            raise DurableExternalEventSchedulerError(
                "external event deterministic identity mismatch"
            )
        digest = event.get("event_sha256")
        unsigned = dict(event)
        unsigned.pop("event_sha256", None)
        if not isinstance(digest, str) or digest != _digest(unsigned):
            raise DurableExternalEventSchedulerError(
                "external event fingerprint mismatch"
            )
        if event.get("authority_effect") is not False:
            raise DurableExternalEventSchedulerError(
                "external event cannot have authority effect"
            )
        return event

    def ingest(self, *, session_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        session_id = _identifier(session_id, field="session_id")
        if not isinstance(event, Mapping) or not event:
            raise DurableExternalEventSchedulerError("external event must be non-empty")
        normalized_event = json.loads(_canonical(dict(event)).decode("utf-8"))
        refs = _refs(
            normalized_event.get("evidence_refs"), field="event.evidence_refs"
        )
        session = self.orchestrator.read(session_id)
        if session.get("host_id") != self.host_id:
            raise DurableExternalEventSchedulerError("Host session belongs to another Host")
        wait = self._wait_from_session(session)
        provider = _text(
            normalized_event.get("provider") or normalized_event.get("provider_id")
        )
        correlation = _text(normalized_event.get("correlation_ref"))
        event_name = _text(
            normalized_event.get("event") or normalized_event.get("event_name")
        )
        if provider != _text(wait.get("provider")):
            raise DurableExternalEventSchedulerError(
                "external event provider does not match the active wait"
            )
        if correlation != _text(wait.get("correlation_ref")):
            raise DurableExternalEventSchedulerError(
                "external event correlation does not match the active wait"
            )
        if event_name != _text(wait.get("resume_event")):
            raise DurableExternalEventSchedulerError(
                "external event name does not match the active wait"
            )
        _, checkpoint = self._wait_checkpoint(session, wait)
        identity = self._event_identity(
            session=session,
            wait=wait,
            wait_sequence=int(checkpoint["sequence"]),
            event=normalized_event,
            evidence_refs=refs,
        )
        event_id = _digest(identity)
        payload = _seal(
            {
                "schema": EXTERNAL_EVENT_SCHEMA,
                "event_id": event_id,
                **identity,
                "received_at": _now(),
                "authority_effect": False,
            },
            digest_field="event_sha256",
        )
        validated = self._validate_event(payload)
        path = self.event_root / f"{event_id}.json"
        if self.event_root.is_symlink():
            raise DurableExternalEventSchedulerError("event root is unsafe")
        try:
            _atomic_exclusive(path, validated)
        except FileExistsError:
            existing = self._validate_event(
                _read_object(path, field="persisted external event")
            )
            fields = _EVENT_FIELDS - {"received_at", "event_sha256"}
            if any(existing[field] != validated[field] for field in fields):
                raise DurableExternalEventSchedulerError(
                    "external event identity collision"
                )
            validated = existing
        return {
            "schema": EXTERNAL_EVENT_RESULT_SCHEMA,
            "status": "QUEUED",
            "event_id": event_id,
            "event_ref": f"file:{path.relative_to(self.workspace).as_posix()}",
            "receipt_ref": None,
            "session_id": session_id,
            "task_id": validated["task_id"],
            "workflow_id": validated["workflow_id"],
            "next_action": "WAKE_EXTERNAL_EVENT",
            "authority_effect": False,
            "completion_authority_changed": False,
            "merge_authority_changed": False,
        }

    def _event_path(self, ref: str) -> Path:
        value = _text(ref)
        if not value.startswith("file:"):
            raise DurableExternalEventSchedulerError("event_ref must use file:")
        relative = Path(value.removeprefix("file:"))
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
            raise DurableExternalEventSchedulerError("event_ref is unbounded")
        path = self.workspace / relative
        try:
            path.resolve().relative_to(self.event_root.resolve())
        except ValueError as exc:
            raise DurableExternalEventSchedulerError(
                "event_ref is outside the configured event root"
            ) from exc
        if path.parent.resolve() != self.event_root.resolve():
            raise DurableExternalEventSchedulerError(
                "event_ref must name a direct event artifact"
            )
        return path

    def _artifact_paths(self, event_id: str) -> tuple[Path, Path, Path]:
        root = self.receipt_root / event_id
        if self.receipt_root.is_symlink() or root.is_symlink():
            raise DurableExternalEventSchedulerError("scheduler receipt root is unsafe")
        return root / "reservation.json", root / "receipt.json", self.lock_root

    def _reservation(self, event: Mapping[str, Any]) -> dict[str, Any]:
        payload = _seal(
            {
                "schema": EXTERNAL_EVENT_RESERVATION_SCHEMA,
                "event_id": event["event_id"],
                "event_sha256": event["event_sha256"],
                "session_id": event["session_id"],
                "expected_revision": event["expected_revision"],
                "created_at": _now(),
                "authority_effect": False,
            },
            digest_field="reservation_sha256",
        )
        return self._validate_reservation(payload, event=event)

    def _validate_reservation(
        self, payload: Mapping[str, Any], *, event: Mapping[str, Any]
    ) -> dict[str, Any]:
        row = dict(payload)
        _closed(row, _RESERVATION_FIELDS, field="wakeup reservation")
        unsigned = dict(row)
        digest = unsigned.pop("reservation_sha256", None)
        if (
            row.get("schema") != EXTERNAL_EVENT_RESERVATION_SCHEMA
            or row.get("event_id") != event["event_id"]
            or row.get("event_sha256") != event["event_sha256"]
            or row.get("session_id") != event["session_id"]
            or row.get("expected_revision") != event["expected_revision"]
            or row.get("authority_effect") is not False
            or digest != _digest(unsigned)
        ):
            raise DurableExternalEventSchedulerError(
                "wakeup reservation is stale or tampered"
            )
        return row

    def _validate_receipt(
        self, payload: Mapping[str, Any], *, event: Mapping[str, Any]
    ) -> dict[str, Any]:
        row = dict(payload)
        _closed(row, _RECEIPT_FIELDS, field="wakeup receipt")
        unsigned = dict(row)
        digest = unsigned.pop("receipt_sha256", None)
        if (
            row.get("schema") != EXTERNAL_EVENT_RECEIPT_SCHEMA
            or row.get("event_id") != event["event_id"]
            or row.get("event_sha256") != event["event_sha256"]
            or row.get("session_id") != event["session_id"]
            or row.get("task_id") != event["task_id"]
            or row.get("workflow_id") != event["workflow_id"]
            or row.get("authority_effect") is not False
            or row.get("completion_authority_changed") is not False
            or row.get("merge_authority_changed") is not False
            or row.get("status") not in {
                "DELIVERED",
                "REJECTED_STALE",
                "BLOCKED_UNCERTAIN",
            }
            or digest != _digest(unsigned)
        ):
            raise DurableExternalEventSchedulerError(
                "wakeup receipt is stale or tampered"
            )
        return row

    def _result(
        self, event: Mapping[str, Any], receipt: Mapping[str, Any], receipt_path: Path
    ) -> dict[str, Any]:
        return {
            "schema": EXTERNAL_EVENT_RESULT_SCHEMA,
            "status": receipt["status"],
            "event_id": event["event_id"],
            "event_ref": f"file:{(self.event_root / (event['event_id'] + '.json')).relative_to(self.workspace).as_posix()}",
            "receipt_ref": f"file:{receipt_path.relative_to(self.workspace).as_posix()}",
            "session_id": event["session_id"],
            "task_id": event["task_id"],
            "workflow_id": event["workflow_id"],
            "next_action": receipt.get("next_action"),
            "authority_effect": False,
            "completion_authority_changed": False,
            "merge_authority_changed": False,
        }

    def _write_receipt(
        self,
        *,
        event: Mapping[str, Any],
        path: Path,
        status: str,
        method: str,
        recovered: bool,
        session: Mapping[str, Any],
        reason: str | None,
    ) -> dict[str, Any]:
        next_action = session.get("next_action")
        kind = next_action.get("kind") if isinstance(next_action, Mapping) else None
        receipt = _seal(
            {
                "schema": EXTERNAL_EVENT_RECEIPT_SCHEMA,
                "event_id": event["event_id"],
                "event_sha256": event["event_sha256"],
                "session_id": event["session_id"],
                "task_id": event["task_id"],
                "workflow_id": event["workflow_id"],
                "status": status,
                "delivery_method": method,
                "recovered": recovered,
                "pre_revision": event["expected_revision"],
                "post_revision": session.get("revision"),
                "taskrun_status": session.get("taskrun_status"),
                "taskrun_phase": session.get("taskrun_phase"),
                "next_action": kind,
                "reason": reason,
                "created_at": _now(),
                "authority_effect": False,
                "completion_authority_changed": False,
                "merge_authority_changed": False,
            },
            digest_field="receipt_sha256",
        )
        receipt = self._validate_receipt(receipt, event=event)
        try:
            _atomic_exclusive(path, receipt)
        except FileExistsError:
            existing = self._validate_receipt(
                _read_object(path, field="persisted wakeup receipt"), event=event
            )
            return existing
        return receipt

    @staticmethod
    def _pending_matches(session: Mapping[str, Any], event: Mapping[str, Any], event_ref: str) -> bool:
        pending = session.get("pending_transition")
        if not isinstance(pending, Mapping) or pending.get("kind") != "EXTERNAL_EVENT":
            return False
        value = pending.get("input")
        refs = pending.get("evidence_refs")
        return (
            isinstance(value, Mapping)
            and value.get("event") == event["event"]
            and pending.get("correlation_ref")
            == event["external_wait"]["correlation_ref"]
            and isinstance(refs, list)
            and event_ref in refs
        )

    def _taskrun_proves_resume(self, event: Mapping[str, Any], event_ref: str) -> bool:
        taskrun = _read_object(
            self._taskrun_path(event["taskrun_ref"]), field="TaskRun"
        )
        if taskrun.get("task_id") != event["task_id"]:
            return False
        checkpoints = taskrun.get("checkpoints")
        for checkpoint in checkpoints if isinstance(checkpoints, list) else []:
            metadata = checkpoint.get("metadata") if isinstance(checkpoint, Mapping) else None
            refs = checkpoint.get("evidence_refs") if isinstance(checkpoint, Mapping) else None
            if (
                isinstance(checkpoint, Mapping)
                and isinstance(metadata, Mapping)
                and isinstance(refs, list)
                and int(checkpoint.get("sequence") or 0)
                > int(event["wait_checkpoint_sequence"])
                and checkpoint.get("phase") == "WORKFLOW_RUNTIME_RESUMED"
                and metadata.get("workflow_id") == event["workflow_id"]
                and metadata.get("resume_kind") == "EXTERNAL_EVENT"
                and metadata.get("correlation_ref")
                == event["external_wait"]["correlation_ref"]
                and event_ref in refs
            ):
                return True
        return False

    def _waiting_matches(self, session: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
        if (
            session.get("phase") != PHASE_WAITING_EXTERNAL
            or session.get("revision") != event["expected_revision"]
            or session.get("session_id") != event["session_id"]
            or session.get("task_id") != event["task_id"]
        ):
            return False
        state = session.get("runtime_state")
        return (
            isinstance(state, Mapping)
            and state.get("workflow_id") == event["workflow_id"]
            and state.get("external_wait") == event["external_wait"]
        )

    def wake(self, *, event_ref: str) -> dict[str, Any]:
        event_path = self._event_path(event_ref)
        event = self._validate_event(
            _read_object(event_path, field="persisted external event")
        )
        if event_path.name != f"{event['event_id']}.json":
            raise DurableExternalEventSchedulerError(
                "event_ref filename does not match event identity"
            )
        reservation_path, receipt_path, lock_root = self._artifact_paths(
            event["event_id"]
        )
        lock_root.mkdir(parents=True, exist_ok=True)
        if lock_root.is_symlink():
            raise DurableExternalEventSchedulerError("scheduler lock root is unsafe")
        lock_path = lock_root / f"{event['session_id']}.lock"
        if lock_path.is_symlink():
            raise DurableExternalEventSchedulerError("scheduler lock is unsafe")
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if receipt_path.is_file():
                receipt = self._validate_receipt(
                    _read_object(receipt_path, field="persisted wakeup receipt"),
                    event=event,
                )
                return self._result(event, receipt, receipt_path)
            if reservation_path.exists():
                self._validate_reservation(
                    _read_object(reservation_path, field="wakeup reservation"),
                    event=event,
                )
            else:
                try:
                    _atomic_exclusive(reservation_path, self._reservation(event))
                except FileExistsError:
                    self._validate_reservation(
                        _read_object(reservation_path, field="wakeup reservation"),
                        event=event,
                    )

            session = self.orchestrator.read(event["session_id"])
            method = ""
            command: dict[str, Any] | None = None
            if self._waiting_matches(session, event):
                method = OP_RESUME_EXTERNAL
                command = {
                    "schema": STARTER_HOST_COMMAND_SCHEMA,
                    "command_id": f"external-wakeup:{event['event_id']}",
                    "host_id": self.host_id,
                    "operation": OP_RESUME_EXTERNAL,
                    "session_id": event["session_id"],
                    "expected_revision": event["expected_revision"],
                    "payload": {
                        "event": dict(event["event"]),
                        "evidence_refs": list(
                            dict.fromkeys([*event["evidence_refs"], event_ref])
                        ),
                        "correlation_ref": event["external_wait"]["correlation_ref"],
                    },
                    "authority_effect": False,
                }
            elif (
                session.get("phase") == PHASE_RESUMING_EXTERNAL
                and session.get("revision") == event["expected_revision"] + 1
                and self._pending_matches(session, event, event_ref)
            ):
                method = OP_RECONCILE
                command = {
                    "schema": STARTER_HOST_COMMAND_SCHEMA,
                    "command_id": f"external-reconcile:{event['event_id']}",
                    "host_id": self.host_id,
                    "operation": OP_RECONCILE,
                    "session_id": event["session_id"],
                    "expected_revision": session["revision"],
                    "payload": {},
                    "authority_effect": False,
                }
            elif (
                session.get("revision", -1) > event["expected_revision"]
                and session.get("phase") != PHASE_RESUMING_EXTERNAL
                and self._taskrun_proves_resume(event, event_ref)
            ):
                receipt = self._write_receipt(
                    event=event,
                    path=receipt_path,
                    status="DELIVERED",
                    method="RECOVERED_FROM_TASKRUN_EVIDENCE",
                    recovered=True,
                    session=session,
                    reason=None,
                )
                return self._result(event, receipt, receipt_path)
            else:
                status = (
                    "BLOCKED_UNCERTAIN"
                    if session.get("phase") == PHASE_RESUMING_EXTERNAL
                    else "REJECTED_STALE"
                )
                receipt = self._write_receipt(
                    event=event,
                    path=receipt_path,
                    status=status,
                    method="NONE",
                    recovered=False,
                    session=session,
                    reason="Host session no longer proves the exact resumable wait",
                )
                return self._result(event, receipt, receipt_path)

            try:
                response = self.transport.execute(command)
            except Exception:
                # Keep the immutable reservation. A later one-shot wake will
                # inspect RESUMING_EXTERNAL and use the existing RECONCILE path.
                raise
            if response.get("status") != "PASS" or not isinstance(
                response.get("session"), Mapping
            ):
                raise DurableExternalEventSchedulerError(
                    "Host transport did not return a successful exact session"
                )
            delivered_session = dict(response["session"])
            if not self._taskrun_proves_resume(event, event_ref):
                raise DurableExternalEventSchedulerError(
                    "Host resume returned without exact TaskRun event evidence"
                )
            receipt = self._write_receipt(
                event=event,
                path=receipt_path,
                status="DELIVERED",
                method=method,
                recovered=method == OP_RECONCILE,
                session=delivered_session,
                reason=None,
            )
            return self._result(event, receipt, receipt_path)

    def run_once(self) -> dict[str, Any]:
        """Process a bounded inbox snapshot; never poll a Provider or sleep."""

        if not self.event_root.exists():
            candidates: list[Path] = []
        else:
            if self.event_root.is_symlink():
                raise DurableExternalEventSchedulerError("event root is unsafe")
            candidates = sorted(
                path
                for path in self.event_root.iterdir()
                if path.is_file() and not path.is_symlink() and path.suffix == ".json"
            )[: self.max_events_per_run]
        results: list[dict[str, Any]] = []
        for path in candidates:
            ref = f"file:{path.relative_to(self.workspace).as_posix()}"
            try:
                results.append(self.wake(event_ref=ref))
            except Exception as exc:
                results.append(
                    {
                        "schema": EXTERNAL_EVENT_RESULT_SCHEMA,
                        "status": "ERROR",
                        "event_id": path.stem if _SHA256.fullmatch(path.stem) else None,
                        "event_ref": ref,
                        "receipt_ref": None,
                        "session_id": None,
                        "task_id": None,
                        "workflow_id": None,
                        "next_action": "INSPECT_EVENT",
                        "error": str(exc),
                        "authority_effect": False,
                        "completion_authority_changed": False,
                        "merge_authority_changed": False,
                    }
                )
        return {
            "schema": "external-wakeup-run-once@1",
            "status": "PASS",
            "processed": len(results),
            "limit": self.max_events_per_run,
            "provider_polling": False,
            "results": results,
            "authority_effect": False,
            "completion_authority_changed": False,
            "merge_authority_changed": False,
        }


__all__ = [
    "DurableExternalEventScheduler",
    "DurableExternalEventSchedulerError",
    "EXTERNAL_EVENT_INGEST_REQUEST_SCHEMA",
    "EXTERNAL_EVENT_RECEIPT_SCHEMA",
    "EXTERNAL_EVENT_RESERVATION_SCHEMA",
    "EXTERNAL_EVENT_RESULT_SCHEMA",
    "EXTERNAL_EVENT_SCHEMA",
    "validate_ingest_request",
]
