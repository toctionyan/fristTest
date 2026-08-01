#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


quality_loop = ROOT / "scripts" / "quality_loop.py"
text = quality_loop.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''    system_npm = shutil.which("npm")
    if system_npm:
        return Path(system_npm).resolve()
    tools_root = workspace / ".quality" / "tools"
''',
    '''    system_npm = shutil.which("npm")
    if system_npm:
        # Preserve the launcher path. Official Node distributions expose
        # ``bin/npm`` as a symlink into ``lib/node_modules/npm/bin``. Resolving
        # it moves ``npm.parent`` away from the sibling ``node`` executable;
        # _run_shell would then prepend npm's package-internal bin directory to
        # PATH and later gates would discover the non-launcher ``npm`` script.
        return Path(system_npm).absolute()
    tools_root = workspace / ".quality" / "tools"
''',
    label="preserve system npm launcher",
)
quality_loop.write_text(text, encoding="utf-8")


test = ROOT / "services" / "agent-service" / "tests" / "runtime" / "test_quality_loop_npm_launcher_boundary.py"
test.write_text(
    '''from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load_quality_loop():
    path = ROOT / "scripts" / "quality_loop.py"
    spec = importlib.util.spec_from_file_location("quality_loop_npm_launcher_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _node_distribution(root: Path) -> tuple[Path, Path, Path]:
    launcher_dir = root / "bin"
    package_bin = root / "lib" / "node_modules" / "npm" / "bin"
    launcher_dir.mkdir(parents=True)
    package_bin.mkdir(parents=True)
    node = launcher_dir / "node"
    node.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    node.chmod(0o755)
    npm_cli = package_bin / "npm-cli.js"
    npm_cli.write_text("#!/usr/bin/env node\\n", encoding="utf-8")
    npm_cli.chmod(0o755)
    npm_launcher = launcher_dir / "npm"
    npm_launcher.symlink_to(Path("../lib/node_modules/npm/bin/npm-cli.js"))
    # This package-internal executable is what PATH incorrectly selected after
    # the launcher symlink had been resolved by the old implementation.
    internal_npm = package_bin / "npm"
    internal_npm.write_text("#!/bin/sh\\nexit 97\\n", encoding="utf-8")
    internal_npm.chmod(0o755)
    return npm_launcher, node, internal_npm


def test_system_npm_keeps_launcher_directory_with_sibling_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_quality_loop()
    npm_launcher, node, internal_npm = _node_distribution(tmp_path / "node-dist")
    monkeypatch.setattr(module.shutil, "which", lambda name: str(npm_launcher) if name == "npm" else None)

    selected = module._npm_executable(tmp_path)

    assert selected == npm_launcher.absolute()
    assert selected != npm_launcher.resolve()
    assert selected.parent / "node" == node
    assert selected.parent != internal_npm.parent
    quality_path = str(selected.parent) + os.pathsep + str(internal_npm.parent)
    assert quality_path.split(os.pathsep, 1)[0] == str(npm_launcher.parent)


def test_managed_npm_launcher_is_also_not_resolved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_quality_loop()
    workspace = tmp_path
    npm_launcher, node, internal_npm = _node_distribution(
        workspace / ".quality" / "tools" / "node-24.18.0"
    )
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    selected = module._npm_executable(workspace)

    assert selected == npm_launcher.absolute()
    assert selected != npm_launcher.resolve()
    assert selected.parent / "node" == node
    assert selected.parent != internal_npm.parent
''',
    encoding="utf-8",
)
