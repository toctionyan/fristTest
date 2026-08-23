from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from concrete_publication_hosts import (
    GitHubPullRequestPublicationHost,
    GitHubTransport,
    SubprocessLocalGitPublicationHost,
)
from provider_adapters import EventDrivenCIProviderAdapter, LocalProcessProviderAdapter, ProfileRunner
from publication_provider_adapters import CodeReviewProviderAdapter, LocalGitProviderAdapter
from request_dataflow_provider_adapter import RequestDataflowProviderAdapter
from workflow_dispatcher import ProviderAdapterRegistry
from workspace_provider_adapter import StructuredWorkspaceProviderAdapter


class StarterProviderBootstrapError(RuntimeError):
    """Raised when a concrete Starter Provider assembly is incomplete or ambiguous."""


def _identifier(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or any(char.isspace() for char in text):
        raise StarterProviderBootstrapError(f"{field_name} must be a stable non-empty identifier")
    return text


@dataclass(frozen=True)
class GitHubPullRequestConfiguration:
    repository_full_name: str
    token: str = field(repr=False)
    code_review_provider_id: str = "github.code_review"
    ci_provider_id: str = "github.actions"
    api_base: str = "https://api.github.com"

    @classmethod
    def from_environment(
        cls,
        *,
        repository_full_name: str,
        token_environment_variable: str = "GITHUB_TOKEN",
        code_review_provider_id: str = "github.code_review",
        ci_provider_id: str = "github.actions",
        api_base: str = "https://api.github.com",
    ) -> "GitHubPullRequestConfiguration":
        variable = str(token_environment_variable or "").strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", variable):
            raise StarterProviderBootstrapError(
                "token_environment_variable must be a stable uppercase name"
            )
        token = os.environ.get(variable, "")
        if not token:
            raise StarterProviderBootstrapError(
                f"GitHub token environment variable is missing: {variable}"
            )
        return cls(
            repository_full_name=repository_full_name,
            token=token,
            code_review_provider_id=code_review_provider_id,
            ci_provider_id=ci_provider_id,
            api_base=api_base,
        )


@dataclass(frozen=True)
class ConcreteStarterProviderAssembly:
    registry: ProviderAdapterRegistry
    provider_ids: tuple[str, ...]
    policy: Mapping[str, object]


def build_concrete_starter_provider_registry(
    *,
    workspace: Path,
    write_scope: Iterable[str],
    allowed_profiles: Mapping[str, Iterable[str]],
    github: GitHubPullRequestConfiguration,
    process_runner: ProfileRunner | None = None,
    github_transport: GitHubTransport | None = None,
) -> ConcreteStarterProviderAssembly:
    """Assemble concrete Providers for Customer Agent repair/full-dev Workflows.

    This function creates implementations only. Workflow activation still selects
    providers, and WorkflowAdapterDispatcher still enforces WriteAuthorityGuard.
    No merge adapter or completion decision is exposed by this assembly.
    """

    root = workspace.resolve()
    if not root.is_dir():
        raise StarterProviderBootstrapError(f"workspace is not a directory: {root}")
    code_review_provider_id = _identifier(
        github.code_review_provider_id, field_name="code_review_provider_id"
    )
    ci_provider_id = _identifier(github.ci_provider_id, field_name="ci_provider_id")
    fixed_ids = ("local.workspace", "local.process", "local.git")
    provider_ids = (*fixed_ids, code_review_provider_id, ci_provider_id)
    if len(set(provider_ids)) != len(provider_ids):
        raise StarterProviderBootstrapError(
            f"concrete Starter Provider IDs must be unique: {provider_ids}"
        )
    scope = tuple(dict.fromkeys(str(value).strip() for value in write_scope if str(value).strip()))
    if not scope:
        raise StarterProviderBootstrapError("concrete Starter bootstrap requires non-empty write_scope")

    workspace_adapter = StructuredWorkspaceProviderAdapter(
        workspace=root,
        allowed_path_patterns=scope,
    )
    process_adapter = LocalProcessProviderAdapter(
        workspace=root,
        allowed_profiles=allowed_profiles,
        runner=process_runner,
    )
    git_adapter = LocalGitProviderAdapter(
        workspace=root,
        host=SubprocessLocalGitPublicationHost(workspace=root),
    )
    github_host = GitHubPullRequestPublicationHost(
        repository_full_name=github.repository_full_name,
        token=github.token,
        api_base=github.api_base,
        transport=github_transport,
    )
    code_review_adapter = CodeReviewProviderAdapter(
        workspace=root,
        provider_id=code_review_provider_id,
        host=github_host,
        merge_authority_guard=None,
    )
    ci_adapter = EventDrivenCIProviderAdapter(
        workspace=root,
        provider_id=ci_provider_id,
    )
    registry = ProviderAdapterRegistry(
        [
            workspace_adapter,
            process_adapter,
            git_adapter,
            RequestDataflowProviderAdapter(code_review_adapter),
            ci_adapter,
        ]
    )
    return ConcreteStarterProviderAssembly(
        registry=registry,
        provider_ids=provider_ids,
        policy={
            "schema": "concrete-starter-provider-assembly@1",
            "write_scope": list(scope),
            "repository_full_name": github.repository_full_name,
            "code_review_provider_id": code_review_provider_id,
            "ci_provider_id": ci_provider_id,
            "automatic_merge": False,
            "merge_adapter_enabled": False,
            "write_authority_granted": False,
            "completion_authority": "TaskRun",
            "authority_effect": False,
        },
    )


__all__ = [
    "ConcreteStarterProviderAssembly",
    "GitHubPullRequestConfiguration",
    "StarterProviderBootstrapError",
    "build_concrete_starter_provider_registry",
]
