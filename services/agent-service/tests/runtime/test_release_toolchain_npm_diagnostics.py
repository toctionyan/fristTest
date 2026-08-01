from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load_module():
    path = ROOT / "scripts" / "release_toolchain_contract.py"
    spec = importlib.util.spec_from_file_location("release_toolchain_contract_npm_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tree(*, react_version: str = "19.1.1", diagnostics: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "frontend-fixture",
        "version": "1.0.0",
        "dependencies": {
            "react": {
                "version": react_version,
                "dependencies": {
                    "scheduler": {"version": "0.26.0"},
                },
            },
            "vite": {"version": "6.4.3"},
        },
    }
    if diagnostics:
        payload.update(
            {
                "path": "/tmp/environment-specific-path",
                "problems": ["peer dep diagnostic"],
                "error": {"code": "ELSPROBLEMS", "summary": "diagnostic only"},
            }
        )
        react = payload["dependencies"]["react"]  # type: ignore[index]
        react["invalid"] = True  # type: ignore[index]
        react["path"] = "/tmp/another-path"  # type: ignore[index]
    return payload


def _completed(payload: object, returncode: int) -> subprocess.CompletedProcess[str]:
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(
        args=["npm", "ls"],
        returncode=returncode,
        stdout=stdout,
        stderr="peer/optional dependency diagnostics",
    )


def test_npm_exit_one_with_valid_json_has_same_semantic_digest_as_exit_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_resolved_executable", lambda _name: Path("/locked/npm"))
    results = iter((_completed(_tree(), 0), _completed(_tree(diagnostics=True), 1)))
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: next(results))

    clean = module._npm_tree_digest(tmp_path)
    diagnostic = module._npm_tree_digest(tmp_path)

    assert diagnostic == clean


def test_npm_dependency_version_change_changes_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_resolved_executable", lambda _name: Path("/locked/npm"))
    results = iter((_completed(_tree(), 0), _completed(_tree(react_version="19.2.0"), 0)))
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: next(results))

    before = module._npm_tree_digest(tmp_path)
    after = module._npm_tree_digest(tmp_path)

    assert before != after


def test_npm_invalid_json_is_rejected_even_for_diagnostic_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_resolved_executable", lambda _name: Path("/locked/npm"))
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed("not-json", 1),
    )

    with pytest.raises(module.ReleaseToolchainError) as exc_info:
        module._npm_tree_digest(tmp_path)

    assert exc_info.value.code == "release_npm_environment_invalid"
    assert exc_info.value.environment_blocked is True


def test_npm_exit_above_one_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_resolved_executable", lambda _name: Path("/locked/npm"))
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(_tree(), 2),
    )

    with pytest.raises(module.ReleaseToolchainError) as exc_info:
        module._npm_tree_digest(tmp_path)

    assert exc_info.value.code == "release_toolchain_command_failed"
    assert exc_info.value.environment_blocked is True
