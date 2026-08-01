#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = {'scripts/locked_python.py': 'eNp9VFFP2zAQfvevsPyUbCW8d2JSQdXGxkpHywSqUOSmF2oa7Mh22lWM/75z7DgBaYvUJjnffffdfXcptXqmeV42ttGQ51Q810pbyqVUlluhpCGEMXYDRlV7oJUqdrChtVZPUFg6P9qtkhSk1cdaCWkNPQg0NZZuQEOJP1kI+Uj3QtuGVyD3tBJyZzLEJCQkU6Z7MkdDSseo5nZbiXVHZ46v/sAea4cX7D947V4Jyec319+mF8sFPaMvhOLF+CPSYmPKDOi9KMCctpaT8MpG3m3dGCHBmDeenXHg/EoI2UAZOpCHDuR124GkxToovTM1L2DcEvYJguOYGquRXKDlzz74G3Zl3JWyQreR832gf+hMScAYdxuRlJ58bnHHnrgTBUWTqBWF31A0lq8riNKgGKBrDfhPD1vRnkBbDrbPboHunRi9cl4RhyzKCIJDgEA0dtendpfmwgD9xasGplornZSskTupDrKbkTAbsf6X8PTK0hbFqEYXrjxlMqQiNDpjasdKGF86VJgDDcNG5s4B+/jzdnJ1ubzPJ1+ms2U+v19+vZ6xIfez2GyPEyPObxeXs+li0QW16AWXG7HhFgyCr2KZKETiiWaPYJMBh5QqjSKkGbqIOklH/4qJeX26fHo3vbhdTs6vpuw9xn/ps7cZ3CQkceJSetqrtAoYD2hkmdOZuae1kO3djyx7z/iIMsQxCocPvjdbcIqOcXWNXbWziT3yZyVWoPnBTUnfwn5MsCA3Q+jR21pIJa2QDURjDEbotjQMSZFQjfYGxzZJM77Gb1BjIUn7KM8sw90BuUlcHREoTYcsojkTJi9xHbDbaHLDxwtcedMHjpzxLr/+nr7lrP26RT/S78FNg+U8h02IQSWTariaYTHiInQrMljVT32zGf1I2QgFfML1TILZ15SSvwwKzs4=', 'services/agent-service/tests/runtime/test_release_owned_runtime_boundary.py': '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'}
TEMP_FILES = (
    "scripts/cleanup_release_owned_runtime_repair.py",
    ".github/workflows/cleanup-release-owned-runtime-repair.yml",
)


def main() -> None:
    for relative, encoded in PAYLOAD.items():
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(zlib.decompress(base64.b64decode(encoded)))

    for cache in ROOT.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache)
    for pyc in ROOT.rglob("*.pyc"):
        if pyc.is_file():
            pyc.unlink()

    for relative in TEMP_FILES:
        path = ROOT / relative
        if path.exists():
            path.unlink()

    manifest_path = ROOT / "PHASE_CANDIDATE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["phase"] = "B17i"
    manifest["root_name"] = "customer_agent_workspace_v20_17_b17i_production_execution_handoff_phase_candidate_env_blocked_20260731"
    manifest["required_environment"] = [
        str(value).replace("B17j", "B17i")
        for value in manifest.get("required_environment", [])
    ]
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
        rows.append({
            "path": relative,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    manifest["file_count"] = len(rows)
    manifest["files"] = rows
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
