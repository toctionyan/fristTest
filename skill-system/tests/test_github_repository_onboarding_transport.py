from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system/controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from github_repository_onboarding_transport import (  # noqa: E402
    GitHubHttpResponse,
    GitHubRepositoryOnboardingError,
    GitHubRepositoryOnboardingTransport,
)
import repository_onboarding_cli  # noqa: E402


def _load_preflight():
    script = ROOT / "scripts/repository_onboarding_preflight.py"
    spec = importlib.util.spec_from_file_location(
        "repository_onboarding_preflight_transport_test", script
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREFLIGHT = _load_preflight()


def _copy_workspace(tmp_path: Path) -> Path:
    target = tmp_path / "workspace"
    files = set(PREFLIGHT.REQUIRED_ROOT_FILES) | set(PREFLIGHT.REQUIRED_WORKFLOWS)
    for relative in files:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = ROOT / relative
        shutil.copy2(source, destination)
    return target


class FakeHttpTransport:
    def __init__(self, responses: dict[str, GitHubHttpResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def request(
        self, *, method: str, url: str, headers: dict[str, str]
    ) -> GitHubHttpResponse:
        self.requests.append((method, url, dict(headers)))
        if url not in self.responses:
            raise AssertionError(f"unexpected URL: {url}")
        return self.responses[url]


def _json_response(
    payload: object,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> GitHubHttpResponse:
    return GitHubHttpResponse(
        status=status,
        headers=headers or {},
        body=json.dumps(payload).encode("utf-8"),
    )


def _content_response(raw: bytes) -> GitHubHttpResponse:
    encoded = base64.b64encode(raw).decode("ascii")
    github_content = "\n".join(
        encoded[index : index + 60] for index in range(0, len(encoded), 60)
    )
    return _json_response(
        {
            "type": "file",
            "encoding": "base64",
            "size": len(raw),
            "content": github_content,
        }
    )


def _ready_responses(
    workspace: Path, *, visibility: str = "private"
) -> dict[str, GitHubHttpResponse]:
    base = "https://api.github.com/repos/owner/project"
    release = (workspace / "release/MANIFEST.json").read_bytes()
    return {
        base: _json_response(
            {
                "full_name": "owner/project",
                "default_branch": "main",
                "visibility": visibility,
                "private": visibility == "private",
                "size": 42,
                "permissions": {"admin": True, "maintain": False, "push": True},
            }
        ),
        f"{base}/branches/main/protection": _json_response({"required_status_checks": {}}),
        f"{base}/environments?per_page=100": _json_response(
            {"environments": [{"name": "production-certification"}]}
        ),
        f"{base}/environments/production-certification/secrets?per_page=100": _json_response(
            {
                "secrets": [
                    {"name": "PRODUCTION_MODEL_API_KEY", "value": "must-never-survive"},
                    {"name": "PRODUCTION_EMBEDDING_API_KEY"},
                    {"name": "QUALITY_EVIDENCE_SIGNING_KEY"},
                ]
            }
        ),
        f"{base}/contents/release/MANIFEST.json?ref=main": _content_response(release),
    }


def _collector(workspace: Path, fake: FakeHttpTransport) -> GitHubRepositoryOnboardingTransport:
    return GitHubRepositoryOnboardingTransport(
        repository_full_name="owner/project",
        token="token-only-in-memory",
        transport=fake,
    )


def test_live_private_repository_is_names_only_sealed_and_passes_existing_preflight(
    tmp_path: Path,
) -> None:
    workspace = _copy_workspace(tmp_path)
    fake = FakeHttpTransport(_ready_responses(workspace))
    collector = _collector(workspace, fake)
    artifact_path = tmp_path / "metadata.json"

    artifact = collector.collect_and_write(artifact_path)
    reloaded = collector.load_artifact(
        artifact_path, expected_seal_sha256=artifact["seal_sha256"]
    )
    result = PREFLIGHT.evaluate(workspace, repository_metadata=reloaded["metadata"])

    assert result["status"] == "PASS"
    assert reloaded == artifact
    assert reloaded["authority_effect"] is False
    assert reloaded["deploy_allowed"] is False
    assert reloaded["production_closed"] is False
    assert reloaded["metadata"]["secret_names"] == [
        "PRODUCTION_EMBEDDING_API_KEY",
        "PRODUCTION_MODEL_API_KEY",
        "QUALITY_EVIDENCE_SIGNING_KEY",
    ]
    durable = artifact_path.read_text(encoding="utf-8")
    assert "must-never-survive" not in durable
    assert "token-only-in-memory" not in durable
    assert {method for method, _, _ in fake.requests} == {"GET"}
    assert all(
        headers["Authorization"] == "Bearer token-only-in-memory"
        for _, _, headers in fake.requests
    )
    assert all("PHASE_CANDIDATE_MANIFEST.json" not in url for _, url, _ in fake.requests)
    assert reloaded["metadata"]["workspace_marker"]["manifest_sha256"] == PREFLIGHT._sha256(
        workspace / "release/MANIFEST.json"
    )


def test_environment_and_secret_pagination_remains_repository_bound(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    responses = _ready_responses(workspace)
    env_url = "https://api.github.com/repos/owner/project/environments?per_page=100"
    env_next = "https://api.github.com/repos/owner/project/environments?page=2&per_page=100"
    secret_url = (
        "https://api.github.com/repos/owner/project/environments/"
        "production-certification/secrets?per_page=100"
    )
    secret_next = (
        "https://api.github.com/repos/owner/project/environments/"
        "production-certification/secrets?page=2&per_page=100"
    )
    responses[env_url] = _json_response(
        {"environments": [{"name": "staging"}]},
        headers={"Link": f'<{env_next}>; rel="next"'},
    )
    responses[env_next] = _json_response({"environments": [{"name": "production-certification"}]})
    responses[secret_url] = _json_response(
        {"secrets": [{"name": "PRODUCTION_MODEL_API_KEY"}]},
        headers={"link": f'<{secret_next}>; rel="next"'},
    )
    responses[secret_next] = _json_response(
        {"secrets": [
            {"name": "PRODUCTION_EMBEDDING_API_KEY"},
            {"name": "QUALITY_EVIDENCE_SIGNING_KEY"},
        ]}
    )

    artifact = _collector(workspace, FakeHttpTransport(responses)).collect()

    assert artifact["metadata"]["environments"] == ["production-certification", "staging"]
    assert len(artifact["metadata"]["secret_names"]) == 3


def test_tamper_and_ambiguous_permission_failure_fail_closed(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    collector = _collector(workspace, FakeHttpTransport(_ready_responses(workspace)))
    artifact_path = tmp_path / "metadata.json"
    collector.collect_and_write(artifact_path)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["metadata"]["permissions"]["admin"] = False
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GitHubRepositoryOnboardingError, match="seal"):
        collector.load_artifact(
            artifact_path, expected_seal_sha256=collector.collect()["seal_sha256"]
        )

    responses = _ready_responses(workspace)
    protection = "https://api.github.com/repos/owner/project/branches/main/protection"
    responses[protection] = _json_response({"message": "Resource not accessible"}, status=403)
    with pytest.raises(GitHubRepositoryOnboardingError, match="HTTP 403"):
        _collector(workspace, FakeHttpTransport(responses)).collect()


def test_wrong_repository_and_malformed_remote_content_fail_closed(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    responses = _ready_responses(workspace)
    responses["https://api.github.com/repos/owner/project"] = _json_response(
        {
            "full_name": "attacker/project",
            "default_branch": "main",
            "visibility": "private",
            "size": 1,
            "permissions": {"admin": True, "push": True},
        }
    )
    with pytest.raises(GitHubRepositoryOnboardingError, match="identity"):
        _collector(workspace, FakeHttpTransport(responses)).collect()

    responses = _ready_responses(workspace)
    contents = "https://api.github.com/repos/owner/project/contents/release/MANIFEST.json?ref=main"
    responses[contents] = _json_response(
        {"type": "file", "encoding": "base64", "size": 2, "content": "%%%"}
    )
    with pytest.raises(GitHubRepositoryOnboardingError, match="base64"):
        _collector(workspace, FakeHttpTransport(responses)).collect()


def test_missing_protection_is_negative_but_cross_origin_pagination_is_rejected(
    tmp_path: Path,
) -> None:
    workspace = _copy_workspace(tmp_path)
    responses = _ready_responses(workspace)
    protection = "https://api.github.com/repos/owner/project/branches/main/protection"
    responses[protection] = _json_response({"message": "Not Found"}, status=404)
    artifact = _collector(workspace, FakeHttpTransport(responses)).collect()
    assert artifact["metadata"]["branch_protection"] == {"main": False}

    responses = _ready_responses(workspace)
    env_url = "https://api.github.com/repos/owner/project/environments?per_page=100"
    responses[env_url] = _json_response(
        {"environments": []},
        headers={"Link": '<https://evil.invalid/steal?page=2>; rel="next"'},
    )
    with pytest.raises(GitHubRepositoryOnboardingError, match="pagination"):
        _collector(workspace, FakeHttpTransport(responses)).collect()


def test_root_cli_preflight_delegates_to_existing_evaluator_and_keeps_public_approval_explicit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _copy_workspace(tmp_path)
    fake = FakeHttpTransport(_ready_responses(workspace, visibility="public"))
    output = workspace / ".harness/runtime/repository-onboarding/public.json"

    blocked_code = repository_onboarding_cli.main(
        [
            "preflight",
            "--repository", "owner/project",
            "--workspace-root", str(workspace),
            "--output", str(output),
        ],
        transport=fake,
        environ={"GITHUB_TOKEN": "memory-only"},
    )
    blocked = json.loads(capsys.readouterr().out)
    assert blocked_code == 78
    assert blocked["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert "public_repository_requires_explicit_approval" in blocked["blockers"]
    assert blocked["metadata_artifact"] == str(output.resolve())
    assert blocked["authority_effect"] is False

    allowed_code = repository_onboarding_cli.main(
        [
            "preflight",
            "--repository", "owner/project",
            "--workspace-root", str(workspace),
            "--output", str(output),
            "--allow-public",
        ],
        transport=FakeHttpTransport(_ready_responses(workspace, visibility="public")),
        environ={"GITHUB_TOKEN": "memory-only"},
    )
    allowed = json.loads(capsys.readouterr().out)
    assert allowed_code == 0
    assert allowed["status"] == "PASS"
    assert allowed["production_closed"] is False
