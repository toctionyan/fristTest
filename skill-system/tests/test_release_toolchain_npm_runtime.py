from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts" / "release_toolchain_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "release_toolchain_contract_npm_runtime_regression", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolved_executable_preserves_launcher_symlink_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract = _load_module()
    target = tmp_path / "lib" / "node_modules" / "npm" / "bin" / "npm"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o755)
    launcher = tmp_path / "bin" / "npm"
    launcher.parent.mkdir()
    launcher.symlink_to(target)
    monkeypatch.setattr(contract.shutil, "which", lambda _name: str(launcher))

    resolved = contract._resolved_executable("npm")

    assert resolved == launcher.absolute()
    assert resolved.is_symlink()
    assert resolved.resolve() == target.resolve()


def test_npm_installation_identity_reads_locked_package_metadata_without_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract = _load_module()
    npm_root = tmp_path / "lib" / "node_modules" / "npm"
    target = npm_root / "bin" / "npm"
    target.parent.mkdir(parents=True)
    target.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    target.chmod(0o755)
    package_json = npm_root / "package.json"
    package_json.write_text(
        json.dumps({"name": "npm", "version": "11.16.0"}), encoding="utf-8"
    )
    launcher = tmp_path / "bin" / "npm"
    launcher.parent.mkdir()
    launcher.symlink_to(target)

    def must_not_run(*args, **kwargs):
        raise AssertionError("npm version identity must not execute the npm launcher")

    monkeypatch.setattr(contract.subprocess, "run", must_not_run)
    identity = contract._npm_installation_identity(launcher)

    assert identity["version"] == "11.16.0"
    assert identity["package_json_sha256"] == contract._sha256_file(package_json)


def test_npm_installation_identity_fails_closed_on_invalid_metadata(tmp_path: Path) -> None:
    contract = _load_module()
    npm_root = tmp_path / "lib" / "node_modules" / "npm"
    target = npm_root / "bin" / "npm"
    target.parent.mkdir(parents=True)
    target.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    target.chmod(0o755)
    (npm_root / "package.json").write_text(
        json.dumps({"name": "not-npm", "version": "11.16.0"}), encoding="utf-8"
    )

    with pytest.raises(contract.ReleaseToolchainError) as caught:
        contract._npm_installation_identity(target)

    assert caught.value.code == "release_npm_installation_identity_invalid"
    assert caught.value.environment_blocked is True


def test_npm_tree_accepts_valid_tree_json_when_npm_reports_diagnostic_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract = _load_module()
    payload = {
        "name": "frontend",
        "version": "1.0.0",
        "dependencies": {"react": {"version": "19.0.0"}},
        "problems": ["optional peer diagnostic"],
    }

    def fake_run(command, **kwargs):
        assert command[-4:] == ["ls", "--all", "--json"]
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=json.dumps(payload),
            stderr="npm error code ELSPROBLEMS\n",
        )

    monkeypatch.setattr(contract.subprocess, "run", fake_run)
    result = contract._npm_tree_digest(tmp_path, Path("/toolchain/bin/npm"))
    expected = contract.hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert result == {"dependency_tree_sha256": expected}


def test_npm_tree_fails_closed_when_nonzero_exit_has_no_dependency_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract = _load_module()

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=json.dumps({"error": {"code": "EUNKNOWN"}}),
            stderr="npm fatal error\n",
        )

    monkeypatch.setattr(contract.subprocess, "run", fake_run)
    with pytest.raises(contract.ReleaseToolchainError) as caught:
        contract._npm_tree_digest(tmp_path, Path("/toolchain/bin/npm"))

    assert caught.value.code == "release_npm_environment_invalid"
    assert caught.value.environment_blocked is True


def test_npm_tree_fails_closed_when_output_is_not_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract = _load_module()

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="not-json",
            stderr="npm fatal error\n",
        )

    monkeypatch.setattr(contract.subprocess, "run", fake_run)
    with pytest.raises(contract.ReleaseToolchainError) as caught:
        contract._npm_tree_digest(tmp_path, Path("/toolchain/bin/npm"))

    assert caught.value.code == "release_npm_environment_invalid"
    assert caught.value.environment_blocked is True
