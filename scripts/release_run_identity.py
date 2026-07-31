#!/usr/bin/env python3
"""Fail-closed GitHub Actions run identity and checkout provenance contract."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

CONTRACT = "release-run-identity@1"
FINGERPRINT_ENV = "PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT"
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ReleaseRunIdentityError(RuntimeError):
    def __init__(self, code: str, message: str, *, environment_blocked: bool = False):
        super().__init__(message)
        self.code = str(code)
        self.environment_blocked = bool(environment_blocked)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _positive_integer(source: Mapping[str, str], name: str) -> str:
    value = str(source.get(name) or "").strip()
    if not value.isdigit() or int(value) < 1:
        raise ReleaseRunIdentityError(
            f"{name.lower()}_invalid",
            f"{name} must be a positive integer",
            environment_blocked=True,
        )
    return value


def _https_url(source: Mapping[str, str], name: str) -> str:
    value = str(source.get(name) or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ReleaseRunIdentityError(
            f"{name.lower()}_invalid",
            f"{name} must be an explicit HTTPS URL",
            environment_blocked=True,
        )
    return value


def _run_git(
    workspace: Path,
    args: Sequence[str],
    *,
    allow_empty: bool = False,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise ReleaseRunIdentityError(
            "release_git_unavailable",
            "git is required to validate the protected checkout",
            environment_blocked=True,
        ) from exc
    if completed.returncode not in allowed_returncodes:
        detail = (completed.stderr or completed.stdout or "git command failed").strip()
        raise ReleaseRunIdentityError(
            "release_git_identity_unavailable",
            f"unable to validate protected checkout: {detail[:240]}",
            environment_blocked=True,
        )
    value = completed.stdout.strip()
    if not value and not allow_empty:
        raise ReleaseRunIdentityError(
            "release_git_identity_empty",
            "protected checkout identity is empty",
            environment_blocked=True,
        )
    return value


def _normalize_origin(url: str) -> str:
    raw = str(url or "").strip()
    if raw.startswith("git@github.com:"):
        raw = "https://github.com/" + raw.split(":", 1)[1]
    raw = raw.removesuffix(".git").rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return ""
    return f"https://{parsed.hostname.lower()}{parsed.path}".rstrip("/")


def capture_run_identity(
    workspace_root: Path,
    *,
    env: Mapping[str, str] | None = None,
    validate_git: bool = True,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    source = dict(os.environ if env is None else env)

    if str(source.get("GITHUB_ACTIONS") or "").strip().lower() != "true" or str(source.get("CI") or "").strip().lower() != "true":
        raise ReleaseRunIdentityError(
            "release_ci_identity_missing",
            "protected release must run inside GitHub Actions CI",
            environment_blocked=True,
        )

    expected_event = str(source.get("PRODUCTION_RELEASE_EXPECTED_EVENT") or "workflow_dispatch").strip()
    event_name = str(source.get("GITHUB_EVENT_NAME") or "").strip()
    if event_name != expected_event:
        raise ReleaseRunIdentityError(
            "release_event_invalid",
            f"protected release requires {expected_event}, received {event_name or '<missing>'}",
        )

    repository = str(source.get("GITHUB_REPOSITORY") or "").strip()
    if not _REPOSITORY_RE.fullmatch(repository):
        raise ReleaseRunIdentityError(
            "github_repository_invalid",
            "GITHUB_REPOSITORY must be owner/repository",
            environment_blocked=True,
        )
    repository_id = _positive_integer(source, "GITHUB_REPOSITORY_ID")

    commit_sha = str(source.get("GITHUB_SHA") or "").strip().casefold()
    workflow_sha = str(source.get("GITHUB_WORKFLOW_SHA") or "").strip().casefold()
    if not _SHA40_RE.fullmatch(commit_sha) or not _SHA40_RE.fullmatch(workflow_sha):
        raise ReleaseRunIdentityError(
            "github_commit_identity_invalid",
            "GITHUB_SHA and GITHUB_WORKFLOW_SHA must be full 40-character commit identities",
            environment_blocked=True,
        )
    if workflow_sha != commit_sha:
        raise ReleaseRunIdentityError(
            "release_workflow_source_mismatch",
            "release workflow and certified source must come from the same commit",
        )

    git_ref = str(source.get("GITHUB_REF") or "").strip()
    expected_ref = str(source.get("PRODUCTION_RELEASE_EXPECTED_REF") or "").strip()
    if not expected_ref.startswith("refs/heads/"):
        raise ReleaseRunIdentityError(
            "release_expected_ref_missing",
            "PRODUCTION_RELEASE_EXPECTED_REF must name one protected branch",
            environment_blocked=True,
        )
    if git_ref != expected_ref:
        raise ReleaseRunIdentityError(
            "release_ref_mismatch",
            f"protected release ref must be {expected_ref}",
        )
    if str(source.get("GITHUB_REF_TYPE") or "").strip() != "branch":
        raise ReleaseRunIdentityError(
            "release_ref_type_invalid",
            "protected release must run from a branch ref",
        )
    if str(source.get("GITHUB_REF_PROTECTED") or "").strip().lower() != "true":
        raise ReleaseRunIdentityError(
            "release_ref_unprotected",
            "protected release ref is not protected by repository rules",
        )

    workflow_name = str(source.get("GITHUB_WORKFLOW") or "").strip()
    expected_workflow = str(source.get("PRODUCTION_RELEASE_EXPECTED_WORKFLOW") or "production-certification-release").strip()
    if workflow_name != expected_workflow:
        raise ReleaseRunIdentityError(
            "release_workflow_name_mismatch",
            f"protected release workflow must be {expected_workflow}",
        )
    job_name = str(source.get("GITHUB_JOB") or "").strip()
    expected_job = str(source.get("PRODUCTION_RELEASE_EXPECTED_JOB") or "protected-release").strip()
    if job_name != expected_job:
        raise ReleaseRunIdentityError(
            "release_job_identity_mismatch",
            f"protected release job must be {expected_job}",
        )

    workflow_ref = str(source.get("GITHUB_WORKFLOW_REF") or "").strip()
    expected_workflow_ref = f"{repository}/.github/workflows/release.yml@{expected_ref}"
    if workflow_ref != expected_workflow_ref:
        raise ReleaseRunIdentityError(
            "release_workflow_ref_mismatch",
            "GITHUB_WORKFLOW_REF does not identify the locked release workflow on the protected ref",
        )

    run_id = _positive_integer(source, "GITHUB_RUN_ID")
    run_attempt = _positive_integer(source, "GITHUB_RUN_ATTEMPT")
    run_number = _positive_integer(source, "GITHUB_RUN_NUMBER")
    server_url = _https_url(source, "GITHUB_SERVER_URL")
    api_url = _https_url(source, "GITHUB_API_URL")

    checkout: dict[str, Any] = {
        "head_sha": commit_sha,
        "origin": f"{server_url}/{repository}",
        "clean": True,
        "credential_headers_present": False,
    }
    if validate_git:
        top = Path(_run_git(workspace, ["rev-parse", "--show-toplevel"])).resolve()
        if top != workspace:
            raise ReleaseRunIdentityError(
                "release_checkout_root_mismatch",
                "protected checkout root does not match the certified workspace",
            )
        head = _run_git(workspace, ["rev-parse", "HEAD"]).casefold()
        if head != commit_sha:
            raise ReleaseRunIdentityError(
                "release_checkout_commit_mismatch",
                "checked-out HEAD does not match GITHUB_SHA",
            )
        if _run_git(workspace, ["cat-file", "-t", commit_sha]) != "commit":
            raise ReleaseRunIdentityError(
                "release_checkout_object_invalid",
                "GITHUB_SHA is not a commit object in the protected checkout",
            )
        status = _run_git(workspace, ["status", "--porcelain=v1", "--untracked-files=all"], allow_empty=True)
        if status:
            raise ReleaseRunIdentityError(
                "release_checkout_dirty",
                "protected checkout contains tracked or untracked source changes",
            )
        extra_headers = _run_git(
            workspace,
            ["config", "--local", "--get-regexp", r"^http\..*\.extraheader$"],
            allow_empty=True,
            allowed_returncodes=(0, 1),
        )
        if extra_headers:
            raise ReleaseRunIdentityError(
                "release_checkout_credentials_persisted",
                "checkout credentials remain persisted in local git configuration",
            )
        origin = _normalize_origin(_run_git(workspace, ["remote", "get-url", "origin"]))
        expected_origin = _normalize_origin(f"{server_url}/{repository}")
        if not origin or origin.casefold() != expected_origin.casefold():
            raise ReleaseRunIdentityError(
                "release_checkout_origin_mismatch",
                "protected checkout origin does not match GITHUB_REPOSITORY",
            )
        checkout = {
            "head_sha": head,
            "origin": origin,
            "clean": True,
            "credential_headers_present": False,
        }

    payload: dict[str, Any] = {
        "contract": CONTRACT,
        "status": "PASS",
        "event_name": event_name,
        "repository": repository,
        "repository_id": repository_id,
        "workflow": workflow_name,
        "workflow_ref": workflow_ref,
        "workflow_sha": workflow_sha,
        "job": job_name,
        "git_ref": git_ref,
        "ref_type": "branch",
        "ref_protected": True,
        "commit_sha": commit_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_number": run_number,
        "server_url": server_url,
        "api_url": api_url,
        "checkout": checkout,
    }
    payload["run_identity_fingerprint_sha256"] = _canonical_sha256(payload)
    return payload


def validate_run_identity_payload(
    payload: Mapping[str, Any],
    *,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    if payload.get("contract") != CONTRACT or payload.get("status") != "PASS":
        raise ReleaseRunIdentityError(
            "release_run_identity_contract_invalid",
            "release run identity is not a PASS contract",
        )
    fingerprint = str(payload.get("run_identity_fingerprint_sha256") or "").strip().casefold()
    unsigned = dict(payload)
    unsigned.pop("run_identity_fingerprint_sha256", None)
    if not _SHA256_RE.fullmatch(fingerprint) or _canonical_sha256(unsigned) != fingerprint:
        raise ReleaseRunIdentityError(
            "release_run_identity_fingerprint_invalid",
            "release run identity fingerprint is invalid",
        )
    if expected_fingerprint and fingerprint != str(expected_fingerprint).strip().casefold():
        raise ReleaseRunIdentityError(
            "release_run_identity_mismatch",
            "release run identity does not match the expected protected run",
        )
    return dict(payload)


__all__ = [
    "CONTRACT",
    "FINGERPRINT_ENV",
    "ReleaseRunIdentityError",
    "capture_run_identity",
    "validate_run_identity_payload",
]
