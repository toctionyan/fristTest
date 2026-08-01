#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMP_FILES = {
    ".github/scripts/apply_production_request_007.py",
    ".github/workflows/one-time-production-request-007.yml",
}
REQUIRED_FILES = {
    ".github/production-certification-request.json",
    ".github/workflows/production-certification-request.yml",
    "scripts/quality_loop.py",
    "scripts/release_toolchain_contract.py",
    "services/agent-service/tests/runtime/test_quality_loop_npm_launcher_boundary.py",
    "services/agent-service/tests/runtime/test_release_runtime_database_authority.py",
    "services/agent-service/tests/runtime/test_release_toolchain_npm_diagnostics.py",
    "services/agent-service/tests/runtime/test_release_toolchain_npm_version_provenance.py",
}

request_path = ROOT / ".github/production-certification-request.json"
request_path.write_text(
    json.dumps(
        {
            "schema_version": 1,
            "request_id": "production-certification-20260801-007",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "embedding_model": "text-embedding-v4",
            "embedding_dimension": "1024",
            "comment_issue": 7,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)

for relative in TEMP_FILES:
    path = ROOT / relative
    if path.exists():
        path.unlink()

manifest_path = ROOT / "PHASE_CANDIDATE_MANIFEST.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
listed = subprocess.run(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
    cwd=ROOT,
    text=True,
    capture_output=True,
    check=True,
).stdout.splitlines()
ignored = {"PHASE_CANDIDATE_MANIFEST.json", *TEMP_FILES}
rows: list[dict[str, object]] = []
for relative in sorted(set(listed)):
    if (
        not relative
        or relative in ignored
        or "__pycache__" in Path(relative).parts
        or relative.endswith(".pyc")
    ):
        continue
    path = ROOT / relative
    if not path.is_file() or path.is_symlink():
        continue
    data = path.read_bytes()
    rows.append(
        {
            "path": relative,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    )

present = {str(row["path"]) for row in rows}
if not REQUIRED_FILES <= present:
    raise RuntimeError(
        f"manifest omitted required release files: {sorted(REQUIRED_FILES - present)}"
    )
manifest["file_count"] = len(rows)
manifest["files"] = rows
manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
