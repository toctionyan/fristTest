from __future__ import annotations

import re
from typing import Any, Mapping


STARTER_HOST_COMMAND_SCHEMA = "starter-host-command@1"
STARTER_HOST_COMMAND_RESPONSE_SCHEMA = "starter-host-command-response@1"

OP_OPEN = "OPEN"
OP_READ = "READ"
OP_SELECT = "SELECT"
OP_CONFIRM = "CONFIRM"
OP_START = "START"
OP_SUBMIT_HOST_RESULT = "SUBMIT_HOST_RESULT"
OP_RESUME_EXTERNAL = "RESUME_EXTERNAL"
OP_RESUME_HUMAN = "RESUME_HUMAN"
OP_RECONCILE = "RECONCILE"

OPERATIONS = frozenset(
    {
        OP_OPEN,
        OP_READ,
        OP_SELECT,
        OP_CONFIRM,
        OP_START,
        OP_SUBMIT_HOST_RESULT,
        OP_RESUME_EXTERNAL,
        OP_RESUME_HUMAN,
        OP_RECONCILE,
    }
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")
_HOSTS = frozenset({"chatgpt", "codex"})
_TRANSPORT_POLICY = {
    "transport_is_authority": False,
    "semantic_routing": False,
    "write_authority_granted": False,
    "completion_authority": "TaskRun",
    "automatic_merge": False,
    "authority_effect": False,
}


class StarterHostTransportError(ValueError):
    """Raised before an invalid Host command can reach the Orchestrator."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise StarterHostTransportError(f"{field} must be a stable identifier")
    if value != value.strip() or not value or not _SAFE_ID.fullmatch(value):
        raise StarterHostTransportError(f"{field} must be a stable identifier")
    return value


def _closed(value: Mapping[str, Any], required: set[str], *, field: str) -> None:
    missing = sorted(required - set(value))
    unexpected = sorted(set(value) - required)
    if missing or unexpected:
        raise StarterHostTransportError(
            f"{field} fields are not closed: missing={missing} unexpected={unexpected}"
        )


def _object(value: object, *, field: str, nonempty: bool = False) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise StarterHostTransportError(f"{field} must be an object")
    result = dict(value)
    if nonempty and not result:
        raise StarterHostTransportError(f"{field} must not be empty")
    return result


def _evidence_refs(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise StarterHostTransportError(f"{field} must be a non-empty array")
    if any(not isinstance(item, str) for item in value):
        raise StarterHostTransportError(f"{field} entries must be strings")
    refs = list(value)
    if (
        any(not item or item != item.strip() for item in refs)
        or len(refs) != len(set(refs))
    ):
        raise StarterHostTransportError(
            f"{field} must contain unique non-empty evidence references"
        )
    return refs


def validate_host_command(raw: Mapping[str, Any]) -> dict[str, Any]:
    command = _object(raw, field="Host command")
    _closed(
        command,
        {
            "schema",
            "command_id",
            "host_id",
            "operation",
            "session_id",
            "expected_revision",
            "payload",
            "authority_effect",
        },
        field="Host command",
    )
    if command.get("schema") != STARTER_HOST_COMMAND_SCHEMA:
        raise StarterHostTransportError(
            f"Host command schema must be {STARTER_HOST_COMMAND_SCHEMA!r}"
        )
    command_id = _identifier(command.get("command_id"), field="command_id")
    host_id = _identifier(command.get("host_id"), field="host_id")
    if host_id not in _HOSTS:
        raise StarterHostTransportError(f"unsupported Host: {host_id}")
    operation = _identifier(command.get("operation"), field="operation")
    if operation not in OPERATIONS:
        raise StarterHostTransportError(f"unsupported Host operation: {operation}")
    session_id = _identifier(command.get("session_id"), field="session_id")
    if command.get("authority_effect") is not False:
        raise StarterHostTransportError("Host command authority_effect must be false")

    revision = command.get("expected_revision")
    if operation in {OP_OPEN, OP_READ}:
        if revision is not None:
            raise StarterHostTransportError(
                f"{operation} expected_revision must be null"
            )
    elif not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise StarterHostTransportError(
            f"{operation} expected_revision must be a non-negative integer"
        )

    payload = _object(command.get("payload"), field="payload")
    expected_fields = {
        OP_OPEN: {"user_request"},
        OP_READ: set(),
        OP_SELECT: {"selection"},
        OP_CONFIRM: {"confirmation"},
        OP_START: {"target_ref"},
        OP_SUBMIT_HOST_RESULT: {"result"},
        OP_RESUME_EXTERNAL: {"event", "evidence_refs", "correlation_ref"},
        OP_RESUME_HUMAN: {"decision", "evidence_refs"},
        OP_RECONCILE: set(),
    }[operation]
    _closed(payload, expected_fields, field=f"{operation} payload")

    if operation == OP_OPEN:
        user_request = payload.get("user_request")
        if not isinstance(user_request, str) or not user_request.strip():
            raise StarterHostTransportError(
                "OPEN payload.user_request must be a non-empty string"
            )
        payload = {"user_request": user_request}
    elif operation == OP_SELECT:
        payload = {"selection": _object(payload.get("selection"), field="selection", nonempty=True)}
    elif operation == OP_CONFIRM:
        payload = {
            "confirmation": _object(
                payload.get("confirmation"), field="confirmation", nonempty=True
            )
        }
    elif operation == OP_START:
        payload = {"target_ref": _object(payload.get("target_ref"), field="target_ref", nonempty=True)}
    elif operation == OP_SUBMIT_HOST_RESULT:
        payload = {"result": _object(payload.get("result"), field="result", nonempty=True)}
    elif operation == OP_RESUME_EXTERNAL:
        raw_correlation = payload.get("correlation_ref")
        if (
            not isinstance(raw_correlation, str)
            or not raw_correlation
            or raw_correlation != raw_correlation.strip()
        ):
            raise StarterHostTransportError(
                "RESUME_EXTERNAL correlation_ref must be non-empty"
            )
        correlation_ref = raw_correlation
        payload = {
            "event": _object(payload.get("event"), field="event", nonempty=True),
            "evidence_refs": _evidence_refs(
                payload.get("evidence_refs"), field="evidence_refs"
            ),
            "correlation_ref": correlation_ref,
        }
    elif operation == OP_RESUME_HUMAN:
        payload = {
            "decision": _object(
                payload.get("decision"), field="decision", nonempty=True
            ),
            "evidence_refs": _evidence_refs(
                payload.get("evidence_refs"), field="evidence_refs"
            ),
        }

    return {
        "schema": STARTER_HOST_COMMAND_SCHEMA,
        "command_id": command_id,
        "host_id": host_id,
        "operation": operation,
        "session_id": session_id,
        "expected_revision": revision,
        "payload": payload,
        "authority_effect": False,
    }


def _success_response(command: Mapping[str, Any], session: Mapping[str, Any]) -> dict[str, Any]:
    session_payload = _object(session, field="Orchestrator session", nonempty=True)
    if session_payload.get("session_id") != command["session_id"]:
        raise StarterHostTransportError("Orchestrator returned another Host session")
    if session_payload.get("host_id") != command["host_id"]:
        raise StarterHostTransportError("Orchestrator returned another Host identity")
    next_action = session_payload.get("next_action")
    if not isinstance(next_action, Mapping):
        raise StarterHostTransportError("Orchestrator session has no canonical next_action")
    return {
        "schema": STARTER_HOST_COMMAND_RESPONSE_SCHEMA,
        "command_id": command["command_id"],
        "host_id": command["host_id"],
        "operation": command["operation"],
        "session_id": command["session_id"],
        "status": "PASS",
        "session": session_payload,
        "next_action": dict(next_action),
        "error": None,
        "policy": dict(_TRANSPORT_POLICY),
        "authority_effect": False,
    }


def failure_response(
    raw: object,
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}

    def safe(field: str, allowed: frozenset[str] | None = None) -> str | None:
        raw_value = source.get(field)
        if not isinstance(raw_value, str):
            return None
        if raw_value != raw_value.strip() or not raw_value or not _SAFE_ID.fullmatch(raw_value):
            return None
        if allowed is not None and raw_value not in allowed:
            return None
        return raw_value

    return {
        "schema": STARTER_HOST_COMMAND_RESPONSE_SCHEMA,
        "command_id": safe("command_id"),
        "host_id": safe("host_id", _HOSTS),
        "operation": safe("operation", OPERATIONS),
        "session_id": safe("session_id"),
        "status": "BLOCKED",
        "session": None,
        "next_action": None,
        "error": {"code": _identifier(code, field="error code"), "message": message},
        "policy": dict(_TRANSPORT_POLICY),
        "authority_effect": False,
    }


class StarterHostCommandTransport:
    """Fixed, non-authorizing command dispatch over StarterHostOrchestrator."""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    def execute(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        command = validate_host_command(raw)
        if _text(getattr(self.orchestrator, "host_id", "")).lower() != command["host_id"]:
            raise StarterHostTransportError(
                "trusted Host factory returned an Orchestrator for another Host"
            )
        session_id = command["session_id"]
        revision = command["expected_revision"]
        payload = command["payload"]
        operation = command["operation"]

        if operation == OP_OPEN:
            session = self.orchestrator.open(
                session_id=session_id, user_request=payload["user_request"]
            )
        elif operation == OP_READ:
            session = self.orchestrator.read(session_id)
        elif operation == OP_SELECT:
            session = self.orchestrator.select(
                session_id=session_id,
                expected_revision=revision,
                selection=payload["selection"],
            )
        elif operation == OP_CONFIRM:
            session = self.orchestrator.confirm(
                session_id=session_id,
                expected_revision=revision,
                confirmation=payload["confirmation"],
            )
        elif operation == OP_START:
            session = self.orchestrator.start(
                session_id=session_id,
                expected_revision=revision,
                target_ref=payload["target_ref"],
            )
        elif operation == OP_SUBMIT_HOST_RESULT:
            session = self.orchestrator.submit_host_result(
                session_id=session_id,
                expected_revision=revision,
                result=payload["result"],
            )
        elif operation == OP_RESUME_EXTERNAL:
            session = self.orchestrator.resume_external(
                session_id=session_id,
                expected_revision=revision,
                event=payload["event"],
                evidence_refs=payload["evidence_refs"],
                correlation_ref=payload["correlation_ref"],
            )
        elif operation == OP_RESUME_HUMAN:
            session = self.orchestrator.resume_human(
                session_id=session_id,
                expected_revision=revision,
                decision=payload["decision"],
                evidence_refs=payload["evidence_refs"],
            )
        else:
            session = self.orchestrator.reconcile(
                session_id=session_id, expected_revision=revision
            )
        return _success_response(command, session)


__all__ = [
    "OPERATIONS",
    "STARTER_HOST_COMMAND_RESPONSE_SCHEMA",
    "STARTER_HOST_COMMAND_SCHEMA",
    "StarterHostCommandTransport",
    "StarterHostTransportError",
    "failure_response",
    "validate_host_command",
]
