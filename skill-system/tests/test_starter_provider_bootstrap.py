from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from capability_registry import CapabilityBinding  # type: ignore
from starter_provider_bootstrap import (  # type: ignore
    GitHubPullRequestConfiguration,
    StarterProviderBootstrapError,
    build_concrete_starter_provider_registry,
)


class FakeGitHubTransport:
    def request(self, *, method, url, headers, payload):
        raise AssertionError("bootstrap construction must not call GitHub")


class StarterProviderBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="starter-provider-bootstrap-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        process = subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)

    @staticmethod
    def github(**overrides):
        values = {
            "repository_full_name": "owner/customer-agent",
            "token": "token-value",
            "code_review_provider_id": "github.code_review",
            "ci_provider_id": "github.actions",
        }
        values.update(overrides)
        return GitHubPullRequestConfiguration(**values)

    @staticmethod
    def binding(provider_id: str, provider_type: str, capability: str, *, mutates=False, wait=False):
        return CapabilityBinding(
            capability_id=capability,
            provider_id=provider_id,
            provider_type=provider_type,
            activation_key=provider_id,
            mutates=mutates,
            external_wait=wait,
        )

    def build(self, **overrides):
        values = {
            "workspace": self.root,
            "write_scope": ("src/**", "tests/**"),
            "allowed_profiles": {
                "test.run": ("skill-unit",),
                "quality.evaluate": ("skill-static",),
            },
            "github": self.github(),
            "github_transport": FakeGitHubTransport(),
            "process_runner": lambda profile, *, state_file: {"status": "PASS"},
        }
        values.update(overrides)
        return build_concrete_starter_provider_registry(**values)

    def test_bootstrap_registers_exact_concrete_providers_without_authority(self) -> None:
        assembly = self.build()
        self.assertEqual(
            assembly.provider_ids,
            (
                "local.workspace",
                "local.process",
                "local.git",
                "github.code_review",
                "github.actions",
            ),
        )
        bindings = (
            self.binding("local.workspace", "executor", "workspace.write", mutates=True),
            self.binding("local.process", "executor", "test.run"),
            self.binding("local.git", "executor", "vcs.commit.create", mutates=True),
            self.binding("github.code_review", "integration", "code_review.pull_request.create", mutates=True),
            self.binding("github.actions", "integration", "ci.run.wait", wait=True),
        )
        for binding in bindings:
            with self.subTest(provider=binding.provider_id):
                self.assertEqual(
                    assembly.registry.require(binding).provider_id,
                    binding.provider_id,
                )
        self.assertFalse(assembly.policy["automatic_merge"])
        self.assertFalse(assembly.policy["merge_adapter_enabled"])
        self.assertFalse(assembly.policy["write_authority_granted"])
        self.assertEqual(assembly.policy["completion_authority"], "TaskRun")

    def test_bootstrap_fails_on_empty_scope_duplicate_provider_or_non_git_workspace(self) -> None:
        with self.assertRaisesRegex(StarterProviderBootstrapError, "write_scope"):
            self.build(write_scope=())
        with self.assertRaisesRegex(StarterProviderBootstrapError, "must be unique"):
            self.build(github=self.github(ci_provider_id="local.git"))

        other = self.root / "not-a-repository"
        other.mkdir()
        with self.assertRaises(Exception):
            self.build(workspace=other)

    def test_environment_configuration_requires_token_without_exposing_it_in_repr(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(StarterProviderBootstrapError, "is missing"):
                GitHubPullRequestConfiguration.from_environment(
                    repository_full_name="owner/customer-agent"
                )
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "private-token"}, clear=True):
            config = GitHubPullRequestConfiguration.from_environment(
                repository_full_name="owner/customer-agent"
            )
        self.assertNotIn("private-token", repr(config))


if __name__ == "__main__":
    unittest.main()
