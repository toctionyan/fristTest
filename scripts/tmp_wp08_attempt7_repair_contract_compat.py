#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def patch(root: Path) -> None:
    path = root / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    text = read(path)
    line = '            "scope_constraint_rule": "explicit target/result-population predicates use target_candidate.scope_constraints literal evidence; ordinary scope filters are not Goal.condition",\n'
    if text.count(line) != 1:
        raise SystemExit(f"scope_constraint_rule count={text.count(line)}")
    write(path, text.replace(line, "", 1))


def baseline(root: Path, product_sha: str) -> None:
    path = root / "skill-system/registry/product-source-baseline.json"
    data = json.loads(read(path))
    rel = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    data["files"][rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["generated_from"] = f"git:{product_sha}"
    write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("patch")
    p.add_argument("--workspace", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--workspace", required=True)
    b.add_argument("--product-sha", required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.cmd == "patch":
        patch(root)
    else:
        baseline(root, args.product_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
