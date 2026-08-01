#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


contract = ROOT / "scripts/release_toolchain_contract.py"
text = contract.read_text(encoding="utf-8")
marker = '''def _python_environment_digest(python: Path, *, cwd: Path) -> dict[str, Any]:
'''
helper = '''def _npm_installation_identity(npm: Path) -> dict[str, Any]:
    """Read npm identity from its installed package instead of executing npm.

    The release already hashes the resolved launcher.  Reading and hashing the
    npm package metadata and installation tree avoids making provenance depend
    on npm's environment-sensitive startup diagnostics while strengthening the
    proof to cover the implementation behind that launcher.
    """

    resolved = Path(npm).resolve()
    package_json: Path | None = None
    payload: dict[str, Any] | None = None
    for parent in resolved.parents[:6]:
        candidate = parent / "package.json"
        if not candidate.is_file():
            continue
        try:
            candidate_payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(candidate_payload, dict) and str(candidate_payload.get("name") or "") == "npm":
            package_json = candidate
            payload = candidate_payload
            break
    if package_json is None or payload is None:
        raise ReleaseToolchainError(
            "release_npm_installation_metadata_missing",
            f"resolved npm installation has no valid npm package.json: {resolved}",
            environment_blocked=True,
        )
    version = str(payload.get("version") or "").strip()
    if not re.fullmatch(r"[0-9]+(?:\\.[0-9]+){2}(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ReleaseToolchainError(
            "release_npm_installation_version_invalid",
            f"resolved npm package has an invalid version: {version!r}",
            environment_blocked=True,
        )
    package_root = package_json.parent
    return {
        "version": version,
        "package_json_sha256": _sha256_file(package_json),
        **_tree_digest(package_root),
    }


'''
text = replace_once(text, marker, helper + marker, label="insert npm installation identity")
text = replace_once(
    text,
    '    actual_npm = _run([str(npm), "--version"], cwd=workspace)\n',
    '    npm_installation = _npm_installation_identity(npm)\n    actual_npm = str(npm_installation["version"])\n',
    label="replace npm CLI version probe",
)
text = replace_once(
    text,
    '        "docker": {\n            "client_version": docker_client_version,\n',
    '        "npm_installation": npm_installation,\n        "docker": {\n            "client_version": docker_client_version,\n',
    label="record npm installation identity",
)
contract.write_text(text, encoding="utf-8")


test = ROOT / "services/agent-service/tests/runtime/test_release_toolchain_npm_version_provenance.py"
test.write_text(
    '''from __future__ import annotations

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
    launcher.write_text("#!/bin/sh\\nexit 1\\n", encoding="utf-8")
    launcher.chmod(0o755)
    (root / "package.json").write_text(
        json.dumps({"name": "npm", "version": version}),
        encoding="utf-8",
    )
    (root / "index.js").write_text("module.exports = 'npm';\\n", encoding="utf-8")
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

    (root / "index.js").write_text("module.exports = 'changed';\\n", encoding="utf-8")
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
    launcher.write_text("#!/bin/sh\\nexit 1\\n", encoding="utf-8")

    with pytest.raises(module.ReleaseToolchainError) as exc_info:
        module._npm_installation_identity(launcher)

    assert exc_info.value.code == "release_npm_installation_metadata_missing"
    assert exc_info.value.environment_blocked is True
''',
    encoding="utf-8",
)
