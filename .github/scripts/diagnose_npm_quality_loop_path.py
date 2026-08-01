#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "services" / "agent-service" / "frontend"
OUTPUT = ROOT / ".github" / "diagnostics" / "npm-quality-loop-path.json"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

found = shutil.which("npm")
if not found:
    raise SystemExit("npm missing")
resolved = Path(found).resolve()
quality_env = os.environ.copy()
quality_env["PATH"] = str(resolved.parent) + os.pathsep + quality_env.get("PATH", "")
quality_env.update({
    "PYTHONDONTWRITEBYTECODE": "1",
    "QUALITY_LOOP_MODE": "release",
    "QUALITY_GATE_ID": "production-certification-bundle",
    "QUALITY_EVIDENCE_DIR": "/tmp/production-release-evidence",
})

commands = {
    "original_path": [str(resolved), "ls", "--all", "--offline", "--json"],
    "path_lookup": ["npm", "ls", "--all", "--offline", "--json"],
}
rows = []
for name, command in commands.items():
    completed = subprocess.run(
        command,
        cwd=FRONTEND,
        env=quality_env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
        parse_error = None
    except Exception as exc:
        payload = None
        parse_error = f"{type(exc).__name__}: {exc}"
    rows.append({
        "name": name,
        "command": command,
        "returncode": completed.returncode,
        "json_parse_ok": parse_error is None,
        "parse_error": parse_error,
        "stdout_length": len(completed.stdout),
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stdout_prefix_repr": repr(completed.stdout[:1000]),
        "stdout_suffix_repr": repr(completed.stdout[-1000:]),
        "stderr_length": len(completed.stderr),
        "stderr_prefix_repr": repr(completed.stderr[:1000]),
        "json_root_name": payload.get("name") if isinstance(payload, dict) else None,
    })

OUTPUT.write_text(json.dumps({
    "schema_version": 1,
    "npm_found": found,
    "npm_resolved": str(resolved),
    "quality_path_prefix": str(resolved.parent),
    "rows": rows,
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(rows, ensure_ascii=False))
