#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    args = parser.parse_args()
    path = Path(args.script)
    text = path.read_text(encoding="utf-8")
    old = '    old_semantic_claim_text = \'\'\'                    + "This bounded final call must re-audit only the disputed requested-effect or target-scope semantic claim. "\\n\'\'\'\n'
    new = '    old_semantic_claim_text = \'\'\'                    "This bounded final call must re-audit only the disputed requested-effect or target-scope semantic claim. "\\n\'\'\'\n'
    if text.count(old) != 1:
        raise SystemExit(f"expected one carrier semantic-claim anchor, found {text.count(old)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
