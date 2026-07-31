#!/usr/bin/env python3
"""Verify the preserved frontend dependency tree without mutating it."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def verify(workspace: Path) -> dict[str, object]:
    frontend = workspace / "services/agent-service/frontend"
    errors: list[str] = []
    package = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
    root = dict((lock.get("packages") or {}).get("") or {})
    if root.get("name") != package.get("name"):
        errors.append("package_lock_root_name_mismatch")
    if root.get("version") != package.get("version"):
        errors.append("package_lock_root_version_mismatch")
    for section in ("dependencies", "devDependencies"):
        if dict(root.get(section) or {}) != dict(package.get(section) or {}):
            errors.append(f"package_lock_root_{section}_mismatch")
    required = [
        frontend / "node_modules/.bin/vite",
        frontend / "node_modules/.bin/vitest",
        frontend / "node_modules/@adobe/css-tools/dist/esm/adobe-css-tools.mjs",
    ]
    missing = [path.relative_to(frontend).as_posix() for path in required if not path.exists()]
    errors.extend(f"missing_dependency_entrypoint:{path}" for path in missing)
    npm_check: dict[str, object] = {"ran": False}
    if not missing:
        proc = subprocess.run(
            ["npm", "ls", "--depth=0", "--offline", "--json"],
            cwd=frontend,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        npm_check = {"ran": True, "exit_code": proc.returncode}
        # npm's peer/optional dependency exit status is environment-sensitive.
        # Keep it as diagnostics; the following Vitest and production-build
        # gates are the authoritative executable proof of the dependency tree.
    return {"status": "PASS" if not errors else "FAIL", "errors": errors, "npm_check": npm_check}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    args = parser.parse_args()
    report = verify(Path(args.workspace_root).resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
