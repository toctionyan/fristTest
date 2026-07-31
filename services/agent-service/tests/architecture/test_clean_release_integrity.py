from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

from tests.support.paths import workspace_root


def _load_release_artifact():
    root = workspace_root(__file__)
    scripts = root / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    path = scripts / "release_artifact.py"
    spec = importlib.util.spec_from_file_location("release_artifact_test_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_stage(tmp_path: Path) -> Path:
    stage = tmp_path / "workspace"
    (stage / "architecture-skill").mkdir(parents=True)
    (stage / "services/agent-service/frontend/dist/assets").mkdir(parents=True)
    (stage / "services/agent-service/frontend/src").mkdir(parents=True)
    (stage / "VERSION").write_text("20.5.0\n", encoding="utf-8")
    (stage / "README.md").write_text("workspace\n", encoding="utf-8")
    (stage / "architecture-skill/manifest.json").write_text(
        json.dumps({"name": "skill", "version": "5.10.0"}), encoding="utf-8"
    )
    (stage / "services/agent-service/frontend/src/main.jsx").write_text("export default 1;\n", encoding="utf-8")
    (stage / "services/agent-service/frontend/dist/index.html").write_text("<div id='root'></div>\n", encoding="utf-8")
    (stage / "services/agent-service/frontend/dist/assets/app.js").write_text("console.log('ok');\n", encoding="utf-8")
    return stage


def test_clean_release_metadata_and_zip_round_trip(tmp_path: Path) -> None:
    release = _load_release_artifact()
    stage = _fake_stage(tmp_path)
    release.write_release_metadata(
        stage,
        workspace=stage,
        frontend_build_mode="test-rebuild",
        evidence_summary=None,
    )
    assert release.verify_release_tree(stage)["status"] == "PASS"

    output = tmp_path / "artifact.zip"
    release.create_zip(stage, output, root_name="artifact")
    assert release.verify_zip(output)["status"] == "PASS"


def test_clean_release_rejects_payload_tampering(tmp_path: Path) -> None:
    release = _load_release_artifact()
    stage = _fake_stage(tmp_path)
    release.write_release_metadata(
        stage,
        workspace=stage,
        frontend_build_mode="test-rebuild",
        evidence_summary=None,
    )
    (stage / "README.md").write_text("tampered after signing\n", encoding="utf-8")
    result = release.verify_release_tree(stage)
    assert result["status"] == "FAIL"
    assert "sha256_mismatch:README.md" in result["errors"]
    assert "manifest_source_snapshot_fingerprint_mismatch" in result["errors"]


def test_clean_release_rejects_unlisted_or_forbidden_files(tmp_path: Path) -> None:
    release = _load_release_artifact()
    stage = _fake_stage(tmp_path)
    release.write_release_metadata(
        stage,
        workspace=stage,
        frontend_build_mode="test-rebuild",
        evidence_summary=None,
    )
    (stage / ".env").write_text("SECRET=must-not-ship\n", encoding="utf-8")
    result = release.verify_release_tree(stage)
    assert result["status"] == "FAIL"
    assert "forbidden_file:.env" in result["errors"]


def test_clean_release_rejects_unknown_top_level_even_when_metadata_is_regenerated(tmp_path: Path) -> None:
    release = _load_release_artifact()
    stage = _fake_stage(tmp_path)
    (stage / "unexpected-plugin/payload.txt").parent.mkdir(parents=True)
    (stage / "unexpected-plugin/payload.txt").write_text("must not ship\n", encoding="utf-8")
    release.write_release_metadata(
        stage,
        workspace=stage,
        frontend_build_mode="test-rebuild",
        evidence_summary=None,
    )
    result = release.verify_release_tree(stage)
    assert result["status"] == "FAIL"
    assert "unexpected_top_level:unexpected-plugin" in result["errors"]


def test_zip_verifier_rejects_duplicates_backslashes_and_symlinks(tmp_path: Path) -> None:
    release = _load_release_artifact()
    output = tmp_path / "malicious.zip"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("artifact/VERSION", "20.5.0\n")
        archive.writestr("artifact/VERSION", "forged\n")
        archive.writestr("artifact\\..\\escape.txt", "escape\n")
        link = zipfile.ZipInfo("artifact/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "VERSION")

    result = release.verify_zip(output)
    assert result["status"] == "FAIL"
    assert "duplicate_zip_entry" in result["errors"]
    assert "unsafe_zip_path" in result["errors"]
    assert "zip_symlink_not_allowed" in result["errors"]


def test_source_copy_excludes_previous_dist_runtime_and_secrets(tmp_path: Path) -> None:
    release = _load_release_artifact()
    source = tmp_path / "source"
    stage = tmp_path / "stage"
    (source / "services/agent-service/frontend/dist").mkdir(parents=True)
    (source / "services/agent-service/frontend/src").mkdir(parents=True)
    (source / "services/agent-service/runtime").mkdir(parents=True)
    (source / "architecture-skill").mkdir(parents=True)
    (source / "VERSION").write_text("20.5.0\n", encoding="utf-8")
    (source / "services/agent-service/frontend/src/main.jsx").write_text("ok\n", encoding="utf-8")
    (source / "services/agent-service/frontend/dist/stale.js").write_text("stale\n", encoding="utf-8")
    (source / "services/agent-service/runtime/state.db").write_text("db\n", encoding="utf-8")
    (source / "services/agent-service/.env").write_text("SECRET=x\n", encoding="utf-8")
    (source / "architecture-skill/manifest.json").write_text("{}\n", encoding="utf-8")
    stage.mkdir()

    copied = release.copy_release_sources(source, stage)
    assert "services/agent-service/frontend/src/main.jsx" in copied
    assert not (stage / "services/agent-service/frontend/dist/stale.js").exists()
    assert not (stage / "services/agent-service/runtime/state.db").exists()
    assert not (stage / "services/agent-service/.env").exists()



def test_source_copy_includes_portable_skill_control_plane(tmp_path: Path) -> None:
    release = _load_release_artifact()
    source = tmp_path / "source"
    stage = tmp_path / "stage"
    required = {
        "AGENTS.md": "agents\n",
        "CLAUDE.md": "claude\n",
        "skillctl.py": "print('ok')\n",
        "skill-system/core/constitution.md": "constitution\n",
        ".agents/skills/change-scope/SKILL.md": "skill\n",
        ".claude/settings.json": "{}\n",
        ".codex/config.toml": "model = 'test'\n",
    }
    for name, content in required.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    stage.mkdir()

    copied = release.copy_release_sources(source, stage)

    assert set(required).issubset(copied)
    for name in required:
        assert (stage / name).is_file()

def test_source_copy_keeps_runtime_named_source_and_test_packages(tmp_path: Path) -> None:
    release = _load_release_artifact()
    source = tmp_path / "source"
    stage = tmp_path / "stage"
    production_runtime = source / "services/agent-service/src/agent_core/runtime/engine.py"
    runtime_test = source / "services/agent-service/tests/runtime/test_engine.py"
    generated_runtime = source / "services/agent-service/runtime/state.json"
    for path, content in (
        (production_runtime, "VALUE = 1\n"),
        (runtime_test, "def test_engine(): assert True\n"),
        (generated_runtime, "{}\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    stage.mkdir()

    copied = release.copy_release_sources(source, stage)

    assert production_runtime.relative_to(source).as_posix() in copied
    assert runtime_test.relative_to(source).as_posix() in copied
    assert not (stage / generated_runtime.relative_to(source)).exists()


def test_workspace_snapshot_tracks_runtime_named_source_and_tests_but_not_artifacts(
    tmp_path: Path,
) -> None:
    quality = _load_quality_loop()
    production = tmp_path / "services/agent-service/src/agent_core/runtime/engine.py"
    runtime_test = tmp_path / "services/agent-service/tests/runtime/test_engine.py"
    generated = tmp_path / "services/agent-service/runtime/state.json"
    business_generated = tmp_path / "services/business-service/runtime/worker.json"
    for path, content in (
        (production, "VALUE = 1\n"),
        (runtime_test, "def test_engine(): assert True\n"),
        (generated, "{\"state\": 1}\n"),
        (business_generated, "{\"state\": 1}\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    before = quality.workspace_snapshot(tmp_path)
    assert production.relative_to(tmp_path).as_posix() in before["files"]
    assert runtime_test.relative_to(tmp_path).as_posix() in before["files"]
    assert generated.relative_to(tmp_path).as_posix() not in before["files"]
    assert business_generated.relative_to(tmp_path).as_posix() not in before["files"]

    production.write_text("VALUE = 2\n", encoding="utf-8")
    assert quality.workspace_snapshot(tmp_path)["fingerprint"] != before["fingerprint"]

    stable = quality.workspace_snapshot(tmp_path)["fingerprint"]
    generated.write_text("{\"state\": 2}\n", encoding="utf-8")
    assert quality.workspace_snapshot(tmp_path)["fingerprint"] == stable


def test_every_clean_release_source_is_bound_by_workspace_snapshot(tmp_path: Path) -> None:
    release = _load_release_artifact()
    quality = _load_quality_loop()
    source = tmp_path / "source"
    stage = tmp_path / "stage"
    files = {
        "VERSION": "20.5.0\n",
        "services/agent-service/src/agent_core/runtime/engine.py": "VALUE = 1\n",
        "services/agent-service/tests/runtime/test_engine.py": "def test_engine(): assert True\n",
        "services/business-service/business_service/database.py": "VALUE = 1\n",
        "services/agent-service/runtime/state.db": "generated\n",
    }
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    stage.mkdir()

    copied = release.copy_release_sources(source, stage)
    snapshot_files = quality.workspace_snapshot(source)["files"]

    assert copied
    assert set(copied).issubset(snapshot_files)
    assert "services/agent-service/runtime/state.db" not in copied


def test_release_semantic_self_checks_fail_closed(tmp_path: Path) -> None:
    release = _load_release_artifact()
    stage = tmp_path / "stage"
    skill_script = stage / "architecture-skill/scripts/verify_skill_package.py"
    version_script = stage / "scripts/verify_version_consistency.py"
    architecture_script = stage / "scripts/verify_architecture.py"
    scripts = (
        (skill_script, 'print(\'{"status":"PASS"}\')\n'),
        (version_script, 'print(\'{"status":"PASS"}\')\n'),
        (architecture_script, 'print(\'{"status":"FAIL"}\')\nraise SystemExit(1)\n'),
    )
    for path, body in scripts:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    try:
        release.run_release_self_checks(stage)
    except RuntimeError as exc:
        assert "architecture-convergence" in str(exc)
    else:
        raise AssertionError("a failed staged architecture check must block clean-release")


def _load_quality_loop():
    root = workspace_root(__file__)
    scripts = root / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    path = scripts / "quality_loop.py"
    spec = importlib.util.spec_from_file_location("quality_loop_release_test_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _signed_complete_evidence(
    tmp_path: Path, *, rerun_from: str | None = None, mode: str = "quick"
) -> tuple[Path, Path]:
    release = _load_release_artifact()
    quality = _load_quality_loop()
    workspace = tmp_path / "source"
    evidence = workspace / ".quality/evidence/final"
    (workspace / "governance").mkdir(parents=True)
    evidence.mkdir(parents=True)
    (workspace / "VERSION").write_text("20.5.0\n", encoding="utf-8")
    (workspace / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    policy = workspace / "governance/quality-loop-policy.json"
    policy.write_text('{"version":"20.5.0"}\n', encoding="utf-8")
    claim_payload = {
        "schema_version": 1,
        "target_id": "test",
        "claims": [
            {
                "id": "TEST-RELEASE-001",
                "statement": "The fixture quality evidence is complete.",
                "risk": "P2",
                "required_mode": mode,
                "evidence_kind": "counterexample",
                "required_gates": ["architecture", "python-tests"],
                "evidence_refs": ["test-fixture"],
            }
        ],
    }
    claim_text = json.dumps(claim_payload, ensure_ascii=False, indent=2) + "\n"
    (evidence / "claim-manifest.json").write_text(claim_text, encoding="utf-8")
    workspace_claim = workspace / "governance/claims/test.json"
    workspace_claim.parent.mkdir(parents=True, exist_ok=True)
    workspace_claim.write_text(claim_text, encoding="utf-8")
    snapshot = quality.workspace_snapshot(workspace, ignored_roots=(evidence,))
    (evidence / "workspace-snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    claim_fingerprint = release.sha256_text(
        json.dumps(claim_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    gate_ids = ["architecture", "python-tests"]
    summary = {
        "schema_version": quality.EVIDENCE_SCHEMA_VERSION,
        "workspace_version": "20.5.0",
        "mode": mode,
        "run_kind": "verification",
        "decision": "PASS",
        "loop_status": "CONVERGED",
        "generated_at": "2026-07-12T00:00:00+00:00",
        "evidence_dir": str(evidence),
        "target": str(workspace / "target.md"),
        "target_identity": {
            "id": "test",
            "change_ref": "test-ref",
            "context": "ci",
            "fingerprint": "f" * 64,
            "claim_manifest_fingerprint": claim_fingerprint,
        },
        "target_minimum_mode_declared": mode,
        "target_minimum_mode_derived": mode,
        "target_minimum_mode_effective": mode,
        "replan_predecessor": None,
        "claim_manifest": "governance/claims/test.json",
        "claim_manifest_fingerprint": claim_fingerprint,
        "claim_manifest_evidence_file": "claim-manifest.json",
        "claim_results": [
            {
                "id": "TEST-RELEASE-001",
                "statement": "The fixture quality evidence is complete.",
                "risk": "P2",
                "required_mode": mode,
                "evidence_kind": "counterexample",
                "required_gates": gate_ids,
                "evidence_refs": ["test-fixture"],
                "gate_statuses": {gate_id: "PASS" for gate_id in gate_ids},
                "status": "VERIFIED",
            }
        ],
        "unverified_claim_ids": [],
        "policy_fingerprint": release.sha256_file(policy),
        "rerun_from": rerun_from,
        "prior_evidence": None,
        "reused_prerequisites": [],
        "missing_prerequisites": [],
        "workspace_snapshot_start_fingerprint": snapshot["fingerprint"],
        "workspace_snapshot_fingerprint": snapshot["fingerprint"],
        "workspace_snapshot_file": "workspace-snapshot.json",
        "selected_gate_ids": gate_ids,
        "required_gate_ids": gate_ids,
        "gate_contract_fingerprints": {gate_id: "a" * 64 for gate_id in gate_ids},
        "completion_eligible": True,
        "ci_run_identity_fingerprint_sha256": "f" * 64,
        "evidence_attestation_file": "evidence-attestation.json",
        "results": [{"id": gate_id, "status": "PASS"} for gate_id in gate_ids],
    }
    (evidence / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (evidence / "repair-plan.json").write_text("{}\n", encoding="utf-8")
    quality._write_evidence_attestation(workspace, evidence)
    return workspace, evidence


def test_release_accepts_only_attested_complete_current_evidence(tmp_path: Path) -> None:
    release = _load_release_artifact()
    workspace, evidence = _signed_complete_evidence(tmp_path)
    summary = release.load_evidence_summary(workspace, evidence)
    assert summary is not None
    assert summary["completion_eligible"] is True


def test_release_embeds_verifiable_provenance_and_binds_evidence_bundle(
    tmp_path: Path, monkeypatch
) -> None:
    release = _load_release_artifact()
    workspace, evidence_dir = _signed_complete_evidence(tmp_path)
    evidence = release.load_evidence_summary(workspace, evidence_dir)
    assert evidence is not None
    bundle = release.create_evidence_bundle(
        evidence_dir, tmp_path / "protected-quality-evidence.zip"
    )
    evidence["_release_provenance"].update(
        {
            "evidence_bundle_filename": bundle["filename"],
            "evidence_bundle_sha256": bundle["sha256"],
        }
    )
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    stage = _fake_stage(tmp_path / "artifact")

    manifest = release.write_release_metadata(
        stage,
        workspace=workspace,
        frontend_build_mode="test-rebuild",
        evidence_summary=evidence,
        certification_level="candidate",
    )

    quality = manifest["quality_evidence"]
    assert quality["evidence_bundle_sha256"] == release.sha256_file(
        Path(bundle["path"])
    )
    assert quality["commit_sha"] == "a" * 40
    assert quality["workflow_run_id"] == "12345"
    assert (stage / "release/provenance/evidence-attestation.json").is_file()
    assert release.verify_release_tree(stage)["status"] == "PASS"


def test_protected_release_requires_release_mode_ci_identity_and_npm_ci(
    tmp_path: Path, monkeypatch
) -> None:
    release = _load_release_artifact()
    monkeypatch.setenv("PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT", "f" * 64)
    workspace, evidence_dir = _signed_complete_evidence(tmp_path, mode="release")
    evidence = release.load_evidence_summary(
        workspace, evidence_dir, required_mode="release"
    )
    assert evidence is not None
    bundle = release.create_evidence_bundle(evidence_dir, tmp_path / "evidence.zip")
    evidence["_release_provenance"].update(
        {
            "evidence_bundle_filename": bundle["filename"],
            "evidence_bundle_sha256": bundle["sha256"],
        }
    )
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    stage = _fake_stage(tmp_path / "protected")
    release.write_release_metadata(
        stage,
        workspace=workspace,
        frontend_build_mode="npm-ci-stage",
        evidence_summary=evidence,
        certification_level="protected-release",
    )
    assert release.verify_release_tree(stage)["status"] == "PASS"

    manifest_path = stage / "release/MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["frontend_build"]["mode"] = "locked-source-node_modules"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = release.verify_release_tree(stage)
    assert "protected_release_requires_clean_npm_ci_build" in result["errors"]


def test_release_rejects_evidence_edited_after_attestation(tmp_path: Path) -> None:
    release = _load_release_artifact()
    workspace, evidence = _signed_complete_evidence(tmp_path)
    summary_path = evidence / "run-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["mode"] = "release"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    try:
        release.load_evidence_summary(workspace, evidence)
    except RuntimeError as exc:
        assert "attestation failed" in str(exc)
    else:
        raise AssertionError("tampered evidence must not authorize release")


def test_release_rejects_source_drift_after_signed_pass(tmp_path: Path) -> None:
    release = _load_release_artifact()
    workspace, evidence = _signed_complete_evidence(tmp_path)
    (workspace / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
    try:
        release.load_evidence_summary(workspace, evidence)
    except RuntimeError as exc:
        assert "source snapshot" in str(exc)
    else:
        raise AssertionError("source drift must invalidate release evidence")


def test_release_rejects_targeted_regression_even_when_signed_pass(tmp_path: Path) -> None:
    release = _load_release_artifact()
    workspace, evidence = _signed_complete_evidence(tmp_path, rerun_from="python-tests")
    try:
        release.load_evidence_summary(workspace, evidence)
    except RuntimeError as exc:
        assert "targeted regression" in str(exc)
    else:
        raise AssertionError("targeted regression evidence must not authorize release")


def test_release_rejects_source_mutation_during_attested_quality_run(tmp_path: Path) -> None:
    release = _load_release_artifact()
    quality = _load_quality_loop()
    workspace, evidence = _signed_complete_evidence(tmp_path)
    summary_path = evidence / "run-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["workspace_snapshot_start_fingerprint"] = "b" * 64
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (evidence / "evidence-attestation.json").unlink()
    quality._write_evidence_attestation(workspace, evidence)

    try:
        release.load_evidence_summary(workspace, evidence)
    except RuntimeError as exc:
        assert "source changed while quality Gates were running" in str(exc)
    else:
        raise AssertionError("a Gate-mutated source tree must not authorize release")


def test_release_verifier_is_observational_without_python_dash_b(tmp_path: Path) -> None:
    release = _load_release_artifact()
    root = workspace_root(__file__)
    stage = _fake_stage(tmp_path)
    scripts = stage / "scripts"
    scripts.mkdir()
    for name in ("verify_release_integrity.py", "release_artifact.py", "source_paths.py"):
        (scripts / name).write_bytes((root / "scripts" / name).read_bytes())
    release.write_release_metadata(
        stage,
        workspace=stage,
        frontend_build_mode="test-rebuild",
        evidence_summary=None,
    )

    result = subprocess.run(
        [sys.executable, str(scripts / "verify_release_integrity.py"), "--workspace-root", str(stage)],
        cwd=stage,
        text=True,
        capture_output=True,
        check=False,
        env={key: value for key, value in __import__("os").environ.items() if key != "PYTHONDONTWRITEBYTECODE"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(stage.rglob("__pycache__"))
    assert release.verify_release_tree(stage)["status"] == "PASS"
