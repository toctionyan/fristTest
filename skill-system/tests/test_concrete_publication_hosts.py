from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from concrete_publication_hosts import (  # type: ignore
    GitHubPullRequestPublicationHost,
    SubprocessLocalGitPublicationHost,
)
from publication_provider_adapters import PublicationProviderError  # type: ignore
from workflow_graph_contract import WorkflowStepSpec  # type: ignore


class FakeGitHubTransport:
    def __init__(self, *, head_sha: str, repository: str = "owner/project") -> None:
        self.head_sha = head_sha
        self.repository = repository
        self.calls = []
        self.fail = False

    def request(self, *, method, url, headers, payload):
        self.calls.append((method, url, dict(headers), None if payload is None else dict(payload)))
        if self.fail:
            raise PublicationProviderError("transport unavailable")
        return {
            "number": 91,
            "html_url": "https://github.com/owner/project/pull/91",
            "draft": False,
            "base": {"ref": "main", "repo": {"full_name": self.repository}},
            "head": {
                "ref": "feat/customer-agent",
                "sha": self.head_sha,
                "repo": {"full_name": self.repository},
            },
        }


class LeakingGitHubTransport:
    def request(self, *, method, url, headers, payload):
        raise RuntimeError(f"transport leaked {headers['Authorization']}")


class ConcretePublicationHostsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="concrete-publication-hosts-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    @staticmethod
    def run_git(root: Path, *arguments: str) -> str:
        process = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode:
            raise AssertionError(process.stderr)
        return process.stdout.strip()

    def git_repository(self) -> tuple[Path, str]:
        self.run_git(self.root, "init", "-b", "main")
        self.run_git(self.root, "config", "user.name", "Harness Test")
        self.run_git(self.root, "config", "user.email", "harness@example.invalid")
        (self.root / "README.md").write_text("baseline\n", encoding="utf-8")
        self.run_git(self.root, "add", "README.md")
        self.run_git(self.root, "commit", "-m", "baseline")
        return self.root, self.run_git(self.root, "rev-parse", "HEAD")

    @staticmethod
    def step(capability: str) -> WorkflowStepSpec:
        return WorkflowStepSpec(
            step_id="publish",
            step_type="executor",
            use=capability,
            routes={"green": "END", "blocked": "BLOCKED_UNRECOVERABLE"},
            max_attempts=2,
        )

    def test_real_local_git_host_commits_exact_parent_and_paths(self) -> None:
        root, parent = self.git_repository()
        (root / "src").mkdir()
        (root / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
        host = SubprocessLocalGitPublicationHost(workspace=root)
        result = host.create_commit(
            request={
                "expected_parent_sha": parent,
                "message": "feat: add app",
                "changed_paths": ["src/app.py"],
            },
            step=self.step("vcs.commit.create"),
            state={"task_id": "task-git"},
        )

        commit_sha = result.receipt["commit_sha"]
        self.assertEqual(len(commit_sha), 40)
        self.assertEqual(result.receipt["parent_sha"], parent)
        self.assertEqual(result.receipt["changed_paths"], ["src/app.py"])
        self.assertEqual(result.receipt["branch"], "main")
        self.assertEqual(self.run_git(root, "rev-parse", "HEAD"), commit_sha)
        self.assertEqual(self.run_git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha), "src/app.py")

    def test_git_host_rejects_wrong_parent_out_of_scope_and_staged_index(self) -> None:
        root, parent = self.git_repository()
        host = SubprocessLocalGitPublicationHost(workspace=root)
        (root / "one.py").write_text("one\n", encoding="utf-8")
        with self.assertRaisesRegex(PublicationProviderError, "HEAD does not match"):
            host.create_commit(
                request={"expected_parent_sha": "0" * 40, "message": "bad", "changed_paths": ["one.py"]},
                step=self.step("vcs.commit.create"),
                state={},
            )

        (root / "two.py").write_text("two\n", encoding="utf-8")
        with self.assertRaisesRegex(PublicationProviderError, "exact scope"):
            host.create_commit(
                request={"expected_parent_sha": parent, "message": "bad", "changed_paths": ["one.py"]},
                step=self.step("vcs.commit.create"),
                state={},
            )

        (root / "two.py").unlink()
        self.run_git(root, "add", "one.py")
        with self.assertRaisesRegex(PublicationProviderError, "index must be clean"):
            host.create_commit(
                request={"expected_parent_sha": parent, "message": "bad", "changed_paths": ["one.py"]},
                step=self.step("vcs.commit.create"),
                state={},
            )

    def test_github_host_creates_then_reloads_exact_head_without_token_evidence(self) -> None:
        head_sha = "3" * 40
        transport = FakeGitHubTransport(head_sha=head_sha)
        host = GitHubPullRequestPublicationHost(
            repository_full_name="owner/project",
            token="secret-token-value",
            transport=transport,
        )
        result = host.create_pull_request(
            request={
                "repository_full_name": "owner/project",
                "base_branch": "main",
                "head_branch": "feat/customer-agent",
                "head_sha": head_sha,
                "title": "feat: customer agent",
                "body": "bounded change",
                "draft": False,
            },
            step=self.step("code_review.pull_request.create"),
            state={"task_id": "task-pr"},
        )

        self.assertEqual([call[0] for call in transport.calls], ["POST", "GET"])
        self.assertEqual(transport.calls[0][3]["head"], "feat/customer-agent")
        self.assertEqual(result.receipt["head_sha"], head_sha)
        self.assertEqual(result.receipt["repository_full_name"], "owner/project")
        serialized = repr((result.receipt, result.evidence_refs))
        self.assertNotIn("secret-token-value", serialized)
        self.assertIn("Bearer secret-token-value", transport.calls[0][2]["Authorization"])

    def test_github_host_rejects_missing_token_remote_mismatch_and_merge(self) -> None:
        with self.assertRaisesRegex(PublicationProviderError, "non-empty token"):
            GitHubPullRequestPublicationHost(
                repository_full_name="owner/project",
                token="",
                transport=FakeGitHubTransport(head_sha="3" * 40),
            )

        transport = FakeGitHubTransport(head_sha="4" * 40)
        host = GitHubPullRequestPublicationHost(
            repository_full_name="owner/project",
            token="secret-token-value",
            transport=transport,
        )
        with self.assertRaisesRegex(PublicationProviderError, "exact head SHA mismatch"):
            host.create_pull_request(
                request={
                    "base_branch": "main",
                    "head_branch": "feat/customer-agent",
                    "head_sha": "3" * 40,
                    "title": "title",
                    "body": "body",
                    "draft": False,
                },
                step=self.step("code_review.pull_request.create"),
                state={},
            )
        with self.assertRaisesRegex(PublicationProviderError, "does not implement"):
            host.merge_pull_request(request={}, step=self.step("code_review.pull_request.merge"), state={})

        leaking = GitHubPullRequestPublicationHost(
            repository_full_name="owner/project",
            token="never-persist-this-token",
            transport=LeakingGitHubTransport(),
        )
        with self.assertRaises(PublicationProviderError) as caught:
            leaking.create_pull_request(
                request={
                    "base_branch": "main",
                    "head_branch": "feat/customer-agent",
                    "head_sha": "3" * 40,
                    "title": "title",
                    "body": "body",
                    "draft": False,
                },
                step=self.step("code_review.pull_request.create"),
                state={},
            )
        self.assertNotIn("never-persist-this-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
