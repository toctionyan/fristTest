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
OUTPUT = ROOT / ".github" / "diagnostics" / "npm-ls-after-gates.json"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)


def snippet(value: str, limit: int = 2000) -> dict[str, object]:
    encoded = value.encode("utf-8", errors="replace")
    return {
        "length": len(value),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "prefix_repr": repr(value[:limit]),
        "suffix_repr": repr(value[-limit:]) if len(value) > limit else repr(value),
    }


def run_variant(name: str, command: list[str], *, quiet: bool = False) -> dict[str, object]:
    env = os.environ.copy()
    if quiet:
        env.update(
            {
                "NO_COLOR": "1",
                "FORCE_COLOR": "0",
                "npm_config_color": "false",
                "npm_config_loglevel": "silent",
                "npm_config_progress": "false",
                "npm_config_fund": "false",
                "npm_config_audit": "false",
            }
        )
    completed = subprocess.run(
        command,
        cwd=FRONTEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    parsed: object | None = None
    parse_error: str | None = None
    try:
        parsed = json.loads(completed.stdout)
    except Exception as exc:  # diagnostic only
        parse_error = f"{type(exc).__name__}: {exc}"
    return {
        "name": name,
        "command": command,
        "quiet": quiet,
        "returncode": completed.returncode,
        "stdout": snippet(completed.stdout),
        "stderr": snippet(completed.stderr),
        "json_parse_ok": parse_error is None,
        "json_type": type(parsed).__name__ if parse_error is None else None,
        "json_root_name": parsed.get("name") if isinstance(parsed, dict) else None,
        "json_problem_count": len(parsed.get("problems") or []) if isinstance(parsed, dict) else None,
        "parse_error": parse_error,
    }


found = shutil.which("npm")
if not found:
    raise SystemExit("npm is missing")
launcher = Path(found)
resolved = launcher.resolve()
node = shutil.which("node")
if not node:
    raise SystemExit("node is missing")

npm_root: Path | None = None
for parent in resolved.parents[:6]:
    package_json = parent / "package.json"
    if not package_json.is_file():
        continue
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except Exception:
        continue
    if isinstance(payload, dict) and payload.get("name") == "npm":
        npm_root = parent
        break
if npm_root is None:
    raise SystemExit(f"cannot resolve npm installation from {resolved}")
npm_cli = npm_root / "bin" / "npm-cli.js"

args = ["ls", "--all", "--offline", "--json"]
variants = [
    run_variant("path_launcher", [str(launcher), *args]),
    run_variant("resolved_launcher", [str(resolved), *args]),
    run_variant("node_npm_cli", [str(Path(node).resolve()), str(npm_cli), *args]),
    run_variant("path_launcher_quiet", [str(launcher), *args], quiet=True),
    run_variant("resolved_launcher_quiet", [str(resolved), *args], quiet=True),
    run_variant("node_npm_cli_quiet", [str(Path(node).resolve()), str(npm_cli), *args], quiet=True),
    run_variant("node_npm_cli_without_offline", [str(Path(node).resolve()), str(npm_cli), "ls", "--all", "--json"], quiet=True),
]

result = {
    "schema_version": 1,
    "node": str(Path(node).resolve()),
    "npm_launcher": str(launcher),
    "npm_resolved": str(resolved),
    "npm_root": str(npm_root),
    "npm_cli": str(npm_cli),
    "frontend": str(FRONTEND),
    "variants": variants,
}
OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"output": str(OUTPUT), "variants": [{"name": row["name"], "returncode": row["returncode"], "json_parse_ok": row["json_parse_ok"]} for row in variants]}, ensure_ascii=False))
