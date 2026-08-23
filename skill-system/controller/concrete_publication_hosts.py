from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol

from langgraph_workflow_runtime import WorkflowRuntimeState
from publication_provider_adapters import (
    PublicationHostResult,
    PublicationProviderError,
)
from workflow_graph_contract import WorkflowStepSpec


_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PROTECTED_GIT_PATHS = (".git", ".harness", ".quality")


def _text(value: object) -> str:
    return str(value or "").strip()


def _sha(value: object, *, field: str) -> str:
    text = _text(value)
    if not _SHA.fullmatch(text):
        raise PublicationProviderError(f"{field} must be an exact lowercase 40-character commit SHA")
    return text


def _repository(value: object) -> str:
    text = _text(value)
    if not _REPOSITORY.fullmatch(text) or ".." in text:
        raise PublicationProviderError("repository_full_name must use exact owner/name form")
    return text


def _repo_path(value: object) -> str:
    text = _text(value).replace("\\", "/")
    if not text or text.startswith("/"):
        raise PublicationProviderError("changed path must be repository-relative")
    path = PurePosixPath(text)
    if path.as_posix() != text or any(part in {"", ".", ".."} for part in path.parts):
        raise PublicationProviderError(f"changed path is not canonical: {text!r}")
    if path.parts[0] in _PROTECTED_GIT_PATHS:
        raise PublicationProviderError(f"control-plane path cannot be committed by Provider host: {text}")
    return path.as_posix()


def _zpaths(value: bytes) -> tuple[str, ...]:
    return tuple(part.decode("utf-8") for part in value.split(b"\0") if part)


class SubprocessLocalGitPublicationHost:
    """Create one exact-parent, exact-path commit in a real local repository.

    Arguments are passed directly to Git without a shell. Runtime state cannot
    supply command names, flags, environment variables, or hooks policy.
    """

    def __init__(self, *, workspace: Path, git_binary: str = "git") -> None:
        self.workspace = workspace.resolve()
        self.git_binary = _text(git_binary)
        if not self.git_binary or any(char.isspace() for char in self.git_binary):
            raise PublicationProviderError("git_binary must be one executable token")
        if not self.workspace.is_dir():
            raise PublicationProviderError(f"Git workspace is not a directory: {self.workspace}")
        root = Path(self._run(("rev-parse", "--show-toplevel")).decode("utf-8").strip()).resolve()
        if root != self.workspace:
            raise PublicationProviderError(
                f"Git workspace must be the repository root: expected={self.workspace} actual={root}"
            )

    def _run(
        self,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> bytes:
        process = subprocess.run(
            [self.git_binary, *arguments],
            cwd=self.workspace,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
        if process.returncode not in allowed_returncodes:
            stderr = process.stderr.decode("utf-8", errors="replace").strip()
            summary = stderr.splitlines()[-1][:400] if stderr else "no diagnostic"
            raise PublicationProviderError(
                f"Git operation failed ({arguments[0]}, exit={process.returncode}): {summary}"
            )
        return process.stdout

    def _changed_paths(self) -> tuple[str, ...]:
        tracked = _zpaths(self._run(("diff", "--name-only", "-z", "HEAD", "--")))
        untracked = _zpaths(self._run(("ls-files", "--others", "--exclude-standard", "-z")))
        return tuple(sorted(dict.fromkeys((*tracked, *untracked))))

    def create_commit(
        self,
        *,
        request: Mapping[str, Any],
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> PublicationHostResult:
        del step, state
        expected_parent = _sha(request.get("expected_parent_sha"), field="expected_parent_sha")
        current_parent = _sha(
            self._run(("rev-parse", "HEAD")).decode("utf-8").strip(),
            field="current HEAD",
        )
        if current_parent != expected_parent:
            raise PublicationProviderError(
                f"Git HEAD does not match expected_parent_sha: expected={expected_parent} actual={current_parent}"
            )
        branch = self._run(("symbolic-ref", "--quiet", "--short", "HEAD")).decode("utf-8").strip()
        if not branch:
            raise PublicationProviderError("Git commit requires a named branch, not detached HEAD")

        raw_paths = request.get("changed_paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise PublicationProviderError("Git commit requires non-empty changed_paths")
        requested_paths = tuple(_repo_path(value) for value in raw_paths)
        if len(set(requested_paths)) != len(requested_paths):
            raise PublicationProviderError("Git commit changed_paths must be unique")
        actual_paths = self._changed_paths()
        if set(actual_paths) != set(requested_paths):
            raise PublicationProviderError(
                f"Git worktree changed paths differ from requested exact scope: "
                f"requested={sorted(requested_paths)} actual={list(actual_paths)}"
            )

        cached = self._run(
            ("diff", "--cached", "--quiet", "--exit-code"),
            allowed_returncodes=(0, 1),
        )
        del cached
        cached_names = _zpaths(self._run(("diff", "--cached", "--name-only", "-z", "--")))
        if cached_names:
            raise PublicationProviderError(
                f"Git index must be clean before Provider commit: {sorted(cached_names)}"
            )

        self._run(("add", "--all", "--", *requested_paths))
        staged_paths = _zpaths(self._run(("diff", "--cached", "--name-only", "-z", "--")))
        if set(staged_paths) != set(requested_paths):
            raise PublicationProviderError(
                f"Git staged paths differ from requested exact scope: {sorted(staged_paths)}"
            )
        message = _text(request.get("message"))
        if not message:
            raise PublicationProviderError("Git commit requires a non-empty message")
        self._run(("commit", "--no-gpg-sign", "-m", message))

        commit_sha = _sha(
            self._run(("rev-parse", "HEAD")).decode("utf-8").strip(),
            field="created commit SHA",
        )
        observed_parent = _sha(
            self._run(("rev-parse", f"{commit_sha}^" )).decode("utf-8").strip(),
            field="created commit parent",
        )
        if observed_parent != expected_parent:
            raise PublicationProviderError("created commit parent does not match expected_parent_sha")
        committed_paths = _zpaths(
            self._run(("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit_sha, "--"))
        )
        if set(committed_paths) != set(requested_paths):
            raise PublicationProviderError(
                f"created commit paths differ from requested exact scope: {sorted(committed_paths)}"
            )
        return PublicationHostResult(
            receipt={
                "commit_sha": commit_sha,
                "parent_sha": observed_parent,
                "changed_paths": list(requested_paths),
                "branch": branch,
            },
            evidence_refs=(
                f"git-commit:{commit_sha}",
                f"git-ref:{branch}@{commit_sha}",
            ),
        )


class GitHubTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        ...


class UrllibGitHubTransport:
    """Small standard-library HTTPS transport that never returns response bodies in errors."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise PublicationProviderError("GitHub timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        data = None if payload is None else json.dumps(dict(payload)).encode("utf-8")
        request = urllib.request.Request(url=url, data=data, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise PublicationProviderError(f"GitHub API returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise PublicationProviderError("GitHub API transport failed") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicationProviderError("GitHub API returned invalid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise PublicationProviderError("GitHub API response must be an object")
        return decoded


class GitHubPullRequestPublicationHost:
    """Create and exact-head verify a GitHub pull request.

    This host intentionally does not implement merge. A token is an API
    prerequisite only; it is never written to evidence or returned in receipts.
    """

    def __init__(
        self,
        *,
        repository_full_name: str,
        token: str,
        api_base: str = "https://api.github.com",
        transport: GitHubTransport | None = None,
    ) -> None:
        self.repository_full_name = _repository(repository_full_name)
        self._token = _text(token)
        if not self._token:
            raise PublicationProviderError("GitHub pull-request host requires a non-empty token")
        parsed = urllib.parse.urlparse(_text(api_base))
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise PublicationProviderError("GitHub api_base must be an HTTPS origin without credentials")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise PublicationProviderError("GitHub api_base must not contain path, query, or fragment")
        self.api_base = f"{parsed.scheme}://{parsed.netloc}"
        self.transport = transport or UrllibGitHubTransport()

    @classmethod
    def from_environment(
        cls,
        *,
        repository_full_name: str,
        token_environment_variable: str = "GITHUB_TOKEN",
        api_base: str = "https://api.github.com",
        transport: GitHubTransport | None = None,
    ) -> "GitHubPullRequestPublicationHost":
        variable = _text(token_environment_variable)
        if not variable or not re.fullmatch(r"[A-Z][A-Z0-9_]*", variable):
            raise PublicationProviderError("token_environment_variable must be a stable uppercase name")
        token = os.environ.get(variable, "")
        if not token:
            raise PublicationProviderError(f"GitHub token environment variable is missing: {variable}")
        return cls(
            repository_full_name=repository_full_name,
            token=token,
            api_base=api_base,
            transport=transport,
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "portable-development-harness/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, suffix: str) -> str:
        owner, repository = self.repository_full_name.split("/", 1)
        return (
            f"{self.api_base}/repos/{urllib.parse.quote(owner, safe='')}/"
            f"{urllib.parse.quote(repository, safe='')}{suffix}"
        )

    def _request_api(
        self,
        *,
        method: str,
        url: str,
        payload: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        try:
            return self.transport.request(
                method=method,
                url=url,
                headers=self._headers,
                payload=payload,
            )
        except Exception as exc:
            # A transport is an untrusted effect boundary. Never propagate its
            # message because it may include request headers or credentials.
            raise PublicationProviderError(f"GitHub API {method} request failed") from exc

    def _verify_pull_request(
        self,
        payload: Mapping[str, Any],
        *,
        expected_number: int,
        expected_base: str,
        expected_head: str,
        expected_sha: str,
        expected_draft: bool,
    ) -> dict[str, Any]:
        if payload.get("number") != expected_number:
            raise PublicationProviderError("GitHub pull-request number mismatch")
        base = payload.get("base") if isinstance(payload.get("base"), Mapping) else {}
        head = payload.get("head") if isinstance(payload.get("head"), Mapping) else {}
        base_repo = base.get("repo") if isinstance(base.get("repo"), Mapping) else {}
        head_repo = head.get("repo") if isinstance(head.get("repo"), Mapping) else {}
        if _text(base_repo.get("full_name")) != self.repository_full_name:
            raise PublicationProviderError("GitHub pull-request base repository mismatch")
        if _text(head_repo.get("full_name")) != self.repository_full_name:
            raise PublicationProviderError("GitHub pull-request head repository mismatch")
        if _text(base.get("ref")) != expected_base:
            raise PublicationProviderError("GitHub pull-request base branch mismatch")
        if _text(head.get("ref")) != expected_head:
            raise PublicationProviderError("GitHub pull-request head branch mismatch")
        if _sha(head.get("sha"), field="GitHub pull-request head SHA") != expected_sha:
            raise PublicationProviderError("GitHub pull-request exact head SHA mismatch")
        if payload.get("draft") is not expected_draft:
            raise PublicationProviderError("GitHub pull-request draft state mismatch")
        url = _text(payload.get("html_url"))
        if not url.startswith("https://"):
            raise PublicationProviderError("GitHub pull-request response requires HTTPS html_url")
        return {
            "pull_request_number": expected_number,
            "pull_request_url": url,
            "repository_full_name": self.repository_full_name,
            "base_branch": expected_base,
            "head_branch": expected_head,
            "head_sha": expected_sha,
            "draft": expected_draft,
        }

    def create_pull_request(
        self,
        *,
        request: Mapping[str, Any],
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> PublicationHostResult:
        del step, state
        declared_repo = _text(request.get("repository_full_name"))
        if declared_repo and declared_repo != self.repository_full_name:
            raise PublicationProviderError("pull-request request repository_full_name mismatch")
        base = _text(request.get("base_branch"))
        head = _text(request.get("head_branch"))
        head_sha = _sha(request.get("head_sha"), field="head_sha")
        draft = request.get("draft")
        if not base or not head or any(char.isspace() for char in base + head):
            raise PublicationProviderError("pull-request branches must be stable non-empty identifiers")
        if not isinstance(draft, bool):
            raise PublicationProviderError("pull-request draft must be boolean")
        created = self._request_api(
            method="POST",
            url=self._url("/pulls"),
            payload={
                "title": _text(request.get("title")),
                "body": _text(request.get("body")),
                "head": head,
                "base": base,
                "draft": draft,
            },
        )
        number = created.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise PublicationProviderError("GitHub create response requires positive PR number")
        observed = self._request_api(
            method="GET",
            url=self._url(f"/pulls/{number}"),
            payload=None,
        )
        receipt = self._verify_pull_request(
            observed,
            expected_number=number,
            expected_base=base,
            expected_head=head,
            expected_sha=head_sha,
            expected_draft=draft,
        )
        return PublicationHostResult(
            receipt=receipt,
            evidence_refs=(
                f"github-pr:{self.repository_full_name}#{number}@{head_sha}",
                f"github-ref:{self.repository_full_name}:{head}@{head_sha}",
            ),
        )

    def merge_pull_request(
        self,
        *,
        request: Mapping[str, Any],
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> PublicationHostResult:
        del request, step, state
        raise PublicationProviderError(
            "GitHubPullRequestPublicationHost intentionally does not implement pull-request merge"
        )


__all__ = [
    "GitHubPullRequestPublicationHost",
    "GitHubTransport",
    "SubprocessLocalGitPublicationHost",
    "UrllibGitHubTransport",
]
