from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from github_webhook_external_event_transport import (  # type: ignore  # noqa: E402
    GITHUB_WEBHOOK_PATH_PREFIX,
    GitHubWebhookExternalEventTransportError,
    GitHubWorkflowRunWebhookTransport,
)
import provider_webhook_cli  # type: ignore  # noqa: E402


SECRET = b"0123456789abcdef0123456789abcdef"


class FakeScheduler:
    def __init__(self, *, wake_status: str = "DELIVERED") -> None:
        self.wake_status = wake_status
        self.ingest_calls: list[dict[str, object]] = []
        self.wake_calls: list[str] = []
        self._mutex = threading.Lock()

    def ingest(self, *, session_id: str, event: dict[str, object]) -> dict[str, object]:
        with self._mutex:
            self.ingest_calls.append({"session_id": session_id, "event": event})
        return {
            "schema": "external-wakeup-result@1",
            "status": "QUEUED",
            "event_ref": "file:.harness/runtime/external-events/event.json",
        }

    def wake(self, *, event_ref: str) -> dict[str, object]:
        with self._mutex:
            self.wake_calls.append(event_ref)
        return {
            "schema": "external-wakeup-result@1",
            "status": self.wake_status,
            "event_ref": event_ref,
            "receipt_ref": "file:.harness/runtime/external-wakeup-receipts/event/receipt.json",
        }


def workflow_body(
    *,
    repository: str = "owner/repository",
    run_id: int = 32690968525,
    action: str = "completed",
    status: str = "completed",
    conclusion: str = "success",
    head_sha: str = "a" * 40,
) -> bytes:
    return json.dumps(
        {
            "action": action,
            "repository": {"full_name": repository},
            "workflow_run": {
                "id": run_id,
                "run_attempt": 2,
                "status": status,
                "conclusion": conclusion,
                "head_sha": head_sha,
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def signed_headers(body: bytes, *, delivery: str = "delivery-1") -> dict[str, str]:
    signature = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": "workflow_run",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": f"sha256={signature}",
    }


class ProviderWebhookExternalEventTransportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="provider-webhook-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.scheduler = FakeScheduler()
        self.transport = GitHubWorkflowRunWebhookTransport(
            workspace=self.root,
            scheduler=self.scheduler,
            host_id="codex",
            repository_full_name="owner/repository",
            secret_environment_variable="PRIVATE_WEBHOOK_SECRET",
            secret=SECRET,
        )

    def test_valid_signed_workflow_run_persists_evidence_and_delivers_once(self) -> None:
        body = workflow_body()
        result = self.transport.receive(
            session_id="session-17",
            headers=signed_headers(body),
            body=body,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["scheduler_status"], "DELIVERED")
        self.assertEqual(len(self.scheduler.ingest_calls), 1)
        self.assertEqual(len(self.scheduler.wake_calls), 1)
        submitted = self.scheduler.ingest_calls[0]
        self.assertEqual(submitted["session_id"], "session-17")
        event = submitted["event"]
        self.assertEqual(event["provider"], "github.actions")
        self.assertEqual(event["correlation_ref"], "run-32690968525")
        self.assertEqual(event["event"], "ci.completed")
        self.assertEqual(event["conclusion"], "success")
        self.assertEqual(event["head_sha"], "a" * 40)
        self.assertEqual(len(event["evidence_refs"]), 2)

        evidence_ref = str(result["provider_evidence_ref"])
        evidence = json.loads(
            (self.root / evidence_ref.removeprefix("file:")).read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(evidence["signature_verified"])
        self.assertEqual(evidence["payload_sha256"], hashlib.sha256(body).hexdigest())
        self.assertFalse(evidence["authority_effect"])

        replay = self.transport.receive(
            session_id="session-17",
            headers=signed_headers(body),
            body=body,
        )
        self.assertEqual(replay, result)
        self.assertEqual(len(self.scheduler.ingest_calls), 1)
        self.assertEqual(len(self.scheduler.wake_calls), 1)
        durable_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in self.root.rglob("*.json")
        )
        self.assertNotIn(SECRET.decode("utf-8"), durable_text)

    def test_conflicting_delivery_replay_and_route_fail_closed(self) -> None:
        original = workflow_body()
        self.transport.receive(
            session_id="session-17",
            headers=signed_headers(original),
            body=original,
        )
        changed = workflow_body(conclusion="failure")
        with self.assertRaisesRegex(
            GitHubWebhookExternalEventTransportError, "different bytes or route"
        ):
            self.transport.receive(
                session_id="session-17",
                headers=signed_headers(changed),
                body=changed,
            )
        with self.assertRaisesRegex(
            GitHubWebhookExternalEventTransportError, "different bytes or route"
        ):
            self.transport.receive(
                session_id="session-18",
                headers=signed_headers(original),
                body=original,
            )
        self.assertEqual(len(self.scheduler.ingest_calls), 1)

    def test_concurrent_duplicate_delivery_invokes_scheduler_once(self) -> None:
        body = workflow_body()

        def deliver() -> dict[str, object]:
            return self.transport.receive(
                session_id="session-17",
                headers=signed_headers(body),
                body=body,
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _index: deliver(), range(8)))
        self.assertTrue(all(row == results[0] for row in results))
        self.assertEqual(len(self.scheduler.ingest_calls), 1)
        self.assertEqual(len(self.scheduler.wake_calls), 1)

    def test_recomputed_plain_digest_cannot_authorize_tampered_evidence(self) -> None:
        body = workflow_body()
        result = self.transport.receive(
            session_id="session-17",
            headers=signed_headers(body),
            body=body,
        )
        evidence_path = self.root / str(result["provider_evidence_ref"]).removeprefix(
            "file:"
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["conclusion"] = "failure"
        unsigned = dict(evidence)
        unsigned.pop("evidence_sha256")
        evidence["evidence_sha256"] = hashlib.sha256(
            json.dumps(
                unsigned, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            GitHubWebhookExternalEventTransportError,
            "does not match its authenticated body",
        ):
            self.transport.receive(
                session_id="session-17",
                headers=signed_headers(body),
                body=body,
            )
        self.assertEqual(len(self.scheduler.ingest_calls), 1)

    def test_signature_headers_payload_repository_and_size_are_rejected(self) -> None:
        body = workflow_body()
        cases: list[tuple[str, dict[str, str], bytes]] = []
        missing_signature = signed_headers(body)
        missing_signature.pop("X-Hub-Signature-256")
        cases.append(("Signature", missing_signature, body))
        wrong_signature = signed_headers(body)
        wrong_signature["X-Hub-Signature-256"] = "sha256=" + "0" * 64
        cases.append(("signature verification", wrong_signature, body))
        wrong_event = signed_headers(body)
        wrong_event["X-GitHub-Event"] = "push"
        cases.append(("workflow_run", wrong_event, body))
        wrong_type = signed_headers(body)
        wrong_type["Content-Type"] = "text/plain"
        cases.append(("Content-Type", wrong_type, body))
        wrong_repository = workflow_body(repository="other/repository")
        cases.append(("repository", signed_headers(wrong_repository), wrong_repository))
        wrong_action = workflow_body(action="requested")
        cases.append(("action", signed_headers(wrong_action), wrong_action))
        wrong_status = workflow_body(status="in_progress")
        cases.append(("status", signed_headers(wrong_status), wrong_status))
        wrong_sha = workflow_body(head_sha="short")
        cases.append(("head_sha", signed_headers(wrong_sha), wrong_sha))

        for expected, headers, raw in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(
                    GitHubWebhookExternalEventTransportError, expected
                ):
                    self.transport.receive(
                        session_id="session-17", headers=headers, body=raw
                    )
        self.assertEqual(self.scheduler.ingest_calls, [])

        small = GitHubWorkflowRunWebhookTransport(
            workspace=self.root,
            scheduler=self.scheduler,
            host_id="codex",
            repository_full_name="owner/repository",
            secret=SECRET,
            max_body_bytes=10,
        )
        with self.assertRaisesRegex(
            GitHubWebhookExternalEventTransportError, "configured limit"
        ):
            small.receive(
                session_id="session-17",
                headers=signed_headers(body),
                body=body,
            )

    def test_missing_secret_blocks_without_persisting_request(self) -> None:
        transport = GitHubWorkflowRunWebhookTransport(
            workspace=self.root,
            scheduler=self.scheduler,
            host_id="codex",
            repository_full_name="owner/repository",
            secret_environment_variable="MISSING_WEBHOOK_SECRET",
        )
        body = workflow_body()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                GitHubWebhookExternalEventTransportError, "MISSING_WEBHOOK_SECRET"
            ):
                transport.receive(
                    session_id="session-17",
                    headers=signed_headers(body),
                    body=body,
                )
        self.assertFalse(any(self.root.rglob("*.json")))

    def test_scheduler_terminal_rejection_is_not_converted_to_success(self) -> None:
        scheduler = FakeScheduler(wake_status="REJECTED_STALE")
        transport = GitHubWorkflowRunWebhookTransport(
            workspace=self.root,
            scheduler=scheduler,
            host_id="codex",
            repository_full_name="owner/repository",
            secret=SECRET,
        )
        body = workflow_body()
        result = transport.receive(
            session_id="session-17",
            headers=signed_headers(body),
            body=body,
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["scheduler_status"], "REJECTED_STALE")
        replay = transport.receive(
            session_id="session-17",
            headers=signed_headers(body),
            body=body,
        )
        self.assertEqual(replay, result)
        self.assertEqual(len(scheduler.ingest_calls), 1)

    def test_wsgi_listener_uses_exact_session_route_and_safe_statuses(self) -> None:
        body = workflow_body()
        headers = signed_headers(body)
        captured: dict[str, object] = {}

        def start_response(status: str, response_headers: object) -> None:
            captured["status"] = status
            captured["headers"] = response_headers

        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": GITHUB_WEBHOOK_PATH_PREFIX + "session-17",
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": headers["Content-Type"],
            "HTTP_X_GITHUB_EVENT": headers["X-GitHub-Event"],
            "HTTP_X_GITHUB_DELIVERY": headers["X-GitHub-Delivery"],
            "HTTP_X_HUB_SIGNATURE_256": headers["X-Hub-Signature-256"],
            "wsgi.input": io.BytesIO(body),
        }
        response = json.loads(b"".join(self.transport(environ, start_response)))
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(response["session_id"], "session-17")

        captured.clear()
        blocked = dict(environ)
        blocked["REQUEST_METHOD"] = "GET"
        response = json.loads(b"".join(self.transport(blocked, start_response)))
        self.assertEqual(captured["status"], "405 Method Not Allowed")
        self.assertEqual(response["status"], "BLOCKED")
        self.assertFalse(response["authority_effect"])

    def test_root_receive_cli_delegates_to_same_transport(self) -> None:
        body = workflow_body()
        headers_path = self.root / "headers.json"
        body_path = self.root / "body.json"
        headers_path.write_text(
            json.dumps(signed_headers(body)), encoding="utf-8"
        )
        body_path.write_bytes(body)
        orchestrator = mock.Mock()
        orchestrator._concrete_bootstrap_connection = mock.Mock()
        output = io.StringIO()
        with mock.patch.object(
            provider_webhook_cli,
            "_transport",
            return_value=(orchestrator, self.transport),
        ), redirect_stdout(output):
            status = provider_webhook_cli.main(
                [
                    "--host-id",
                    "codex",
                    "receive",
                    "--session-id",
                    "session-17",
                    "--headers",
                    str(headers_path),
                    "--body",
                    str(body_path),
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "PASS")
        orchestrator._concrete_bootstrap_connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
