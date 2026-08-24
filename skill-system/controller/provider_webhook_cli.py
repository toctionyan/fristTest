from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from wsgiref.simple_server import make_server

from concrete_host_bootstrap import ConcreteHostBootstrapError, build_orchestrator
from github_webhook_external_event_transport import (
    GITHUB_WEBHOOK_RESULT_SCHEMA,
    GitHubWebhookExternalEventTransportError,
    GitHubWorkflowRunWebhookTransport,
)


MAX_HEADER_BYTES = 16 * 1024


class ProviderWebhookCliError(RuntimeError):
    """Raised when the provider webhook process boundary is invalid."""


def _read_headers(path: str) -> dict[str, str]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ProviderWebhookCliError("provider webhook headers file is missing or unsafe")
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ProviderWebhookCliError("provider webhook headers could not be read") from exc
    if not raw or len(raw) > MAX_HEADER_BYTES:
        raise ProviderWebhookCliError("provider webhook headers exceed the size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderWebhookCliError("provider webhook headers are not valid JSON") from exc
    if not isinstance(payload, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in payload.items()
    ):
        raise ProviderWebhookCliError("provider webhook headers must be a string map")
    return dict(payload)


def _read_body(path: str, *, limit: int) -> bytes:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ProviderWebhookCliError("provider webhook body file is missing or unsafe")
    try:
        with source.open("rb") as handle:
            body = handle.read(limit + 1)
    except OSError as exc:
        raise ProviderWebhookCliError("provider webhook body could not be read") from exc
    if not body or len(body) > limit:
        raise ProviderWebhookCliError("provider webhook body exceeds the size limit")
    return body


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillctl provider-webhook",
        description=(
            "Authenticate GitHub workflow_run webhooks and deliver them through "
            "the existing exact external-event Scheduler."
        ),
    )
    parser.add_argument("--host-id", choices=("chatgpt", "codex"), required=True)
    commands = parser.add_subparsers(dest="provider_webhook_command", required=True)
    receive = commands.add_parser(
        "receive", help="process one raw signed GitHub webhook request"
    )
    receive.add_argument("--session-id", required=True)
    receive.add_argument("--headers", required=True, help="JSON string-map file")
    receive.add_argument("--body", required=True, help="exact raw request body file")
    serve = commands.add_parser(
        "serve", help="serve the event-driven WSGI webhook endpoint"
    )
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    return parser


def _transport(host_id: str) -> tuple[Any, GitHubWorkflowRunWebhookTransport]:
    orchestrator = build_orchestrator(host_id=host_id)
    transport = getattr(orchestrator, "_concrete_provider_webhook_transport", None)
    if not isinstance(transport, GitHubWorkflowRunWebhookTransport):
        connection = getattr(orchestrator, "_concrete_bootstrap_connection", None)
        if connection is not None:
            connection.close()
        raise ProviderWebhookCliError(
            "Concrete Host factory did not inject a provider webhook transport"
        )
    return orchestrator, transport


def _blocked(error: Exception) -> dict[str, Any]:
    return {
        "schema": GITHUB_WEBHOOK_RESULT_SCHEMA,
        "status": "BLOCKED",
        "error_code": (
            error.code
            if isinstance(error, GitHubWebhookExternalEventTransportError)
            else "PROVIDER_WEBHOOK_PROCESS_ERROR"
        ),
        "error": str(error),
        "authority_effect": False,
        "completion_authority_changed": False,
        "merge_authority_changed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    orchestrator: Any | None = None
    serving = False
    try:
        orchestrator, transport = _transport(args.host_id)
        if args.provider_webhook_command == "receive":
            result = transport.receive(
                session_id=args.session_id,
                headers=_read_headers(args.headers),
                body=_read_body(args.body, limit=transport.max_body_bytes),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result.get("status") == "PASS" else 2
        if not 1 <= args.port <= 65535:
            raise ProviderWebhookCliError("--port must be 1..65535")
        if args.bind not in {"127.0.0.1", "::1", "localhost"}:
            raise ProviderWebhookCliError(
                "built-in webhook server must bind to loopback behind an HTTPS reverse proxy"
            )
        serving = True
        with make_server(args.bind, args.port, transport) as server:
            print(
                json.dumps(
                    {
                        "schema": "provider-webhook-server@1",
                        "status": "LISTENING",
                        "bind": args.bind,
                        "port": args.port,
                        "path": "/v1/github/workflow-run/<session-id>",
                        "provider_polling": False,
                        "authority_effect": False,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            server.serve_forever()
        return 0
    except (
        ProviderWebhookCliError,
        GitHubWebhookExternalEventTransportError,
        ConcreteHostBootstrapError,
        OSError,
    ) as exc:
        print(
            json.dumps(_blocked(exc), ensure_ascii=False, indent=2, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        error = ProviderWebhookCliError(
            "provider webhook processing failed; inspect durable provider and Scheduler state"
        )
        print(
            json.dumps(_blocked(error), ensure_ascii=False, indent=2, sort_keys=True),
            file=sys.stderr,
        )
        return 3
    finally:
        if orchestrator is not None and not serving:
            connection = getattr(orchestrator, "_concrete_bootstrap_connection", None)
            if connection is not None:
                connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
