#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
MANIFEST = ROOT / "PHASE_CANDIDATE_MANIFEST.json"


def replace_exact(path: Path, old: str, new: str, *, expected: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(
            f"{path.relative_to(ROOT)} expected {expected} occurrences, found {count}: {old!r}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_toolchain_contract() -> None:
    path = ROOT / "scripts/release_toolchain_contract.py"
    replace_exact(
        path,
        '_ACTION_RE = re.compile(r"^\\s*(?:-\\s+)?uses:\\s+([^\\s@]+)@([^\\s#]+)(?:\\s+#\\s*(.*))?\\s*$")\n',
        '_ACTION_RE = re.compile(r"^\\s*(?:-\\s+)?uses:\\s+([^\\s@]+)@([^\\s#]+)(?:\\s+#\\s*(.*))?\\s*$")\n'
        '_UV_VERSION_OUTPUT_RE = re.compile(\n'
        '    r"^uv\\s+(?P<version>[0-9]+(?:\\.[0-9]+){2})(?:\\s+\\([A-Za-z0-9_.-]+\\))?$"\n'
        ')\n',
    )
    marker = '''def _run(command: Sequence[str], *, cwd: Path) -> str:\n'''
    helper = '''def _normalize_uv_version_output(value: str) -> str:\n    raw = str(value or "").strip()\n    match = _UV_VERSION_OUTPUT_RE.fullmatch(raw)\n    if match is None:\n        raise ReleaseToolchainError(\n            "release_uv_version_output_invalid",\n            f"unexpected uv --version output: {raw!r}",\n            environment_blocked=True,\n        )\n    return str(match.group("version"))\n\n\n'''
    replace_exact(path, marker, helper + marker)
    replace_exact(
        path,
        '    actual_uv = _run([str(uv), "--version"], cwd=workspace).removeprefix("uv ").strip()\n',
        '    actual_uv = _normalize_uv_version_output(_run([str(uv), "--version"], cwd=workspace))\n',
    )


def patch_tests() -> None:
    path = ROOT / "services/agent-service/tests/runtime/test_b17e_release_supply_chain_authority.py"
    marker = '''def test_toolchain_provenance_tamper_is_rejected(tmp_path: Path) -> None:\n'''
    test = '''def test_uv_version_output_normalizes_platform_suffix_without_relaxing_version_identity() -> None:\n    contract = _toolchain()\n    assert contract._normalize_uv_version_output("uv 0.11.29") == "0.11.29"\n    assert (\n        contract._normalize_uv_version_output("uv 0.11.29 (x86_64-unknown-linux-gnu)")\n        == "0.11.29"\n    )\n    assert (\n        contract._normalize_uv_version_output("uv 0.11.30 (x86_64-unknown-linux-gnu)")\n        == "0.11.30"\n    )\n    with pytest.raises(Exception, match="unexpected uv --version output"):\n        contract._normalize_uv_version_output("uv 0.11.29 injected-suffix")\n\n\n'''
    replace_exact(path, marker, test + marker)


def update_manifest() -> int:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("phase manifest files list missing")
    changed = {
        "scripts/release_toolchain_contract.py",
        "services/agent-service/tests/runtime/test_b17e_release_supply_chain_authority.py",
    }
    seen: set[str] = set()
    for entry in files:
        relative = str(entry.get("path") or "")
        if relative not in changed:
            continue
        path = ROOT / relative
        data = path.read_bytes()
        entry["size"] = len(data)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        seen.add(relative)
    if seen != changed:
        raise RuntimeError(f"manifest entries missing: {sorted(changed - seen)}")
    payload["file_count"] = len(files)
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(files)


def main() -> int:
    patch_toolchain_contract()
    patch_tests()
    file_count = update_manifest()
    SELF.unlink()
    print(json.dumps({
        "status": "PASS",
        "repair": "normalize-uv-version-platform-suffix",
        "manifest_file_count": file_count,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
