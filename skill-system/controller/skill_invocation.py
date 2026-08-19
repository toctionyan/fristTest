from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

SKILL_INVOCATION_RECEIPT_SCHEMA = "skill-invocation-receipt@1"
SKILL_CONTEXT_SCHEMA = "skill-context@1"
CURRENT_RECEIPT = Path(".quality/skill-invocations/current.json")
_REQUIRED_PHASES = ("discovery", "selection", "load", "execution", "output_binding")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SkillInvocationError(ValueError):
    """Raised when runtime Skill invocation evidence is absent or invalid."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _safe_token(value: str, *, label: str) -> str:
    text = _text(value)
    if not text or not _SAFE_TOKEN.fullmatch(text):
        raise SkillInvocationError(f"{label} contains unsupported characters")
    return text


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_skill_path(skill_name: str) -> Path:
    return Path("skill-system") / "skills" / _safe_token(skill_name, label="skill name") / "SKILL.md"


def canonical_skill_identity(workspace: Path, skill_name: str) -> tuple[Path, str]:
    relative = canonical_skill_path(skill_name)
    path = workspace / relative
    if not path.is_file():
        raise SkillInvocationError(f"canonical Skill is missing: {relative.as_posix()}")
    return relative, _sha256_bytes(path.read_bytes())


def _receipt_fingerprint(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("receipt_fingerprint_sha256", None)
    return _sha256_bytes(_canonical_json(body))


def build_receipt(
    workspace: Path,
    *,
    invocation_id: str,
    request_class: str,
    required_skill: str,
    selected_skill: str,
    entrypoint: str,
    output_schema: str,
    output_content: str | bytes,
    output_evidence_ref: str,
    change_id: str | None = None,
    task_id: str | None = None,
    response_bound: bool = False,
) -> dict[str, Any]:
    """Build PASS evidence only after the canonical Skill and output are materialized.

    `output_binding=PASS` means the invocation output was returned to the host context.
    `response_bound` is stronger: the invoked entrypoint produced the deterministic
    user-facing response payload. Repository-local evidence cannot prove that an
    external product UI actually emitted that payload.
    """

    invocation_id = _safe_token(invocation_id, label="invocation id")
    request_class = _safe_token(request_class.upper(), label="request class")
    required_skill = _safe_token(required_skill, label="required skill")
    selected_skill = _safe_token(selected_skill, label="selected skill")
    if selected_skill != required_skill:
        raise SkillInvocationError("selected Skill does not match the required Skill")
    relative, digest = canonical_skill_identity(workspace, selected_skill)
    entry = Path(_text(entrypoint))
    if entry.is_absolute() or ".." in entry.parts:
        raise SkillInvocationError("entrypoint must be a workspace-relative path")
    if not (workspace / entry).is_file():
        raise SkillInvocationError(f"Skill entrypoint is missing: {entry.as_posix()}")
    output_schema = _text(output_schema)
    output_evidence_ref = _text(output_evidence_ref)
    if not output_schema or not output_evidence_ref:
        raise SkillInvocationError("output schema and evidence reference are required")
    raw_output = output_content.encode("utf-8") if isinstance(output_content, str) else bytes(output_content)
    if not raw_output:
        raise SkillInvocationError("Skill invocation output must not be empty")

    subject: dict[str, str] = {}
    if change_id:
        subject["change_id"] = _safe_token(change_id, label="change id")
    if task_id:
        subject["task_id"] = _safe_token(task_id, label="task id")
    payload: dict[str, Any] = {
        "schema": SKILL_INVOCATION_RECEIPT_SCHEMA,
        "status": "PASS",
        "invocation_id": invocation_id,
        "request_class": request_class,
        "required_skill": required_skill,
        "selected_skill": selected_skill,
        "canonical_skill_path": relative.as_posix(),
        "loaded_sha256": digest,
        "entrypoint": entry.as_posix(),
        "phases": {name: "PASS" for name in _REQUIRED_PHASES},
        "subject": subject,
        "output": {
            "schema": output_schema,
            "sha256": _sha256_bytes(raw_output),
            "evidence_ref": output_evidence_ref,
            "host_context_bound": True,
            "response_bound": bool(response_bound),
        },
        "authority_effect": False,
    }
    payload["receipt_fingerprint_sha256"] = _receipt_fingerprint(payload)
    return payload


def validate_receipt(
    workspace: Path,
    payload: Mapping[str, Any],
    *,
    expected_request_class: str | None = None,
    expected_skill: str | None = None,
    expected_change_id: str | None = None,
    expected_task_id: str | None = None,
    require_response_bound: bool = False,
) -> dict[str, Any]:
    if payload.get("schema") != SKILL_INVOCATION_RECEIPT_SCHEMA:
        raise SkillInvocationError("unsupported Skill invocation receipt schema")
    if payload.get("status") != "PASS":
        raise SkillInvocationError("Skill invocation receipt is not PASS")
    if payload.get("authority_effect") is not False:
        raise SkillInvocationError("Skill invocation receipt must be read-only evidence")
    if payload.get("receipt_fingerprint_sha256") != _receipt_fingerprint(payload):
        raise SkillInvocationError("Skill invocation receipt fingerprint mismatch")

    request_class = _text(payload.get("request_class")).upper()
    required_skill = _text(payload.get("required_skill"))
    selected_skill = _text(payload.get("selected_skill"))
    _safe_token(request_class, label="request class")
    _safe_token(required_skill, label="required skill")
    _safe_token(selected_skill, label="selected skill")
    if selected_skill != required_skill:
        raise SkillInvocationError("selected Skill does not match the required Skill")
    if expected_request_class and request_class != _text(expected_request_class).upper():
        raise SkillInvocationError(
            f"Skill invocation request class mismatch: expected {expected_request_class}, got {request_class}"
        )
    if expected_skill and selected_skill != expected_skill:
        raise SkillInvocationError(
            f"Skill invocation mismatch: expected {expected_skill}, got {selected_skill}"
        )

    relative, digest = canonical_skill_identity(workspace, selected_skill)
    if payload.get("canonical_skill_path") != relative.as_posix():
        raise SkillInvocationError("Skill invocation canonical path mismatch")
    if payload.get("loaded_sha256") != digest:
        raise SkillInvocationError("Skill invocation is stale: canonical Skill digest changed")

    entry = Path(_text(payload.get("entrypoint")))
    if entry.is_absolute() or ".." in entry.parts or not (workspace / entry).is_file():
        raise SkillInvocationError("Skill invocation entrypoint is missing or unsafe")
    phases = payload.get("phases") if isinstance(payload.get("phases"), Mapping) else {}
    for name in _REQUIRED_PHASES:
        if phases.get(name) != "PASS":
            raise SkillInvocationError(f"Skill invocation phase is not PASS: {name}")

    subject = payload.get("subject") if isinstance(payload.get("subject"), Mapping) else {}
    if expected_change_id and _text(subject.get("change_id")) != expected_change_id:
        raise SkillInvocationError("Skill invocation change_id does not match the active Change Contract")
    if expected_task_id and _text(subject.get("task_id")) != expected_task_id:
        raise SkillInvocationError("Skill invocation task_id does not match the authoritative TaskRun")

    output = payload.get("output") if isinstance(payload.get("output"), Mapping) else {}
    if not _text(output.get("schema")) or not _text(output.get("evidence_ref")):
        raise SkillInvocationError("Skill invocation output identity is incomplete")
    if not _SHA256.fullmatch(_text(output.get("sha256"))):
        raise SkillInvocationError("Skill invocation output digest is invalid")
    if output.get("host_context_bound") is not True:
        raise SkillInvocationError("Skill invocation output was not bound to host context")
    if require_response_bound and output.get("response_bound") is not True:
        raise SkillInvocationError("Skill invocation output is not bound as the deterministic response payload")
    return dict(payload)


def receipt_path(workspace: Path, invocation_id: str) -> Path:
    safe = _safe_token(invocation_id, label="invocation id")
    return workspace / ".quality" / "skill-invocations" / f"{safe}.json"


def write_receipt(workspace: Path, payload: Mapping[str, Any], *, make_current: bool = True) -> Path:
    validated = validate_receipt(workspace, payload)
    path = receipt_path(workspace, _text(validated.get("invocation_id")))
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(rendered, encoding="utf-8")
    if make_current:
        current = workspace / CURRENT_RECEIPT
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text(rendered, encoding="utf-8")
    return path


def load_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SkillInvocationError(f"Skill invocation receipt is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SkillInvocationError(f"Skill invocation receipt is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise SkillInvocationError("Skill invocation receipt must be a JSON object")
    return payload


def current_receipt(workspace: Path) -> dict[str, Any]:
    return load_receipt(workspace / CURRENT_RECEIPT)


def require_change_scope_invocation(workspace: Path, *, change_id: str) -> dict[str, Any]:
    """Fail closed for supported host writes unless `change-scope` was really loaded.

    The receipt is tied to the unique Change Contract id and current canonical
    Skill digest, so a static adapter file or a stale load cannot satisfy it.
    """

    return validate_receipt(
        workspace,
        current_receipt(workspace),
        expected_request_class="CHANGE_SCOPE",
        expected_skill="change-scope",
        expected_change_id=change_id,
    )
