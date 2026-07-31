#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Verification must be observational. Do not let importing the verifier
# create __pycache__ inside the artifact being checked, even when the caller
# forgets to pass Python's -B flag.
sys.dont_write_bytecode = True

from release_artifact import verify_release_tree


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    args = parser.parse_args()
    result = verify_release_tree(Path(args.workspace_root).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
