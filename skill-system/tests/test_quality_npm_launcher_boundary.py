from __future__ import annotations

from pathlib import Path
import sys

import pytest


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPTS = WORKSPACE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from quality_control import common as COMMON  # noqa: E402


def test_system_npm_launcher_boundary_is_not_symlink_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    npm_root = tmp_path / "lib" / "node_modules" / "npm"
    internal = npm_root / "bin" / "npm-cli.js"
    internal.parent.mkdir(parents=True)
    internal.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    internal.chmod(0o755)

    node_bin = tmp_path / "node" / "bin"
    node_bin.mkdir(parents=True)
    node = node_bin / "node"
    node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    node.chmod(0o755)
    launcher = node_bin / "npm"
    launcher.symlink_to(internal)

    monkeypatch.setattr(
        COMMON.shutil,
        "which",
        lambda name: str(launcher) if name == "npm" else None,
    )

    selected = COMMON._npm_executable(tmp_path)

    assert selected == launcher.absolute()
    assert selected is not None
    assert selected.is_symlink()
    assert selected.parent == node_bin
    assert selected.resolve() == internal.resolve()


def test_managed_npm_launcher_boundary_remains_in_node_bin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(COMMON.shutil, "which", lambda _name: None)

    runtime = tmp_path / ".quality" / "tools" / "node-24.18.0"
    node_bin = runtime / "bin"
    internal = runtime / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    node_bin.mkdir(parents=True)
    internal.parent.mkdir(parents=True)

    node = node_bin / "node"
    node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    node.chmod(0o755)
    internal.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    internal.chmod(0o755)
    launcher = node_bin / "npm"
    launcher.symlink_to(internal)

    selected = COMMON._npm_executable(tmp_path)

    assert selected == launcher.absolute()
    assert selected is not None
    assert selected.is_symlink()
    assert selected.parent == node_bin
    assert selected.resolve() == internal.resolve()
