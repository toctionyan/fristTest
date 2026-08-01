from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load_module():
    path = ROOT / "scripts" / "release_toolchain_contract.py"
    spec = importlib.util.spec_from_file_location("release_toolchain_contract_npm_version_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _npm_fixture(tmp_path: Path, *, version: str = "11.16.0") -> tuple[Path, Path]:
    root = tmp_path / "lib" / "node_modules" / "npm"
    launcher = root / "bin" / "npm"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    launcher.chmod(0o755)
    (root / "package.json").write_text(
        json.dumps({"name": "npm", "version": version}),
        encoding="utf-8",
    )
    (root / "index.js").write_text("module.exports = 'npm';\n", encoding="utf-8")
    return launcher, root


def test_npm_version_identity_does_not_execute_environment_sensitive_launcher(
    tmp_path: Path,
) -> None:
    module = _load_module()
    launcher, _root = _npm_fixture(tmp_path)

    identity = module._npm_installation_identity(launcher)

    assert identity["version"] == "11.16.0"
    assert len(identity["package_json_sha256"]) == 64
    assert len(identity["content_tree_sha256"]) == 64
    assert identity["file_count"] >= 3


def test_npm_implementation_change_changes_installation_digest(tmp_path: Path) -> None:
    module = _load_module()
    launcher, root = _npm_fixture(tmp_path)
    before = module._npm_installation_identity(launcher)

    (root / "index.js").write_text("module.exports = 'changed';\n", encoding="utf-8")
    after = module._npm_installation_identity(launcher)

    assert before["version"] == after["version"]
    assert before["content_tree_sha256"] != after["content_tree_sha256"]


def test_npm_invalid_version_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    launcher, _root = _npm_fixture(tmp_path, version="latest")

    with pytest.raises(module.ReleaseToolchainError) as exc_info:
        module._npm_installation_identity(launcher)

    assert exc_info.value.code == "release_npm_installation_version_invalid"
    assert exc_info.value.environment_blocked is True


def test_npm_missing_package_metadata_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    launcher = tmp_path / "bin" / "npm"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    with pytest.raises(module.ReleaseToolchainError) as exc_info:
        module._npm_installation_identity(launcher)

    assert exc_info.value.code == "release_npm_installation_metadata_missing"
    assert exc_info.value.environment_blocked is True
