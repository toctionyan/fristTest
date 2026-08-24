from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from concrete_host_bootstrap import (
    ConcreteHostBootstrapError,
    build_orchestrator,
)
from durable_external_event_scheduler import (
    DurableExternalEventScheduler,
    DurableExternalEventSchedulerError,
    validate_ingest_request,
)


MAX_REQUEST_BYTES = 1024 * 1024


class SchedulerCliError(RuntimeError):
    """Raised for bounded scheduler process input or bootstrap failures."""


def _read_request(path: str) -> dict[str, Any]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise SchedulerCliError("scheduler request file is missing or unsafe")
    try:
        with source.open("r", encoding="utf-8") as handle:
            raw = handle.read(MAX_REQUEST_BYTES + 1)
    except OSError as exc:
        raise SchedulerCliError("scheduler request could not be read") from exc
    if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise SchedulerCliError("scheduler request exceeds the size limit")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchedulerCliError("scheduler request is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise SchedulerCliError("scheduler request must be an object")
    return dict(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillctl scheduler",
        description=(
            "Persist and deliver exact external events through the existing Starter Host transport."
        ),
    )
    parser.add_argument("--host-id", choices=("chatgpt", "codex"), required=True)
    commands = parser.add_subparsers(dest="scheduler_command", required=True)
    ingest = commands.add_parser("ingest", help="persist one exact wait-bound event")
    ingest.add_argument("--request", required=True)
    wake = commands.add_parser("wake", help="deliver one persisted event exactly once")
    wake.add_argument("--event-ref", required=True)
    commands.add_parser(
        "run-once", help="process one bounded inbox snapshot without Provider polling"
    )
    return parser


def _scheduler(host_id: str) -> tuple[Any, DurableExternalEventScheduler]:
    orchestrator = build_orchestrator(host_id=host_id)
    scheduler = getattr(orchestrator, "_concrete_wakeup_scheduler", None)
    if not isinstance(scheduler, DurableExternalEventScheduler):
        connection = getattr(orchestrator, "_concrete_bootstrap_connection", None)
        if connection is not None:
            connection.close()
        raise SchedulerCliError("Concrete Host factory did not inject a scheduler")
    return orchestrator, scheduler


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    orchestrator: Any | None = None
    try:
        orchestrator, scheduler = _scheduler(args.host_id)
        if args.scheduler_command == "ingest":
            request = validate_ingest_request(_read_request(args.request))
            if request["host_id"] != args.host_id:
                raise SchedulerCliError(
                    "scheduler request Host does not match --host-id"
                )
            result = scheduler.ingest(
                session_id=request["session_id"], event=request["event"]
            )
        elif args.scheduler_command == "wake":
            result = scheduler.wake(event_ref=args.event_ref)
        else:
            result = scheduler.run_once()
    except (
        SchedulerCliError,
        DurableExternalEventSchedulerError,
        ConcreteHostBootstrapError,
        OSError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema": "external-wakeup-cli-result@1",
                    "status": "BLOCKED",
                    "error": str(exc),
                    "authority_effect": False,
                    "completion_authority_changed": False,
                    "merge_authority_changed": False,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "schema": "external-wakeup-cli-result@1",
                    "status": "BLOCKED",
                    "error": "Scheduler orchestration rejected the event; inspect durable Host and wakeup state",
                    "authority_effect": False,
                    "completion_authority_changed": False,
                    "merge_authority_changed": False,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3
    finally:
        if orchestrator is not None:
            connection = getattr(orchestrator, "_concrete_bootstrap_connection", None)
            if connection is not None:
                connection.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
