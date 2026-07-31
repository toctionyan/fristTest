#!/usr/bin/env python3
"""Regenerate or verify module manifests from source-of-truth capability definitions.

The manifests are release mirrors, never an independent registration source.  ``--check``
allows CI and the module architecture gate to prove that checked-in manifests are current
without mutating the working tree.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def render_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write_json(path: Path, value: dict) -> None:
    path.write_text(render_json(value), encoding="utf-8")


def manifest_targets() -> tuple[tuple[Path, dict], ...]:
    from agent_modules.ecommerce.manifest import build_module_manifest as build_ecommerce_manifest
    from agent_modules.support_ticket_demo.manifest import (
        build_module_manifest as build_support_ticket_manifest,
    )

    return (
        (ROOT / "src/agent_modules/ecommerce/module_manifest.json", build_ecommerce_manifest()),
        (
            ROOT / "src/agent_modules/support_ticket_demo/module_manifest.json",
            build_support_ticket_manifest(),
        ),
    )


def verify_targets(targets: tuple[tuple[Path, dict], ...]) -> list[str]:
    stale: list[str] = []
    for path, value in targets:
        expected = render_json(value)
        actual = path.read_text(encoding="utf-8") if path.exists() else None
        if actual != expected:
            stale.append(str(path.relative_to(ROOT)))
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify checked-in manifests without modifying them",
    )
    args = parser.parse_args(argv)

    targets = manifest_targets()
    if args.check:
        stale = verify_targets(targets)
        if stale:
            print(json.dumps({"status": "FAIL", "stale_manifests": stale}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"status": "PASS", "manifests": [str(path.relative_to(ROOT)) for path, _ in targets]}, ensure_ascii=False, indent=2))
        return 0

    for path, value in targets:
        write_json(path, value)
    print(json.dumps({"status": "UPDATED", "manifests": [str(path.relative_to(ROOT)) for path, _ in targets]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
