#!/usr/bin/env python3
"""Package explicit Stage2B1 acceptance inputs for the governed workflow.

This command is a transport adapter only. It validates the package shape and
copies caller-supplied JSON objects into a new fixed-name directory. It does
not validate acceptance, select evidence, mutate a TaskRun, or write governance
state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


INPUT_PACKAGE_SCHEMA = "stage2b1-acceptance-inputs@2"
COMMAND_SCHEMA = "stage2b1-acceptance-inputs-command@1"
INPUT_FILES = (
    "change-contract.json",
    "decision.json",
    "expected-binding.json",
    "human-decision.json",
    "human-gate.json",
    "task-run.json",
)
ARTIFACT_NAMES = {
    "task-run.json": "stage2b1-acceptance-task-run",
    "decision.json": "stage2b1-acceptance-decision",
    "expected-binding.json": "stage2b1-acceptance-expected-binding",
    "change-contract.json": "stage2b1-acceptance-change-contract",
    "human-gate.json": "stage2b1-acceptance-human-gate",
    "human-decision.json": "stage2b1-acceptance-human-decision",
}
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PROVENANCE_FIELDS = frozenset(
    {
        "repository",
        "workflow_id",
        "workflow_path",
        "event",
        "ref",
        "head_sha",
        "run_id",
        "run_attempt",
    }
)
_ARTIFACT_PROVENANCE_FIELDS = frozenset(
    {"id", "name", "digest", "archive_digest", "content_digest", "source_run_id", "source_run_attempt"}
)


class Stage2B1AcceptanceInputsError(ValueError):
    """Raised when an explicit acceptance input cannot be packaged safely."""


def _sha256_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _require_exact_object(value: object, fields: frozenset[str], *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Stage2B1AcceptanceInputsError(f"{field} must be a JSON object")
    actual = set(value)
    if actual != set(fields):
        missing = sorted(set(fields) - actual)
        unknown = sorted(actual - set(fields))
        detail = ",".join([f"missing:{name}" for name in missing] + [f"unknown:{name}" for name in unknown])
        raise Stage2B1AcceptanceInputsError(f"{field} has invalid fields:{detail}")
    return dict(value)


def _validate_provenance(value: object, *, field: str) -> dict[str, Any]:
    payload = _require_exact_object(value, _PROVENANCE_FIELDS, field=field)
    for name in ("repository", "workflow_path", "event", "ref"):
        if not isinstance(payload[name], str) or not payload[name].strip():
            raise Stage2B1AcceptanceInputsError(f"{field}.{name} is invalid")
    if not isinstance(payload["workflow_id"], int) or isinstance(payload["workflow_id"], bool) or payload["workflow_id"] < 1:
        raise Stage2B1AcceptanceInputsError(f"{field}.workflow_id is invalid")
    if not isinstance(payload["run_id"], int) or isinstance(payload["run_id"], bool) or payload["run_id"] < 1:
        raise Stage2B1AcceptanceInputsError(f"{field}.run_id is invalid")
    if not isinstance(payload["run_attempt"], int) or isinstance(payload["run_attempt"], bool) or payload["run_attempt"] < 1:
        raise Stage2B1AcceptanceInputsError(f"{field}.run_attempt is invalid")
    if not isinstance(payload["head_sha"], str) or _SHA1.fullmatch(payload["head_sha"]) is None:
        raise Stage2B1AcceptanceInputsError(f"{field}.head_sha is invalid")
    return payload


def _validate_artifact_provenance(
    value: object,
    *,
    filename: str,
    contents: bytes,
    source_run_id: int,
    source_run_attempt: int,
) -> dict[str, Any]:
    payload = _require_exact_object(value, _ARTIFACT_PROVENANCE_FIELDS, field=f"artifacts.{filename}")
    if not isinstance(payload["id"], str) or not payload["id"].isdigit() or int(payload["id"]) < 1:
        raise Stage2B1AcceptanceInputsError(f"artifacts.{filename}.id is invalid")
    if payload["name"] != ARTIFACT_NAMES[filename]:
        raise Stage2B1AcceptanceInputsError(f"artifacts.{filename}.name is invalid")
    for name in ("digest", "archive_digest", "content_digest"):
        if not isinstance(payload[name], str) or _SHA256.fullmatch(payload[name]) is None:
            raise Stage2B1AcceptanceInputsError(f"artifacts.{filename}.{name} is invalid")
    if payload["digest"] != payload["archive_digest"]:
        raise Stage2B1AcceptanceInputsError(f"artifacts.{filename}.archive_digest_mismatch")
    if payload["content_digest"] != _sha256_digest(contents):
        raise Stage2B1AcceptanceInputsError(f"artifacts.{filename}.content_digest_mismatch")
    if payload["source_run_id"] != source_run_id or payload["source_run_attempt"] != source_run_attempt:
        raise Stage2B1AcceptanceInputsError(f"artifacts.{filename}.source_run_mismatch")
    return payload


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
    source_provenance: Mapping[str, Any],
    artifact_provenance: Mapping[str, Any],
    producer_provenance: Mapping[str, Any],
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
    source = _validate_provenance(source_provenance, field="source")
    producer = _validate_provenance(producer_provenance, field="producer")
    if source["run_id"] != run_id or source["run_attempt"] != run_attempt:
        raise Stage2B1AcceptanceInputsError("source provenance does not match source run arguments")
    if producer["repository"] != source["repository"]:
        raise Stage2B1AcceptanceInputsError("producer and source repositories differ")
    if not isinstance(artifact_provenance, Mapping):
        raise Stage2B1AcceptanceInputsError("artifacts must be a JSON object")
    if set(artifact_provenance) != set(INPUT_FILES):
        raise Stage2B1AcceptanceInputsError("artifacts must contain exactly the input files")
    artifacts = {
        filename: _validate_artifact_provenance(
            artifact_provenance[filename],
            filename=filename,
            contents=contents[filename],
            source_run_id=run_id,
            source_run_attempt=run_attempt,
        )
        for filename in INPUT_FILES
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
        "source": source,
        "producer": producer,
        "artifacts": artifacts,
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
        "source": source,
        "producer": producer,
        "artifacts": artifacts,
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
    parser.add_argument("--source-provenance", required=True, type=Path)
    parser.add_argument("--artifact-provenance", required=True, type=Path)
    parser.add_argument("--producer-provenance", required=True, type=Path)
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
            source_provenance=json.loads(args.source_provenance.read_text(encoding="utf-8")),
            artifact_provenance=json.loads(args.artifact_provenance.read_text(encoding="utf-8")),
            producer_provenance=json.loads(args.producer_provenance.read_text(encoding="utf-8")),
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
