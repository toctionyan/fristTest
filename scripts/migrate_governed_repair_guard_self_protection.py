#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts/verify_governed_repair_architecture.py"


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    old = '''    "scripts/verify_product_source_baseline.py",\n    ".github/workflows/governed-ci-repair-stage3.yml",\n'''
    new = '''    "scripts/verify_product_source_baseline.py",\n    "scripts/verify_governed_repair_mutation_proof.py",\n    "services/agent-service/tests/architecture/test_governed_repair_mutation_proof.py",\n    ".github/workflows/governed-ci-repair-stage3.yml",\n'''
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one architecture required-file anchor, found {count}")
    TARGET.write_text(text.replace(old, new, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
