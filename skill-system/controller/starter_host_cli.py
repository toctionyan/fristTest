from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from starter_host_orchestrator import StarterHostOrchestrator
from starter_host_transport import (
    StarterHostCommandTransport,
    StarterHostTransportError,
    failure_response,
    validate_host_command,
)


MAX_COMMAND_BYTES = 1024 * 1024
_FACTORY = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)


class StarterHostCliError(RuntimeError):
    """Raised for bounded CLI/bootstrap failures."""


def load_trusted_factory(spec: str) -> Callable[..., Any]:
    value = str(spec or "").strip()
    if not _FACTORY.fullmatch(value):
        raise StarterHostCliError(
            "trusted factory must use a dotted module and callable name"
        )
    module_name, callable_name = value.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, callable_name)
    except (ImportError, AttributeError) as exc:
        raise StarterHostCliError("trusted Host factory could not be loaded") from exc
    if not callable(factory):
        raise StarterHostCliError("trusted Host factory is not callable")
    return factory


def _read_command(source: str) -> Mapping[str, Any]:
    try:
        if source == "-":
            raw = sys.stdin.read(MAX_COMMAND_BYTES + 1)
        else:
            path = Path(source)
            if path.is_symlink() or not path.is_file():
                raise StarterHostCliError("Host command file is missing or unsafe")
            with path.open("r", encoding="utf-8") as handle:
                raw = handle.read(MAX_COMMAND_BYTES + 1)
    except OSError as exc:
        raise StarterHostCliError("Host command could not be read") from exc
    if len(raw.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise StarterHostCliError("Host command exceeds the transport size limit")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StarterHostCliError("Host command is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise StarterHostCliError("Host command must be a JSON object")
    return payload


def _write_response(payload: Mapping[str, Any], *, pretty: bool) -> None:
    print(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            indent=2 if pretty else None,
            sort_keys=True,
            separators=None if pretty else (",", ":"),
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillctl host",
        description="Dispatch one closed JSON command to StarterHostOrchestrator.",
    )
    parser.add_argument(
        "--factory",
        default=os.environ.get("HARNESS_HOST_FACTORY", ""),
        help="trusted operator-selected module:callable Orchestrator factory",
    )
    parser.add_argument(
        "--request",
        default="-",
        help="JSON request file, or - for stdin (default)",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    factory_loader: Callable[[str], Callable[..., Any]] = load_trusted_factory,
) -> int:
    args = _parser().parse_args(argv)
    raw: object = {}
    try:
        raw = _read_command(args.request)
        command = validate_host_command(raw)
        host_id = command["host_id"]
        factory = factory_loader(args.factory)
        # A factory receives only the validated Host identity. The
        # command cannot select a module, path, callable, or arbitrary code.
        with contextlib.redirect_stdout(io.StringIO()):
            orchestrator = factory(host_id=host_id)
            if not isinstance(orchestrator, StarterHostOrchestrator):
                raise StarterHostCliError(
                    "trusted Host factory must return StarterHostOrchestrator"
                )
            response = StarterHostCommandTransport(orchestrator).execute(command)
    except StarterHostTransportError:
        response = failure_response(
            raw,
            code="INVALID_HOST_COMMAND",
            message="Host command validation or dispatch contract failed",
        )
        _write_response(response, pretty=args.pretty)
        return 2
    except StarterHostCliError:
        response = failure_response(
            raw,
            code="HOST_CLI_CONFIGURATION",
            message="Trusted Host CLI configuration is unavailable",
        )
        _write_response(response, pretty=args.pretty)
        return 2
    except Exception:
        response = failure_response(
            raw,
            code="HOST_ORCHESTRATION_BLOCKED",
            message="Host orchestration rejected the command; read the durable session before retrying",
        )
        _write_response(response, pretty=args.pretty)
        return 3
    _write_response(response, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
