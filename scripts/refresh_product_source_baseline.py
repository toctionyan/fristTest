#!/usr/bin/env python3
"""Generate the v3 product-source registry from an exact Git commit object."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from product_source_baseline_policy import (  # type: ignore  # noqa: E402
    BASELINE_PATH,
    ProductSourcePolicyError,
    build_canonical_product_snapshot,
)

PROTECTED_ROOTS = ("contracts", "services", "web")


class BaselineRefreshError(RuntimeError):
    pass


def refresh_product_source_baseline(
    workspace: Path,
    *,
    product_source_ref: str,
) -> dict[str, Any]:
    """Write the only accepted registry representation from Git objects."""

    workspace = workspace.resolve()
    baseline_path = workspace / BASELINE_PATH
    try:
        snapshot = build_canonical_product_snapshot(
            workspace,
            product_source_ref,
            PROTECTED_ROOTS,
        )
    except ProductSourcePolicyError as exc:
        raise BaselineRefreshError(str(exc)) from exc

    serialized = json.dumps(
        snapshot,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    previous = baseline_path.read_text(encoding="utf-8") if baseline_path.is_file() else ""
    changed = serialized != previous
    if changed:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(serialized, encoding="utf-8")

    return {
        "status": "REFRESHED" if changed else "CURRENT",
        "changed": changed,
        "path": BASELINE_PATH,
        "product_source_ref": snapshot["product_source_ref"],
        "entry_count": snapshot["entry_count"],
        "protected_snapshot_digest": snapshot["protected_snapshot_digest"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--product-source-ref", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = refresh_product_source_baseline(
            Path(args.workspace),
            product_source_ref=args.product_source_ref,
        )
    except (OSError, ValueError, ProductSourcePolicyError, BaselineRefreshError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}))
        return 2
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
