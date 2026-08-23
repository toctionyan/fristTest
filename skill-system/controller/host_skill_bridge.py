from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from langgraph_workflow_runtime import HostExecutionPending, WorkflowRuntimeState
from skill_invocation import canonical_skill_identity
from workflow_dispatcher import SkillHostResult
from workflow_graph_contract import WorkflowStepSpec

HOST_REQUEST_SCHEMA = "host-skill-execution-request@1"
HOST_RESULT_SCHEMA = "host-skill-execution-result@1"
HOST_RESUME_SCHEMA = "host-skill-execution-resume@1"
HOST_WAIT_SCHEMA = "host-skill-execution-wait@1"
HOST_TOOL_RECEIPT_SCHEMA = "host-tool-receipt@1"
SUPPORTED_HOSTS = frozenset({"chatgpt", "codex"})

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class HostSkillBridgeError(ValueError):
    """Raised when a Host request, result, or evidence binding is invalid."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _safe_id(value: object, *, field: str) -> str:
    text = _text(value)
    if not text or not _SAFE_ID.fullmatch(text):
        raise HostSkillBridgeError(f"{field} must be a stable identifier")
    return text


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _payload_digest(value: Mapping[str, Any], *, omit: str | None = None) -> str:
    body = dict(value)
    if omit:
        body.pop(omit, None)
    return _digest_bytes(_canonical_json(body))


def _exact_fields(payload: Mapping[str, Any], *, required: set[str], optional: set[str] = set()) -> None:
    missing = sorted(required - set(payload))
    unexpected = sorted(set(payload) - required - optional)
    if missing or unexpected:
        raise HostSkillBridgeError(
            f"invalid fields: missing={missing} unexpected={unexpected}"
        )


class DurableHostSkillBridge:
    """Durable ChatGPT/Codex implementation of the SkillHostAdapter boundary.

    The first call persists an immutable request and suspends the graph.  The Host
    loads the exact Skill, performs its structured tool calls, and submits a
    result.  Resume revalidates every identity before returning SkillHostResult;
    the existing CanonicalSkillInvocationAdapter alone creates the canonical
    Skill invocation receipt.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        host_id: str,
        canonical_skill_paths: Mapping[str, str | Path],
        executions_dir: str | Path = ".harness/host-executions",
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.host_id = _safe_id(host_id, field="host_id").lower()
        if self.host_id not in SUPPORTED_HOSTS:
            raise HostSkillBridgeError(f"unsupported Host: {self.host_id}")
        self.canonical_skill_paths = {
            _safe_id(name, field="skill name"): Path(path)
            for name, path in canonical_skill_paths.items()
        }
        relative = Path(executions_dir)
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
            raise HostSkillBridgeError("executions_dir must be workspace-relative and bounded")
        self.executions_root = self.workspace / relative

    def _execution_dir(self, execution_id: str) -> Path:
        return self.executions_root / _safe_id(execution_id, field="execution_id")

    def _ref(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(self.workspace)
        except ValueError as exc:
            raise HostSkillBridgeError("Host artifact escapes workspace") from exc
        return f"file:{relative.as_posix()}"

    def _path_from_ref(self, ref: object) -> Path:
        text = _text(ref)
        if not text.startswith("file:"):
            raise HostSkillBridgeError("Host execution result_ref must be a workspace file reference")
        relative = Path(text.removeprefix("file:"))
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
            raise HostSkillBridgeError("Host execution file reference is unsafe")
        path = self.workspace / relative
        try:
            path.resolve().relative_to(self.workspace)
        except ValueError as exc:
            raise HostSkillBridgeError("Host execution file reference escapes workspace") from exc
        if path.is_symlink() or not path.is_file():
            raise HostSkillBridgeError("Host execution file reference is missing")
        return path

    @staticmethod
    def _atomic_create(path: Path, payload: Mapping[str, Any]) -> None:
        """Publish a fully-written immutable file without a check/replace race."""

        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            # The temporary inode is complete and fsynced before it becomes
            # visible.  link(2) atomically claims a missing final pathname and
            # raises FileExistsError instead of replacing a concurrent winner.
            os.link(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HostSkillBridgeError(f"Host artifact is unreadable: {path.name}") from exc
        if not isinstance(payload, dict):
            raise HostSkillBridgeError("Host artifact must be a JSON object")
        return payload

    def _execution_id(
        self,
        *,
        skill_name: str,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> str:
        attempts = state.get("step_attempts") if isinstance(state.get("step_attempts"), Mapping) else {}
        attempt = int(attempts.get(step.step_id) or 0) + 1
        seed = "|".join(
            (
                _safe_id(state.get("workflow_id"), field="workflow_id"),
                _safe_id(state.get("task_id"), field="task_id"),
                _safe_id(step.step_id, field="step_id"),
                str(attempt),
                _safe_id(skill_name, field="skill_name"),
                self.host_id,
            )
        )
        return f"host-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}"

    def _build_request(
        self,
        *,
        execution_id: str,
        skill_name: str,
        request_class: str,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> dict[str, Any]:
        relative, skill_sha = canonical_skill_identity(
            self.workspace,
            skill_name,
            canonical_skill_paths=self.canonical_skill_paths,
        )
        target = state.get("target_ref") if isinstance(state.get("target_ref"), Mapping) else {}
        user_payload = _text(target.get("user_payload"))
        request: dict[str, Any] = {
            "schema": HOST_REQUEST_SCHEMA,
            "execution_id": execution_id,
            "host_id": self.host_id,
            "task_id": _safe_id(state.get("task_id"), field="task_id"),
            "workflow_id": _safe_id(state.get("workflow_id"), field="workflow_id"),
            "step_id": _safe_id(step.step_id, field="step_id"),
            "request_class": _safe_id(request_class.upper(), field="request_class"),
            "skill": {
                "name": _safe_id(skill_name, field="skill_name"),
                "path": relative.as_posix(),
                "sha256": skill_sha,
            },
            "user_payload_sha256": _digest_bytes(user_payload.encode("utf-8")),
            "allowed_outcomes": sorted(step.routes),
            "required_result_schema": HOST_RESULT_SCHEMA,
            "policy": {
                "structured_tools_only": True,
                "receipt_before_execution_forbidden": True,
                "authority_effect": False,
                "completion_authority": "TaskRun",
            },
        }
        request["request_fingerprint_sha256"] = _payload_digest(
            request, omit="request_fingerprint_sha256"
        )
        return request

    def _persist_request(self, request: Mapping[str, Any]) -> Path:
        path = self._execution_dir(_text(request.get("execution_id"))) / "request.json"
        try:
            self._atomic_create(path, request)
        except FileExistsError:
            if self._load_json(path) != dict(request):
                raise HostSkillBridgeError("immutable Host execution request identity drifted")
        return path

    def _load_request(self, execution_id: str) -> tuple[Path, dict[str, Any]]:
        path = self._execution_dir(execution_id) / "request.json"
        payload = self._load_json(path)
        if payload.get("schema") != HOST_REQUEST_SCHEMA:
            raise HostSkillBridgeError("unsupported Host execution request schema")
        if payload.get("execution_id") != execution_id:
            raise HostSkillBridgeError("Host execution request identity mismatch")
        if payload.get("request_fingerprint_sha256") != _payload_digest(
            payload, omit="request_fingerprint_sha256"
        ):
            raise HostSkillBridgeError("Host execution request fingerprint mismatch")
        return path, payload

    def _validate_tool_receipt(self, row: object) -> dict[str, Any]:
        if not isinstance(row, Mapping):
            raise HostSkillBridgeError("Host tool receipt must be an object")
        receipt = dict(row)
        _exact_fields(
            receipt,
            required={
                "schema", "tool_call_id", "tool_name", "arguments_sha256",
                "result_sha256", "evidence_ref", "mutates", "write_authority_checked",
            },
        )
        if receipt.get("schema") != HOST_TOOL_RECEIPT_SCHEMA:
            raise HostSkillBridgeError("unsupported Host tool receipt schema")
        _safe_id(receipt.get("tool_call_id"), field="tool_call_id")
        _safe_id(receipt.get("tool_name"), field="tool_name")
        for field in ("arguments_sha256", "result_sha256"):
            if not _SHA256.fullmatch(_text(receipt.get(field))):
                raise HostSkillBridgeError(f"Host tool receipt {field} is invalid")
        if not _text(receipt.get("evidence_ref")):
            raise HostSkillBridgeError("Host tool receipt requires evidence_ref")
        if not isinstance(receipt.get("mutates"), bool) or not isinstance(
            receipt.get("write_authority_checked"), bool
        ):
            raise HostSkillBridgeError("Host tool receipt mutation fields must be boolean")
        if receipt["mutates"] and receipt["write_authority_checked"] is not True:
            raise HostSkillBridgeError(
                "mutating Host tool receipt requires prior write-authority check"
            )
        return receipt

    def _validate_result(
        self,
        *,
        request: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = dict(result)
        _exact_fields(
            payload,
            required={
                "schema", "execution_id", "request_fingerprint_sha256", "host_id",
                "status", "loaded_skill", "outcome", "output", "tool_receipts",
                "evidence_refs", "payload", "problem_ledger_ref", "authority_effect",
            },
            optional={"result_fingerprint_sha256"},
        )
        if payload.get("schema") != HOST_RESULT_SCHEMA or payload.get("status") != "PASS":
            raise HostSkillBridgeError("Host execution result is not a supported PASS result")
        if payload.get("execution_id") != request.get("execution_id"):
            raise HostSkillBridgeError("Host execution result execution_id mismatch")
        if payload.get("request_fingerprint_sha256") != request.get("request_fingerprint_sha256"):
            raise HostSkillBridgeError("Host execution result request fingerprint mismatch")
        if payload.get("host_id") != self.host_id:
            raise HostSkillBridgeError("Host execution result host_id mismatch")
        if payload.get("authority_effect") is not False:
            raise HostSkillBridgeError("Host execution evidence cannot change authority")

        loaded = payload.get("loaded_skill")
        if not isinstance(loaded, Mapping) or dict(loaded) != dict(request.get("skill") or {}):
            raise HostSkillBridgeError("Host did not load the exact requested Skill identity")
        if _text(payload.get("outcome")) not in set(request.get("allowed_outcomes") or []):
            raise HostSkillBridgeError("Host execution returned an undeclared Skill outcome")

        output = payload.get("output")
        if not isinstance(output, Mapping):
            raise HostSkillBridgeError("Host execution output must be an object")
        _exact_fields(
            output,
            required={"schema", "content", "sha256", "evidence_ref"},
        )
        content = output.get("content")
        if not isinstance(content, str) or not content:
            raise HostSkillBridgeError("Host execution output content must not be empty")
        if _digest_bytes(content.encode("utf-8")) != _text(output.get("sha256")):
            raise HostSkillBridgeError("Host execution output digest mismatch")
        if not _text(output.get("schema")) or not _text(output.get("evidence_ref")):
            raise HostSkillBridgeError("Host execution output identity is incomplete")

        rows = payload.get("tool_receipts")
        if not isinstance(rows, list) or not rows:
            raise HostSkillBridgeError("Host execution requires one or more tool_receipts")
        receipts = [self._validate_tool_receipt(row) for row in rows]
        ids = [_text(row.get("tool_call_id")) for row in receipts]
        if len(ids) != len(set(ids)):
            raise HostSkillBridgeError("Host execution tool_call_id values must be unique")
        refs = payload.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not _text(ref) for ref in refs):
            raise HostSkillBridgeError("Host execution requires durable evidence_refs")
        if not isinstance(payload.get("payload"), Mapping):
            raise HostSkillBridgeError("Host execution payload must be an object")
        if payload.get("problem_ledger_ref") is not None and not _text(
            payload.get("problem_ledger_ref")
        ):
            raise HostSkillBridgeError("problem_ledger_ref must be null or non-empty")
        return payload

    def submit_result(self, *, execution_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and immutably persist a result produced by the real Host."""

        execution_id = _safe_id(execution_id, field="execution_id")
        _, request = self._load_request(execution_id)
        payload = self._validate_result(request=request, result=result)
        payload["result_fingerprint_sha256"] = _payload_digest(
            payload, omit="result_fingerprint_sha256"
        )
        path = self._execution_dir(execution_id) / "result.json"
        try:
            self._atomic_create(path, payload)
        except FileExistsError:
            existing = self._load_json(path)
            if existing != payload:
                raise HostSkillBridgeError("conflicting Host execution result resubmission")
        return {
            "schema": HOST_RESUME_SCHEMA,
            "event": "host.skill.completed",
            "execution_id": execution_id,
            "result_ref": self._ref(path),
            "result_sha256": _digest_bytes(path.read_bytes()),
            "authority_effect": False,
        }

    def _resume_result(
        self,
        *,
        request: Mapping[str, Any],
        pointer: Mapping[str, Any],
    ) -> SkillHostResult:
        if pointer.get("schema") != HOST_RESUME_SCHEMA or pointer.get("event") != "host.skill.completed":
            raise HostSkillBridgeError("unsupported Host execution resume event")
        if pointer.get("execution_id") != request.get("execution_id"):
            raise HostSkillBridgeError("Host execution resume identity mismatch")
        if pointer.get("authority_effect") is not False:
            raise HostSkillBridgeError("Host resume evidence cannot change authority")
        path = self._path_from_ref(pointer.get("result_ref"))
        expected_path = self._execution_dir(_text(request.get("execution_id"))) / "result.json"
        if path.resolve() != expected_path.resolve():
            raise HostSkillBridgeError("Host execution result_ref does not match request identity")
        if _digest_bytes(path.read_bytes()) != _text(pointer.get("result_sha256")):
            raise HostSkillBridgeError("Host execution result file digest mismatch")
        result = self._load_json(path)
        if result.get("result_fingerprint_sha256") != _payload_digest(
            result, omit="result_fingerprint_sha256"
        ):
            raise HostSkillBridgeError("Host execution result fingerprint mismatch")
        validated = self._validate_result(request=request, result=result)
        output = dict(validated["output"])
        refs = [str(ref) for ref in validated["evidence_refs"]]
        refs.extend(
            str(row["evidence_ref"])
            for row in validated["tool_receipts"]
            if str(row["evidence_ref"]) not in refs
        )
        result_ref = self._ref(path)
        if result_ref not in refs:
            refs.append(result_ref)
        return SkillHostResult(
            outcome=_text(validated["outcome"]),
            output_schema=_text(output["schema"]),
            output_content=str(output["content"]),
            output_evidence_ref=_text(output["evidence_ref"]),
            evidence_refs=tuple(refs),
            payload=dict(validated["payload"]),
            problem_ledger_ref=_text(validated.get("problem_ledger_ref")) or None,
        )

    def execute(
        self,
        *,
        skill_name: str,
        request_class: str,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> SkillHostResult:
        execution_id = self._execution_id(skill_name=skill_name, step=step, state=state)
        request = self._build_request(
            execution_id=execution_id,
            skill_name=skill_name,
            request_class=request_class,
            step=step,
            state=state,
        )
        request_path = self._persist_request(request)
        pointer = state.get("host_execution_result")
        if isinstance(pointer, Mapping) and pointer.get("execution_id") == execution_id:
            return self._resume_result(request=request, pointer=pointer)
        request_ref = self._ref(request_path)
        raise HostExecutionPending(
            host_wait={
                "schema": HOST_WAIT_SCHEMA,
                "host_id": self.host_id,
                "execution_id": execution_id,
                "task_id": request["task_id"],
                "workflow_id": request["workflow_id"],
                "step_id": request["step_id"],
                "skill_name": request["skill"]["name"],
                "skill_sha256": request["skill"]["sha256"],
                "request_ref": request_ref,
                "request_fingerprint_sha256": request["request_fingerprint_sha256"],
                "resume_event": "host.skill.completed",
                "authority_effect": False,
            },
            evidence_refs=(request_ref,),
        )


__all__ = [
    "DurableHostSkillBridge",
    "HOST_REQUEST_SCHEMA",
    "HOST_RESULT_SCHEMA",
    "HOST_RESUME_SCHEMA",
    "HOST_TOOL_RECEIPT_SCHEMA",
    "HOST_WAIT_SCHEMA",
    "HostSkillBridgeError",
]
