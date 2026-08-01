#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


contract_path = ROOT / "scripts/release_toolchain_contract.py"
text = contract_path.read_text(encoding="utf-8")
old = '''def _npm_tree_digest(frontend: Path) -> dict[str, Any]:
    stdout = _run(["npm", "ls", "--all", "--json"], cwd=frontend)
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise ReleaseToolchainError("release_npm_environment_invalid", "npm dependency tree is invalid")
    return {"dependency_tree_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
'''
new = '''def _canonical_npm_dependency_tree(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep dependency identity while discarding npm diagnostic noise.

    ``npm ls`` may return exit code 1 and add fields such as ``problems`` or
    ``error`` for peer/optional dependency diagnostics even when the installed
    tree is executable.  The byte-level ``node_modules`` digest remains the
    authoritative mutation proof; this semantic tree records package names,
    versions and dependency edges only.
    """

    def normalize_node(name: str, node: Any) -> dict[str, Any]:
        if not isinstance(node, Mapping):
            raise ReleaseToolchainError(
                "release_npm_environment_invalid",
                f"npm dependency node is invalid: {name}",
            )
        dependencies = node.get("dependencies") or {}
        if not isinstance(dependencies, Mapping):
            raise ReleaseToolchainError(
                "release_npm_environment_invalid",
                f"npm dependency children are invalid: {name}",
            )
        return {
            "name": str(name),
            "version": str(node.get("version") or ""),
            "dependencies": [
                normalize_node(str(child_name), child_node)
                for child_name, child_node in sorted(
                    dependencies.items(), key=lambda item: str(item[0])
                )
            ],
        }

    root_name = str(payload.get("name") or "").strip()
    if not root_name:
        raise ReleaseToolchainError(
            "release_npm_environment_invalid",
            "npm dependency tree has no root package name",
        )
    return normalize_node(root_name, payload)


def _npm_tree_digest(frontend: Path) -> dict[str, Any]:
    npm = _resolved_executable("npm")
    try:
        completed = subprocess.run(
            [str(npm), "ls", "--all", "--offline", "--json"],
            cwd=frontend,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseToolchainError(
            "release_toolchain_command_unavailable",
            f"toolchain command failed: {npm}",
            environment_blocked=True,
        ) from exc

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ReleaseToolchainError(
            "release_npm_environment_invalid",
            "npm dependency tree did not emit valid JSON",
            environment_blocked=True,
        ) from exc
    if completed.returncode not in (0, 1):
        raise ReleaseToolchainError(
            "release_toolchain_command_failed",
            f"toolchain command returned {completed.returncode}: {npm}",
            environment_blocked=True,
        )
    if not isinstance(payload, dict):
        raise ReleaseToolchainError(
            "release_npm_environment_invalid",
            "npm dependency tree is invalid",
            environment_blocked=True,
        )

    canonical = _canonical_npm_dependency_tree(payload)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {"dependency_tree_sha256": hashlib.sha256(encoded).hexdigest()}
'''
text = replace_once(text, old, new, label="replace npm dependency provenance")
contract_path.write_text(text, encoding="utf-8")


test_path = ROOT / "services/agent-service/tests/runtime/test_release_toolchain_npm_diagnostics.py"
test_path.write_text(
    '''from __future__ import annotations

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
''',
    encoding="utf-8",
)
