from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


GITHUB_WEBHOOK_EVIDENCE_SCHEMA = "github-workflow-run-webhook-evidence@1"
GITHUB_WEBHOOK_RECEIPT_SCHEMA = "github-workflow-run-webhook-receipt@1"
GITHUB_WEBHOOK_RESULT_SCHEMA = "provider-webhook-result@1"
GITHUB_WEBHOOK_PATH_PREFIX = "/v1/github/workflow-run/"

_ENVIRONMENT_VARIABLE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DELIVERY_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9._:-]+$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PROVIDER_ID = re.compile(r"^[A-Za-z0-9._:-]+$")
_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
_SIGNATURE = re.compile(r"^sha256=([0-9a-f]{64})$")
_EVIDENCE_FIELDS = {
    "schema",
    "delivery_id",
    "event_type",
    "session_id",
    "host_id",
    "provider",
    "repository",
    "workflow_run_id",
    "run_attempt",
    "head_sha",
    "conclusion",
    "payload_sha256",
    "payload_body_base64",
    "signature_algorithm",
    "request_signature",
    "signature_verified",
    "received_at",
    "authority_effect",
    "evidence_sha256",
}
_RECEIPT_FIELDS = {
    "schema",
    "delivery_id",
    "session_id",
    "host_id",
    "provider",
    "repository",
    "workflow_run_id",
    "payload_sha256",
    "provider_evidence_ref",
    "scheduler_ingest",
    "scheduler_wake",
    "transport_status",
    "created_at",
    "authority_effect",
    "completion_authority_changed",
    "merge_authority_changed",
    "receipt_sha256",
}


class GitHubWebhookExternalEventTransportError(RuntimeError):
    """Raised when a GitHub webhook cannot cross the trusted ingress boundary."""

    def __init__(self, message: str, *, code: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GitHubWebhookExternalEventTransportError(
            "provider webhook contains non-JSON data",
            code="INVALID_JSON_VALUE",
            http_status=400,
        ) from exc


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: object) -> str:
    return _digest_bytes(_canonical(value))


def _seal(payload: Mapping[str, Any], *, field: str) -> dict[str, Any]:
    result = dict(payload)
    result.pop(field, None)
    result[field] = _digest(result)
    return result


def _closed(payload: Mapping[str, Any], fields: set[str], *, label: str) -> None:
    missing = sorted(fields - set(payload))
    unexpected = sorted(set(payload) - fields)
    if missing or unexpected:
        raise GitHubWebhookExternalEventTransportError(
            f"{label} fields are not closed: missing={missing} unexpected={unexpected}",
            code="UNSAFE_DURABLE_ARTIFACT",
            http_status=500,
        )


def _bounded_root(workspace: Path, raw: str | Path, *, field: str) -> Path:
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise GitHubWebhookExternalEventTransportError(
            f"{field} must be a bounded workspace-relative path",
            code="INVALID_CONFIGURATION",
            http_status=500,
        )
    path = workspace / relative
    try:
        path.resolve().relative_to(workspace)
    except ValueError as exc:
        raise GitHubWebhookExternalEventTransportError(
            f"{field} escapes the project workspace",
            code="INVALID_CONFIGURATION",
            http_status=500,
        ) from exc
    current = path
    while current != workspace:
        if current.is_symlink():
            raise GitHubWebhookExternalEventTransportError(
                f"{field} cannot use symlinks",
                code="UNSAFE_PATH",
                http_status=500,
            )
        current = current.parent
    return path


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise GitHubWebhookExternalEventTransportError(
            f"{label} is missing or unsafe",
            code="UNSAFE_DURABLE_ARTIFACT",
            http_status=500,
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitHubWebhookExternalEventTransportError(
            f"{label} is unreadable",
            code="UNSAFE_DURABLE_ARTIFACT",
            http_status=500,
        ) from exc
    if not isinstance(value, dict):
        raise GitHubWebhookExternalEventTransportError(
            f"{label} must be an object",
            code="UNSAFE_DURABLE_ARTIFACT",
            http_status=500,
        )
    return value


def _atomic_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise GitHubWebhookExternalEventTransportError(
            "refusing to replace an unsafe provider webhook artifact",
            code="UNSAFE_DURABLE_ARTIFACT",
            http_status=500,
        )
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _header_map(headers: Mapping[str, object]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, raw in headers.items():
        name = str(key).strip().lower()
        value = str(raw).strip()
        if not name or not value or name in normalized:
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook headers are ambiguous",
                code="INVALID_HEADERS",
                http_status=400,
            )
        normalized[name] = value
    return normalized


class GitHubWorkflowRunWebhookTransport:
    """Authenticate and deliver GitHub workflow_run events to the existing Scheduler.

    This transport owns only Provider ingress authentication, raw evidence, and
    delivery replay. It does not interpret CI outcomes, select Workflows, mutate
    Host/TaskRun state, or grant any authority.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        scheduler: Any,
        host_id: str,
        repository_full_name: str,
        provider_id: str = "github.actions",
        secret_environment_variable: str = "GITHUB_WEBHOOK_SECRET",
        secret: bytes | None = None,
        evidence_root: str | Path = ".harness/runtime/provider-webhook-evidence/github",
        receipt_root: str | Path = ".harness/runtime/provider-webhook-receipts/github",
        lock_root: str | Path = ".harness/runtime/provider-webhook-locks/github",
        max_body_bytes: int = 1024 * 1024,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise GitHubWebhookExternalEventTransportError(
                "workspace must be an existing directory",
                code="INVALID_CONFIGURATION",
                http_status=500,
            )
        self.scheduler = scheduler
        self.host_id = str(host_id).strip()
        if self.host_id not in {"chatgpt", "codex"}:
            raise GitHubWebhookExternalEventTransportError(
                "unsupported Host identity",
                code="INVALID_CONFIGURATION",
                http_status=500,
            )
        self.repository_full_name = str(repository_full_name).strip()
        if not _REPOSITORY.fullmatch(self.repository_full_name):
            raise GitHubWebhookExternalEventTransportError(
                "repository_full_name must use owner/repository",
                code="INVALID_CONFIGURATION",
                http_status=500,
            )
        self.provider_id = str(provider_id).strip()
        if not _PROVIDER_ID.fullmatch(self.provider_id):
            raise GitHubWebhookExternalEventTransportError(
                "provider_id must be stable",
                code="INVALID_CONFIGURATION",
                http_status=500,
            )
        self.secret_environment_variable = str(
            secret_environment_variable
        ).strip()
        if not _ENVIRONMENT_VARIABLE.fullmatch(self.secret_environment_variable):
            raise GitHubWebhookExternalEventTransportError(
                "secret_environment_variable must be an uppercase name",
                code="INVALID_CONFIGURATION",
                http_status=500,
            )
        if secret is not None and (not isinstance(secret, bytes) or len(secret) < 16):
            raise GitHubWebhookExternalEventTransportError(
                "webhook secret must contain at least 16 bytes",
                code="INVALID_CONFIGURATION",
                http_status=500,
            )
        self._secret_override = secret
        self.evidence_root = _bounded_root(
            self.workspace, evidence_root, field="provider_webhook.evidence_root"
        )
        self.receipt_root = _bounded_root(
            self.workspace, receipt_root, field="provider_webhook.receipt_root"
        )
        self.lock_root = _bounded_root(
            self.workspace, lock_root, field="provider_webhook.lock_root"
        )
        if len({self.evidence_root, self.receipt_root, self.lock_root}) != 3:
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook roots must be distinct",
                code="INVALID_CONFIGURATION",
                http_status=500,
            )
        if (
            not isinstance(max_body_bytes, int)
            or isinstance(max_body_bytes, bool)
            or not 1 <= max_body_bytes <= 10 * 1024 * 1024
        ):
            raise GitHubWebhookExternalEventTransportError(
                "max_body_bytes must be 1..10485760",
                code="INVALID_CONFIGURATION",
                http_status=500,
            )
        self.max_body_bytes = max_body_bytes

    def _secret(self) -> bytes:
        if self._secret_override is not None:
            return self._secret_override
        value = os.environ.get(self.secret_environment_variable, "")
        encoded = value.encode("utf-8")
        if len(encoded) < 16:
            raise GitHubWebhookExternalEventTransportError(
                f"GitHub webhook secret environment variable is missing or too short: {self.secret_environment_variable}",
                code="MISSING_WEBHOOK_SECRET",
                http_status=503,
            )
        return encoded

    @staticmethod
    def _session_id(value: object) -> str:
        session_id = str(value or "").strip()
        if not session_id or not _SESSION_ID.fullmatch(session_id):
            raise GitHubWebhookExternalEventTransportError(
                "session_id must be a stable route identifier",
                code="INVALID_SESSION_ROUTE",
                http_status=404,
            )
        return session_id

    def _authenticate(
        self, headers: Mapping[str, object], body: bytes
    ) -> tuple[dict[str, str], str, str]:
        if not isinstance(body, bytes) or not body or len(body) > self.max_body_bytes:
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook body is empty or exceeds the configured limit",
                code="INVALID_BODY_SIZE",
                http_status=413,
            )
        values = _header_map(headers)
        content_type = values.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise GitHubWebhookExternalEventTransportError(
                "GitHub webhook Content-Type must be application/json",
                code="INVALID_CONTENT_TYPE",
                http_status=415,
            )
        if values.get("x-github-event") != "workflow_run":
            raise GitHubWebhookExternalEventTransportError(
                "only GitHub workflow_run webhooks are supported",
                code="UNSUPPORTED_GITHUB_EVENT",
                http_status=400,
            )
        delivery_id = values.get("x-github-delivery", "")
        if not _DELIVERY_ID.fullmatch(delivery_id):
            raise GitHubWebhookExternalEventTransportError(
                "X-GitHub-Delivery is missing or invalid",
                code="INVALID_DELIVERY_ID",
                http_status=400,
            )
        raw_signature = values.get("x-hub-signature-256", "")
        matched = _SIGNATURE.fullmatch(raw_signature)
        if matched is None:
            raise GitHubWebhookExternalEventTransportError(
                "X-Hub-Signature-256 is missing or invalid",
                code="INVALID_SIGNATURE",
                http_status=401,
            )
        expected = hmac.new(self._secret(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, matched.group(1)):
            raise GitHubWebhookExternalEventTransportError(
                "GitHub webhook signature verification failed",
                code="INVALID_SIGNATURE",
                http_status=401,
            )
        return values, delivery_id, raw_signature

    def _normalize_payload(self, body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubWebhookExternalEventTransportError(
                "GitHub webhook body must be one UTF-8 JSON object",
                code="INVALID_JSON",
                http_status=400,
            ) from exc
        if not isinstance(payload, Mapping):
            raise GitHubWebhookExternalEventTransportError(
                "GitHub webhook body must be one JSON object",
                code="INVALID_JSON",
                http_status=400,
            )
        repository = payload.get("repository")
        workflow_run = payload.get("workflow_run")
        if payload.get("action") != "completed":
            raise GitHubWebhookExternalEventTransportError(
                "GitHub workflow_run action must be completed",
                code="UNSUPPORTED_WORKFLOW_ACTION",
                http_status=400,
            )
        if not isinstance(repository, Mapping) or repository.get(
            "full_name"
        ) != self.repository_full_name:
            raise GitHubWebhookExternalEventTransportError(
                "GitHub webhook repository does not match the configured repository",
                code="REPOSITORY_MISMATCH",
                http_status=403,
            )
        if not isinstance(workflow_run, Mapping) or workflow_run.get(
            "status"
        ) != "completed":
            raise GitHubWebhookExternalEventTransportError(
                "GitHub workflow_run status must be completed",
                code="INVALID_WORKFLOW_RUN",
                http_status=400,
            )
        run_id = workflow_run.get("id")
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
            raise GitHubWebhookExternalEventTransportError(
                "GitHub workflow_run id must be a positive integer",
                code="INVALID_WORKFLOW_RUN",
                http_status=400,
            )
        run_attempt = workflow_run.get("run_attempt", 1)
        if (
            not isinstance(run_attempt, int)
            or isinstance(run_attempt, bool)
            or run_attempt < 1
        ):
            raise GitHubWebhookExternalEventTransportError(
                "GitHub workflow_run run_attempt must be a positive integer",
                code="INVALID_WORKFLOW_RUN",
                http_status=400,
            )
        head_sha = str(workflow_run.get("head_sha") or "").strip().lower()
        if not _HEAD_SHA.fullmatch(head_sha):
            raise GitHubWebhookExternalEventTransportError(
                "GitHub workflow_run head_sha must be a full commit SHA",
                code="INVALID_WORKFLOW_RUN",
                http_status=400,
            )
        conclusion = str(workflow_run.get("conclusion") or "").strip().lower()
        if not conclusion or len(conclusion) > 64 or not re.fullmatch(
            r"[a-z0-9_-]+", conclusion
        ):
            raise GitHubWebhookExternalEventTransportError(
                "GitHub workflow_run conclusion is missing or invalid",
                code="INVALID_WORKFLOW_RUN",
                http_status=400,
            )
        return {
            "repository": self.repository_full_name,
            "workflow_run_id": run_id,
            "run_attempt": run_attempt,
            "head_sha": head_sha,
            "conclusion": conclusion,
        }

    @staticmethod
    def _artifact_key(delivery_id: str) -> str:
        return hashlib.sha256(delivery_id.encode("utf-8")).hexdigest()

    def _paths(self, delivery_id: str) -> tuple[Path, Path, Path]:
        key = self._artifact_key(delivery_id)
        return (
            self.evidence_root / f"{key}.json",
            self.receipt_root / f"{key}.json",
            self.lock_root / f"{key}.lock",
        )

    def _validate_evidence(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(payload)
        _closed(value, _EVIDENCE_FIELDS, label="provider webhook evidence")
        if value.get("schema") != GITHUB_WEBHOOK_EVIDENCE_SCHEMA:
            raise GitHubWebhookExternalEventTransportError(
                "unsupported provider webhook evidence schema",
                code="UNSAFE_DURABLE_ARTIFACT",
                http_status=500,
            )
        digest = value.get("evidence_sha256")
        unsigned = dict(value)
        unsigned.pop("evidence_sha256", None)
        if not isinstance(digest, str) or digest != _digest(unsigned):
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook evidence fingerprint mismatch",
                code="UNSAFE_DURABLE_ARTIFACT",
                http_status=500,
            )
        try:
            body = base64.b64decode(
                str(value.get("payload_body_base64") or ""), validate=True
            )
        except (ValueError, TypeError) as exc:
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook evidence body is invalid",
                code="UNSAFE_DURABLE_ARTIFACT",
                http_status=500,
            ) from exc
        if _digest_bytes(body) != value.get("payload_sha256"):
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook evidence body fingerprint mismatch",
                code="UNSAFE_DURABLE_ARTIFACT",
                http_status=500,
            )
        if (
            value.get("signature_algorithm") != "hmac-sha256"
            or not isinstance(value.get("request_signature"), str)
            or value.get("signature_verified") is not True
            or value.get("authority_effect") is not False
        ):
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook evidence policy is invalid",
                code="UNSAFE_DURABLE_ARTIFACT",
                http_status=500,
            )
        matched = _SIGNATURE.fullmatch(value["request_signature"])
        expected = hmac.new(self._secret(), body, hashlib.sha256).hexdigest()
        if matched is None or not hmac.compare_digest(expected, matched.group(1)):
            raise GitHubWebhookExternalEventTransportError(
                "persisted provider webhook signature no longer authenticates its body",
                code="UNSAFE_DURABLE_ARTIFACT",
                http_status=500,
            )
        normalized = self._normalize_payload(body)
        expected_fields = {
            "event_type": "workflow_run",
            "host_id": self.host_id,
            "provider": self.provider_id,
            **normalized,
        }
        if any(value.get(field) != expected for field, expected in expected_fields.items()):
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook evidence does not match its authenticated body",
                code="UNSAFE_DURABLE_ARTIFACT",
                http_status=500,
            )
        return value

    def _validate_receipt(
        self, payload: Mapping[str, Any], *, evidence: Mapping[str, Any]
    ) -> dict[str, Any]:
        value = dict(payload)
        _closed(value, _RECEIPT_FIELDS, label="provider webhook receipt")
        if value.get("schema") != GITHUB_WEBHOOK_RECEIPT_SCHEMA:
            raise GitHubWebhookExternalEventTransportError(
                "unsupported provider webhook receipt schema",
                code="UNSAFE_DURABLE_ARTIFACT",
                http_status=500,
            )
        digest = value.get("receipt_sha256")
        unsigned = dict(value)
        unsigned.pop("receipt_sha256", None)
        if not isinstance(digest, str) or digest != _digest(unsigned):
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook receipt fingerprint mismatch",
                code="UNSAFE_DURABLE_ARTIFACT",
                http_status=500,
            )
        for field in (
            "delivery_id",
            "session_id",
            "host_id",
            "provider",
            "repository",
            "workflow_run_id",
            "payload_sha256",
        ):
            if value.get(field) != evidence.get(field):
                raise GitHubWebhookExternalEventTransportError(
                    "provider webhook receipt does not match its evidence",
                    code="UNSAFE_DURABLE_ARTIFACT",
                    http_status=500,
                )
        if (
            value.get("authority_effect") is not False
            or value.get("completion_authority_changed") is not False
            or value.get("merge_authority_changed") is not False
        ):
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook receipt cannot grant authority",
                code="UNSAFE_DURABLE_ARTIFACT",
                http_status=500,
            )
        return value

    @staticmethod
    def _result(receipt: Mapping[str, Any], receipt_path: Path, workspace: Path) -> dict[str, Any]:
        ingest = receipt.get("scheduler_ingest")
        wake = receipt.get("scheduler_wake")
        return {
            "schema": GITHUB_WEBHOOK_RESULT_SCHEMA,
            "status": receipt.get("transport_status"),
            "delivery_id": receipt.get("delivery_id"),
            "session_id": receipt.get("session_id"),
            "host_id": receipt.get("host_id"),
            "provider": receipt.get("provider"),
            "repository": receipt.get("repository"),
            "workflow_run_id": receipt.get("workflow_run_id"),
            "provider_evidence_ref": receipt.get("provider_evidence_ref"),
            "transport_receipt_ref": f"file:{receipt_path.relative_to(workspace).as_posix()}",
            "scheduler_event_ref": (
                ingest.get("event_ref") if isinstance(ingest, Mapping) else None
            ),
            "scheduler_receipt_ref": (
                wake.get("receipt_ref") if isinstance(wake, Mapping) else None
            ),
            "scheduler_status": wake.get("status") if isinstance(wake, Mapping) else None,
            "authority_effect": False,
            "completion_authority_changed": False,
            "merge_authority_changed": False,
        }

    def receive(
        self,
        *,
        session_id: str,
        headers: Mapping[str, object],
        body: bytes,
    ) -> dict[str, Any]:
        session_id = self._session_id(session_id)
        _values, delivery_id, request_signature = self._authenticate(headers, body)
        normalized = self._normalize_payload(body)
        payload_sha256 = _digest_bytes(body)
        evidence_path, receipt_path, lock_path = self._paths(delivery_id)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if lock_path.is_symlink():
            raise GitHubWebhookExternalEventTransportError(
                "provider webhook delivery lock is unsafe",
                code="UNSAFE_PATH",
                http_status=500,
            )
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            evidence = _seal(
                {
                    "schema": GITHUB_WEBHOOK_EVIDENCE_SCHEMA,
                    "delivery_id": delivery_id,
                    "event_type": "workflow_run",
                    "session_id": session_id,
                    "host_id": self.host_id,
                    "provider": self.provider_id,
                    **normalized,
                    "payload_sha256": payload_sha256,
                    "payload_body_base64": base64.b64encode(body).decode("ascii"),
                    "signature_algorithm": "hmac-sha256",
                    "request_signature": request_signature,
                    "signature_verified": True,
                    "received_at": _now(),
                    "authority_effect": False,
                },
                field="evidence_sha256",
            )
            evidence = self._validate_evidence(evidence)
            if evidence_path.exists():
                existing = self._validate_evidence(
                    _read_object(evidence_path, label="persisted provider webhook evidence")
                )
                stable_fields = _EVIDENCE_FIELDS - {"received_at", "evidence_sha256"}
                if any(existing[field] != evidence[field] for field in stable_fields):
                    raise GitHubWebhookExternalEventTransportError(
                        "GitHub delivery identity was replayed with different bytes or route",
                        code="DELIVERY_REPLAY_CONFLICT",
                        http_status=409,
                    )
                evidence = existing
            else:
                try:
                    _atomic_exclusive(evidence_path, evidence)
                except FileExistsError:
                    existing = self._validate_evidence(
                        _read_object(
                            evidence_path,
                            label="persisted provider webhook evidence",
                        )
                    )
                    stable_fields = _EVIDENCE_FIELDS - {
                        "received_at",
                        "evidence_sha256",
                    }
                    if any(existing[field] != evidence[field] for field in stable_fields):
                        raise GitHubWebhookExternalEventTransportError(
                            "GitHub delivery identity was replayed with different bytes or route",
                            code="DELIVERY_REPLAY_CONFLICT",
                            http_status=409,
                        )
                    evidence = existing

            if receipt_path.exists():
                receipt = self._validate_receipt(
                    _read_object(receipt_path, label="persisted provider webhook receipt"),
                    evidence=evidence,
                )
                return self._result(receipt, receipt_path, self.workspace)

            evidence_ref = f"file:{evidence_path.relative_to(self.workspace).as_posix()}"
            durable_run_ref = (
                f"github-workflow-run:{self.repository_full_name}:"
                f"{normalized['workflow_run_id']}:{normalized['head_sha']}"
            )
            scheduler_event = {
                "provider": self.provider_id,
                "correlation_ref": f"run-{normalized['workflow_run_id']}",
                "event": "ci.completed",
                "conclusion": normalized["conclusion"],
                "repository": self.repository_full_name,
                "workflow_run_id": normalized["workflow_run_id"],
                "run_attempt": normalized["run_attempt"],
                "head_sha": normalized["head_sha"],
                "delivery_id": delivery_id,
                "evidence_refs": [evidence_ref, durable_run_ref],
            }
            try:
                ingest = dict(
                    self.scheduler.ingest(
                        session_id=session_id,
                        event=scheduler_event,
                    )
                )
                event_ref = ingest.get("event_ref")
                if ingest.get("status") != "QUEUED" or not isinstance(event_ref, str):
                    raise GitHubWebhookExternalEventTransportError(
                        "Scheduler did not persist the authenticated external event",
                        code="SCHEDULER_REJECTED",
                        http_status=409,
                    )
                wake = dict(self.scheduler.wake(event_ref=event_ref))
            except GitHubWebhookExternalEventTransportError:
                raise
            except Exception as exc:
                raise GitHubWebhookExternalEventTransportError(
                    "Scheduler rejected the authenticated external event",
                    code="SCHEDULER_REJECTED",
                    http_status=409,
                ) from exc
            transport_status = "PASS" if wake.get("status") == "DELIVERED" else "REJECTED"
            receipt = _seal(
                {
                    "schema": GITHUB_WEBHOOK_RECEIPT_SCHEMA,
                    "delivery_id": delivery_id,
                    "session_id": session_id,
                    "host_id": self.host_id,
                    "provider": self.provider_id,
                    "repository": self.repository_full_name,
                    "workflow_run_id": normalized["workflow_run_id"],
                    "payload_sha256": payload_sha256,
                    "provider_evidence_ref": evidence_ref,
                    "scheduler_ingest": ingest,
                    "scheduler_wake": wake,
                    "transport_status": transport_status,
                    "created_at": _now(),
                    "authority_effect": False,
                    "completion_authority_changed": False,
                    "merge_authority_changed": False,
                },
                field="receipt_sha256",
            )
            receipt = self._validate_receipt(receipt, evidence=evidence)
            try:
                _atomic_exclusive(receipt_path, receipt)
            except FileExistsError:
                receipt = self._validate_receipt(
                    _read_object(receipt_path, label="persisted provider webhook receipt"),
                    evidence=evidence,
                )
            return self._result(receipt, receipt_path, self.workspace)

    @staticmethod
    def _json_response(
        start_response: Callable[..., Any],
        *,
        status: str,
        payload: Mapping[str, Any],
    ) -> Iterable[bytes]:
        body = json.dumps(
            dict(payload), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        start_response(
            status,
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ],
        )
        return [body]

    def __call__(
        self,
        environ: Mapping[str, Any],
        start_response: Callable[..., Any],
    ) -> Iterable[bytes]:
        try:
            if environ.get("REQUEST_METHOD") != "POST":
                raise GitHubWebhookExternalEventTransportError(
                    "provider webhook endpoint accepts POST only",
                    code="METHOD_NOT_ALLOWED",
                    http_status=405,
                )
            path = str(environ.get("PATH_INFO") or "")
            if not path.startswith(GITHUB_WEBHOOK_PATH_PREFIX):
                raise GitHubWebhookExternalEventTransportError(
                    "provider webhook route does not exist",
                    code="INVALID_SESSION_ROUTE",
                    http_status=404,
                )
            session_id = path.removeprefix(GITHUB_WEBHOOK_PATH_PREFIX)
            if "/" in session_id:
                raise GitHubWebhookExternalEventTransportError(
                    "provider webhook route does not exist",
                    code="INVALID_SESSION_ROUTE",
                    http_status=404,
                )
            length_text = str(environ.get("CONTENT_LENGTH") or "")
            try:
                content_length = int(length_text)
            except ValueError as exc:
                raise GitHubWebhookExternalEventTransportError(
                    "provider webhook Content-Length is required",
                    code="INVALID_BODY_SIZE",
                    http_status=411,
                ) from exc
            if not 1 <= content_length <= self.max_body_bytes:
                raise GitHubWebhookExternalEventTransportError(
                    "provider webhook body is empty or exceeds the configured limit",
                    code="INVALID_BODY_SIZE",
                    http_status=413,
                )
            stream = environ.get("wsgi.input")
            if stream is None or not hasattr(stream, "read"):
                raise GitHubWebhookExternalEventTransportError(
                    "provider webhook body stream is unavailable",
                    code="INVALID_BODY_SIZE",
                    http_status=400,
                )
            body = stream.read(content_length + 1)
            if not isinstance(body, bytes) or len(body) != content_length:
                raise GitHubWebhookExternalEventTransportError(
                    "provider webhook body length does not match Content-Length",
                    code="INVALID_BODY_SIZE",
                    http_status=400,
                )
            headers = {
                "content-type": environ.get("CONTENT_TYPE", ""),
                "x-github-event": environ.get("HTTP_X_GITHUB_EVENT", ""),
                "x-github-delivery": environ.get("HTTP_X_GITHUB_DELIVERY", ""),
                "x-hub-signature-256": environ.get(
                    "HTTP_X_HUB_SIGNATURE_256", ""
                ),
            }
            result = self.receive(
                session_id=session_id,
                headers=headers,
                body=body,
            )
            http_status = "200 OK" if result.get("status") == "PASS" else "409 Conflict"
            return self._json_response(
                start_response,
                status=http_status,
                payload=result,
            )
        except GitHubWebhookExternalEventTransportError as exc:
            status_names = {
                400: "Bad Request",
                401: "Unauthorized",
                403: "Forbidden",
                404: "Not Found",
                405: "Method Not Allowed",
                409: "Conflict",
                411: "Length Required",
                413: "Payload Too Large",
                415: "Unsupported Media Type",
                500: "Internal Server Error",
                503: "Service Unavailable",
            }
            return self._json_response(
                start_response,
                status=f"{exc.http_status} {status_names.get(exc.http_status, 'Error')}",
                payload={
                    "schema": GITHUB_WEBHOOK_RESULT_SCHEMA,
                    "status": "BLOCKED",
                    "error_code": exc.code,
                    "error": str(exc),
                    "authority_effect": False,
                    "completion_authority_changed": False,
                    "merge_authority_changed": False,
                },
            )


__all__ = [
    "GITHUB_WEBHOOK_EVIDENCE_SCHEMA",
    "GITHUB_WEBHOOK_PATH_PREFIX",
    "GITHUB_WEBHOOK_RECEIPT_SCHEMA",
    "GITHUB_WEBHOOK_RESULT_SCHEMA",
    "GitHubWebhookExternalEventTransportError",
    "GitHubWorkflowRunWebhookTransport",
]
