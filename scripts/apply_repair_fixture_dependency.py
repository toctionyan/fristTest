#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_PATH = ROOT / "services/agent-service/tests/architecture/test_quality_loop_governance.py"
OLD = 'for script_name in ("quality_loop.py", "source_paths.py", "repair_loop.py"):'
NEW = 'for script_name in ("quality_loop.py", "locked_python.py", "source_paths.py", "repair_loop.py"):'
TEMP_FILES = {
    "scripts/apply_repair_fixture_dependency.py",
    ".github/workflows/apply-repair-fixture-dependency.yml",
}


def main() -> None:
    text = TEST_PATH.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise RuntimeError(f"expected exactly one repair fixture copy boundary, got {text.count(OLD)}")
    TEST_PATH.write_text(text.replace(OLD, NEW), encoding="utf-8")

    for relative in TEMP_FILES:
        path = ROOT / relative
        if path.exists():
            path.unlink()

    manifest_path = ROOT / "PHASE_CANDIDATE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["phase"] = "B17i"
    manifest["root_name"] = "customer_agent_workspace_v20_17_b17i_production_execution_handoff_phase_candidate_env_blocked_20260731"
    manifest["required_environment"] = [str(value).replace("B17j", "B17i") for value in manifest.get("required_environment", [])]

    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    ignored = {"PHASE_CANDIDATE_MANIFEST.json", *TEMP_FILES}
    rows = []
    for relative in sorted(set(listed)):
        if not relative or relative in ignored or "__pycache__" in Path(relative).parts or relative.endswith(".pyc"):
            continue
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        rows.append({"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest["file_count"] = len(rows)
    manifest["files"] = rows
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
