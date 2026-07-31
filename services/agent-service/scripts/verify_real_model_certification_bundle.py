#!/usr/bin/env python3
"""Execute smoke, semantic, and full-lifecycle real-model certification live."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agent_core.model_calls import run_certification_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=str(WORKSPACE))
    parser.add_argument("--evidence-out")
    args = parser.parse_args()
    result = run_certification_bundle(workspace_root=Path(args.workspace_root))
    if args.evidence_out and result.get("status") == "PASS":
        output = Path(args.evidence_out).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        public = {key: value for key, value in result.items() if key != "component_evidence"}
        public["evidence_file"] = str(output)
    else:
        public = result
    print(json.dumps(public, ensure_ascii=False))
    status = result.get("status")
    return 0 if status == "PASS" else (78 if status == "BLOCKED_BY_ENVIRONMENT" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
