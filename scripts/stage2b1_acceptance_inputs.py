#!/usr/bin/env python3
"""Package explicit Stage2B1 acceptance inputs for the governed workflow.

This command is a transport adapter only. It validates the package shape and
copies caller-supplied JSON objects into a new fixed-name directory. It does
not validate acceptance, select evidence, mutate a TaskRun, or write governance
state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


INPUT_PACKAGE_SCHEMA = "stage2b1-acceptance-inputs@1"
COMMAND_SCHEMA = "stage2b1-acceptance-inputs-command@1"
INPUT_FILES = (
    "change-contract.json",
    "decision.json",
    "expected-binding.json",
    "human-decision.json",
    "human-gate.json",
    "task-run.json",
)


class Stage2B1AcceptanceInputsError(ValueError):
    """Raised when an explicit acceptance input cannot be packaged safely."""


def _positive_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise Stage2B1AcceptanceInputsError(f"{field} must be a positive integer")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise Stage2B1AcceptanceInputsError(f"{field} must be a positive integer") from exc
    if parsed < 1 or str(parsed) != str(value).strip():
        raise Stage2B1AcceptanceInputsError(f"{field} must be a positive integer")
    return parsed


def _read_json_object(path: Path, *, field: str) -> bytes:
    path = Path(path)
    if path.is_symlink():
        raise Stage2B1AcceptanceInputsError(f"{field} is missing or unsafe")
    resolved = path.resolve()
    if resolved.is_symlink() or not resolved.is_file():
        raise Stage2B1AcceptanceInputsError(f"{field} is missing or unsafe")
    try:
        raw = resolved.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Stage2B1AcceptanceInputsError(f"{field} is unreadable") from exc
    if not isinstance(payload, dict):
        raise Stage2B1AcceptanceInputsError(f"{field} must be a JSON object")
    return raw


def _prepare_output(path: Path) -> Path:
    path = Path(path)
    if path.is_symlink():
        raise Stage2B1AcceptanceInputsError("output_dir is missing or unsafe")
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise Stage2B1AcceptanceInputsError("output_dir must be a new empty directory")
    else:
        try:
            path.mkdir(parents=True)
        except OSError as exc:
            raise Stage2B1AcceptanceInputsError("output_dir cannot be created") from exc
    return path


def package_stage2b1_acceptance_inputs(
    *,
    source_run_id: object,
    source_run_attempt: object,
    task_run: Path,
    decision: Path,
    expected_binding: Path,
    change_contract: Path,
    human_gate: Path,
    human_decision: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create one exact input package from caller-supplied files."""

    run_id = _positive_integer(source_run_id, field="source_run_id")
    run_attempt = _positive_integer(source_run_attempt, field="source_run_attempt")
    sources = {
        "task-run.json": task_run,
        "decision.json": decision,
        "expected-binding.json": expected_binding,
        "change-contract.json": change_contract,
        "human-gate.json": human_gate,
        "human-decision.json": human_decision,
    }
    contents = {
        filename: _read_json_object(path, field=filename)
        for filename, path in sources.items()
    }
    destination = _prepare_output(output_dir)
    for filename in INPUT_FILES:
        try:
            (destination / filename).write_bytes(contents[filename])
        except OSError as exc:
            raise Stage2B1AcceptanceInputsError(f"cannot write {filename}") from exc

    manifest = {
        "schema": INPUT_PACKAGE_SCHEMA,
        "stage_id": "stage2b1",
        "source_run_id": run_id,
        "source_run_attempt": run_attempt,
        "files": list(INPUT_FILES),
    }
    try:
        (destination / "manifest.json").write_bytes(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (OSError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise Stage2B1AcceptanceInputsError("cannot write manifest.json") from exc
    return {
        "schema": COMMAND_SCHEMA,
        "status": "PACKAGED",
        "stage_id": "stage2b1",
        "source_run_id": run_id,
        "source_run_attempt": run_attempt,
        "output_dir": str(destination),
        "files": ["manifest.json", *INPUT_FILES],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package explicit Stage2B1 acceptance workflow inputs."
    )
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--source-run-attempt", required=True)
    parser.add_argument("--task-run", required=True, type=Path)
    parser.add_argument("--decision", required=True, type=Path)
    parser.add_argument("--expected-binding", required=True, type=Path)
    parser.add_argument("--change-contract", required=True, type=Path)
    parser.add_argument("--human-gate", required=True, type=Path)
    parser.add_argument("--human-decision", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = package_stage2b1_acceptance_inputs(
            source_run_id=args.source_run_id,
            source_run_attempt=args.source_run_attempt,
            task_run=args.task_run,
            decision=args.decision,
            expected_binding=args.expected_binding,
            change_contract=args.change_contract,
            human_gate=args.human_gate,
            human_decision=args.human_decision,
            output_dir=args.output_dir,
        )
    except (OSError, Stage2B1AcceptanceInputsError) as exc:
        print(
            json.dumps(
                {"schema": COMMAND_SCHEMA, "status": "BLOCKED", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
