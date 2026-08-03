from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil

import pytest


WORKSPACE = Path(__file__).resolve().parents[2]
RUNNER_SCRIPT = WORKSPACE / "scripts" / "run_wp08_certification.py"
RESUME_SCRIPT = WORKSPACE / "scripts" / "prepare_wp08_resume.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = _load("wp08_runner_for_resume", RUNNER_SCRIPT)
RESUME = _load("wp08_resume", RESUME_SCRIPT)
TOOLCHAIN = _load("release_toolchain_for_resume_test", WORKSPACE / "scripts" / "release_toolchain_contract.py")
RUN_IDENTITY = _load("release_run_identity_for_resume_test", WORKSPACE / "scripts" / "release_run_identity.py")


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    lock = json.loads((WORKSPACE / "deployment" / "ci" / "release-toolchain-lock.json").read_text(encoding="utf-8"))
    relatives = [
        "scripts/run_wp08_certification.py",
        "scripts/prepare_wp08_resume.py",
        "scripts/run_managed_quality_integration.py",
        ".github/workflows/wp08-certification.yml",
        ".github/workflows/release.yml",
        "deployment/ci/release-toolchain-lock.json",
        *[str(item) for item in lock.get("locked_source_files", [])],
    ]
    for relative in relatives:
        source = WORKSPACE / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (root / "release").mkdir(parents=True)
    (root / "VERSION").write_text("test\n", encoding="utf-8")
    (root / "release" / "MANIFEST.json").write_text("{}\n", encoding="utf-8")
    (root / "PHASE_CANDIDATE_MANIFEST.json").write_text("{}\n", encoding="utf-8")
    return root


def _config(root: Path, *, pass_counter: Path) -> Path:
    retry = (
        "import json,os,sys; "
        "ok=os.getenv('RETRY_PASS')=='1'; "
        "print(json.dumps({'status':'PASS' if ok else 'BLOCKED_BY_ENVIRONMENT'})); "
        "sys.exit(0 if ok else 78)"
    )
    passed = (
        "from pathlib import Path; import json; "
        f"p=Path({str(pass_counter)!r}); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); "
        "print(json.dumps({'status':'PASS'}))"
    )
    path = root / "deployment" / "ci" / "wp08-certification-batches.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "contract": "wp08-certification-batches@1",
                "batches": [
                    {"id": "already-pass", "timeout_seconds": 10, "command": ["{python}", "-c", passed]},
                    {"id": "retry", "timeout_seconds": 10, "command": ["{python}", "-c", retry]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _prior_artifact(tmp_path: Path):
    root = _workspace(tmp_path)
    counter = tmp_path / "pass-counter"
    config = _config(root, pass_counter=counter)
    prior_evidence = tmp_path / "prior" / "evidence"
    prior_state = tmp_path / "prior" / "state" / "wp08-state.json"
    state, code = RUNNER.run_certification(
        workspace=root,
        config_path=config,
        evidence_dir=prior_evidence,
        state_file=prior_state,
        resume=False,
        environment={},
    )
    assert code == 78
    assert state["batches"]["already-pass"]["status"] == "PASS"
    assert state["batches"]["retry"]["status"] == "BLOCKED_BY_ENVIRONMENT"

    artifact = tmp_path / "artifact"
    shutil.copytree(prior_evidence, artifact / "wp08-certification-evidence")
    (artifact / "wp08-certification-state").mkdir(parents=True)
    shutil.copy2(prior_state, artifact / "wp08-certification-state" / "wp08-state.json")
    commit = "a" * 40
    run_identity = {
        "contract": "release-run-identity@1",
        "status": "PASS",
        "run_id": "12345",
        "run_attempt": "2",
        "repository": "owner/repo",
        "commit_sha": commit,
    }
    run_identity["run_identity_fingerprint_sha256"] = RUN_IDENTITY._canonical_sha256(run_identity)
    toolchain = {
        "contract": "release-toolchain-provenance@1",
        "status": "PASS",
        "ci_run_identity": run_identity,
        "runner": "ubuntu-24.04",
        "versions": {},
        "executables": {},
        "static_contract": TOOLCHAIN.validate_static_contract(root),
        "python_environments": {},
        "frontend_environment": {},
    }
    toolchain["toolchain_fingerprint_sha256"] = TOOLCHAIN._canonical_sha256(toolchain)
    (artifact / "wp08-toolchain.json").write_text(json.dumps(toolchain), encoding="utf-8")
    return root, config, counter, artifact, commit


def _prepare(tmp_path: Path, root: Path, artifact: Path, commit: str):
    output = tmp_path / "resume-provenance.json"
    target_evidence = tmp_path / "current" / "evidence"
    target_state = tmp_path / "current" / "state" / "wp08-state.json"
    result = RESUME.prepare_resume(
        workspace=root,
        artifact_root=artifact,
        target_evidence_dir=target_evidence,
        target_state_file=target_state,
        expected_run_id="12345",
        expected_run_attempt="2",
        expected_repository="owner/repo",
        expected_commit_sha=commit,
        output=output,
    )
    return result, target_evidence, target_state


def test_cross_run_resume_skips_pass_and_retries_only_incomplete_batch(tmp_path: Path) -> None:
    root, config, counter, artifact, commit = _prior_artifact(tmp_path)
    result, target_evidence, target_state = _prepare(tmp_path, root, artifact, commit)
    assert result["status"] == "PASS"
    assert result["passed_batches_skipped_on_resume"] == ["already-pass"]
    assert result["batches_to_retry"] == ["retry"]
    restored = json.loads(target_state.read_text(encoding="utf-8"))
    assert restored["batches"]["already-pass"]["stdout_log"].startswith(str(target_evidence))

    final, code = RUNNER.run_certification(
        workspace=root,
        config_path=config,
        evidence_dir=target_evidence,
        state_file=target_state,
        resume=True,
        environment={"RETRY_PASS": "1"},
    )
    assert code == 0
    assert final["status"] == "PASS"
    assert final["batches"]["already-pass"]["resume_action"] == "SKIPPED_ALREADY_PASS"
    assert final["batches"]["retry"]["status"] == "PASS"
    assert counter.read_text(encoding="utf-8") == "1"


def test_resume_rejects_wrong_run_identity(tmp_path: Path) -> None:
    root, _config_path, _counter, artifact, commit = _prior_artifact(tmp_path)
    with pytest.raises(RESUME.ResumeInputError) as caught:
        RESUME.prepare_resume(
            workspace=root,
            artifact_root=artifact,
            target_evidence_dir=tmp_path / "evidence",
            target_state_file=tmp_path / "state.json",
            expected_run_id="99999",
            expected_run_attempt="2",
            expected_repository="owner/repo",
            expected_commit_sha=commit,
            output=tmp_path / "result.json",
        )
    assert caught.value.code == "resume_run_identity_mismatch"


def test_resume_rejects_changed_source_identity(tmp_path: Path) -> None:
    root, _config_path, _counter, artifact, commit = _prior_artifact(tmp_path)
    (root / "VERSION").write_text("changed\n", encoding="utf-8")
    with pytest.raises(RESUME.ResumeInputError) as caught:
        _prepare(tmp_path, root, artifact, commit)
    assert caught.value.code == "resume_source_identity_mismatch"


def test_resume_rejects_tampered_toolchain_artifact(tmp_path: Path) -> None:
    root, _config_path, _counter, artifact, commit = _prior_artifact(tmp_path)
    toolchain_path = artifact / "wp08-toolchain.json"
    payload = json.loads(toolchain_path.read_text(encoding="utf-8"))
    payload["runner"] = "tampered-runner"
    toolchain_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RESUME.ResumeInputError) as caught:
        _prepare(tmp_path, root, artifact, commit)
    assert caught.value.code == "resume_toolchain_contract_invalid"


def test_resume_rejects_symlinked_artifact_content(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    root, _config_path, _counter, artifact, commit = _prior_artifact(tmp_path)
    link = artifact / "escape-link"
    try:
        link.symlink_to(tmp_path / "outside")
    except OSError:
        pytest.skip("symlink creation not permitted")
    with pytest.raises(RESUME.ResumeInputError) as caught:
        _prepare(tmp_path, root, artifact, commit)
    assert caught.value.code == "resume_symlink_forbidden"


def test_source_fingerprint_covers_resume_authority_assets(tmp_path: Path) -> None:
    root, config, _counter, _artifact, _commit = _prior_artifact(tmp_path)
    before = RUNNER._source_fingerprint(root, config)
    path = root / "scripts" / "prepare_wp08_resume.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    after = RUNNER._source_fingerprint(root, config)
    assert before != after


def _wp08_identity_env(commit: str) -> dict[str, str]:
    return {
        "GITHUB_ACTIONS": "true",
        "CI": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REPOSITORY_ID": "123",
        "GITHUB_SHA": commit,
        "GITHUB_WORKFLOW_SHA": commit,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_TYPE": "branch",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_WORKFLOW": "wp08-full-stack-certification",
        "GITHUB_JOB": "certify",
        "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/wp08-certification.yml@refs/heads/main",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_RUN_NUMBER": "77",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_API_URL": "https://api.github.com",
        "PRODUCTION_RELEASE_EXPECTED_EVENT": "workflow_dispatch",
        "PRODUCTION_RELEASE_EXPECTED_WORKFLOW": "wp08-full-stack-certification",
        "PRODUCTION_RELEASE_EXPECTED_WORKFLOW_FILE": ".github/workflows/wp08-certification.yml",
        "PRODUCTION_RELEASE_EXPECTED_JOB": "certify",
        "PRODUCTION_RELEASE_EXPECTED_REF": "refs/heads/main",
    }


def test_run_identity_supports_explicit_locked_wp08_workflow_file(tmp_path: Path) -> None:
    commit = "b" * 40
    payload = RUN_IDENTITY.capture_run_identity(
        tmp_path,
        env=_wp08_identity_env(commit),
        validate_git=False,
    )
    assert payload["workflow_file"] == ".github/workflows/wp08-certification.yml"
    assert payload["workflow"] == "wp08-full-stack-certification"
    assert payload["job"] == "certify"


def test_run_identity_rejects_workflow_ref_outside_locked_file(tmp_path: Path) -> None:
    commit = "c" * 40
    env = _wp08_identity_env(commit)
    env["GITHUB_WORKFLOW_REF"] = "owner/repo/.github/workflows/release.yml@refs/heads/main"
    with pytest.raises(RUN_IDENTITY.ReleaseRunIdentityError) as caught:
        RUN_IDENTITY.capture_run_identity(tmp_path, env=env, validate_git=False)
    assert caught.value.code == "release_workflow_ref_mismatch"
