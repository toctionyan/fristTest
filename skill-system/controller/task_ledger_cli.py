from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .task_ledger import LEDGER_RELATIVE_PATH, summary, validate
except ImportError:
    from task_ledger import LEDGER_RELATIVE_PATH, summary, validate  # type: ignore


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and inspect the authoritative project task ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "status"):
        command = sub.add_parser(name)
        command.add_argument("--workspace-root", default=".")
        command.add_argument("--ledger")
    args = parser.parse_args()
    workspace = Path(args.workspace_root).resolve()
    path = Path(args.ledger).resolve() if args.ledger else workspace / LEDGER_RELATIVE_PATH
    result = validate(workspace, path)
    payload = {
        "status": result.status,
        "ledger": path.relative_to(workspace).as_posix() if path.is_relative_to(workspace) else str(path),
        "errors": list(result.errors),
    }
    if result.payload:
        payload.update(summary(result.payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
