#!/usr/bin/env python3
"""Fail-closed admission contract for the protected production release workflow.

This contract runs before the secret-bearing protected release job.  Its purpose
is not to certify the product; it makes invalid workflow dispatches fail visibly
instead of allowing the only release job to be silently skipped.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

CONTRACT = "release-workflow-admission@1"
_ALLOWED_PROVIDERS = frozenset({"openai", "deepseek"})
_SAFE_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")


class ReleaseAdmissionError(RuntimeError):
    def __init__(self, code: str, detail: str, *, environment_blocked: bool = False) -> None:
        super().__init__(detail)
        self.code = str(code)
        self.detail = str(detail)
        self.environment_blocked = bool(environment_blocked)


def _required(source: Mapping[str, str], name: str, *, environment_blocked: bool = True) -> str:
    value = str(source.get(name) or "").strip()
    if not value:
        raise ReleaseAdmissionError(
            f"{name.lower()}_missing",
            f"{name} is required for protected release admission",
            environment_blocked=environment_blocked,
        )
    return value


def _safe_input(source: Mapping[str, str], name: str) -> str:
    value = _required(source, name, environment_blocked=False)
    if not _SAFE_TEXT_RE.fullmatch(value):
        raise ReleaseAdmissionError(
            f"{name.lower()}_invalid",
            f"{name} must be a single printable value no longer than 160 characters",
        )
    return value


def validate_release_admission(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    if str(source.get("GITHUB_ACTIONS") or "").strip().lower() != "true" or str(
        source.get("CI") or ""
    ).strip().lower() != "true":
        raise ReleaseAdmissionError(
            "release_admission_ci_context_missing",
            "release admission must run inside GitHub Actions CI",
            environment_blocked=True,
        )

    expected_event = str(source.get("PRODUCTION_RELEASE_EXPECTED_EVENT") or "workflow_dispatch").strip()
    expected_ref = _required(source, "PRODUCTION_RELEASE_EXPECTED_REF")
    expected_workflow = str(
        source.get("PRODUCTION_RELEASE_EXPECTED_WORKFLOW") or "production-certification-release"
    ).strip()

    event_name = _required(source, "GITHUB_EVENT_NAME")
    git_ref = _required(source, "GITHUB_REF")
    ref_type = _required(source, "GITHUB_REF_TYPE")
    ref_protected = str(source.get("GITHUB_REF_PROTECTED") or "").strip().lower()
    workflow = _required(source, "GITHUB_WORKFLOW")

    if event_name != expected_event:
        raise ReleaseAdmissionError(
            "release_admission_event_invalid",
            f"protected release requires {expected_event}, received {event_name}",
        )
    if ref_type != "branch":
        raise ReleaseAdmissionError(
            "release_admission_ref_type_invalid",
            "protected release must be dispatched from a branch ref",
        )
    if git_ref != expected_ref:
        raise ReleaseAdmissionError(
            "release_admission_ref_mismatch",
            f"protected release ref must be {expected_ref}",
        )
    if ref_protected != "true":
        raise ReleaseAdmissionError(
            "release_admission_ref_unprotected",
            "protected release ref is not protected by repository rules",
        )
    if workflow != expected_workflow:
        raise ReleaseAdmissionError(
            "release_admission_workflow_mismatch",
            f"protected release workflow must be {expected_workflow}",
        )

    provider = _safe_input(source, "RELEASE_INPUT_PROVIDER").lower()
    if provider not in _ALLOWED_PROVIDERS:
        raise ReleaseAdmissionError(
            "release_admission_provider_invalid",
            "release provider must be openai or deepseek",
        )
    model = _safe_input(source, "RELEASE_INPUT_MODEL")
    embedding_model = _safe_input(source, "RELEASE_INPUT_EMBEDDING_MODEL")
    raw_dimension = _required(source, "RELEASE_INPUT_EMBEDDING_DIMENSION", environment_blocked=False)
    if not raw_dimension.isdigit():
        raise ReleaseAdmissionError(
            "release_admission_embedding_dimension_invalid",
            "embedding dimension must be a positive integer",
        )
    embedding_dimension = int(raw_dimension)
    if embedding_dimension < 1 or embedding_dimension > 65535:
        raise ReleaseAdmissionError(
            "release_admission_embedding_dimension_invalid",
            "embedding dimension must be between 1 and 65535",
        )

    return {
        "contract": CONTRACT,
        "status": "PASS",
        "event_name": event_name,
        "workflow": workflow,
        "git_ref": git_ref,
        "ref_type": ref_type,
        "ref_protected": True,
        "provider": provider,
        "model": model,
        "embedding_model": embedding_model,
        "embedding_dimension": embedding_dimension,
        "run_id": str(source.get("GITHUB_RUN_ID") or ""),
        "run_attempt": str(source.get("GITHUB_RUN_ATTEMPT") or ""),
    }


def _safe_metadata(source: Mapping[str, str], name: str, *, limit: int = 256) -> str:
    value = str(source.get(name) or "").strip()
    if not value or len(value) > limit or not _SAFE_TEXT_RE.fullmatch(value[:160]):
        return ""
    return value


def _failure(exc: ReleaseAdmissionError, source: Mapping[str, str]) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
        "reason": exc.code,
        "error": exc.detail,
        "event_name": _safe_metadata(source, "GITHUB_EVENT_NAME"),
        "workflow": _safe_metadata(source, "GITHUB_WORKFLOW"),
        "git_ref": _safe_metadata(source, "GITHUB_REF"),
        "run_id": _safe_metadata(source, "GITHUB_RUN_ID"),
        "run_attempt": _safe_metadata(source, "GITHUB_RUN_ATTEMPT"),
        "credential_values_emitted": False,
    }


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    source = dict(os.environ)
    try:
        payload = validate_release_admission(source)
        payload["credential_values_emitted"] = False
    except ReleaseAdmissionError as exc:
        payload = _failure(exc, source)
    if args.output:
        _write_json_atomic(Path(args.output), payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if payload["status"] == "PASS":
        return 0
    if payload["status"] == "BLOCKED_BY_ENVIRONMENT":
        return 78
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
