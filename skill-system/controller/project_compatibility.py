from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = Path(__file__).resolve().parent

from product_source_baseline_policy import (  # type: ignore
    detect_snapshot_source,
    evaluate_product_source,
    load_baseline_document,
    resolve_baseline_authority,
    snapshot_protected_source,
)


def snapshot(root: Path = ROOT) -> dict[str, str]:
    root = root.resolve()
    document = load_baseline_document(root)
    return snapshot_protected_source(
        root,
        document.protected_roots,
        source=detect_snapshot_source(root),
    )


def resolve_baseline(root: Path = ROOT) -> tuple[dict[str, str], str]:
    authority = resolve_baseline_authority(root.resolve())
    return dict(authority.files), authority.name


def evaluate(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    policy = evaluate_product_source(root)
    errors = [
        str(item)
        for item in policy.get("errors") or []
        if str(item) != "protected_baseline_drift"
    ]
    drift = [str(item) for item in policy.get("drift_paths") or []]
    if "protected_baseline_drift" in (policy.get("errors") or []) and drift:
        errors.append("product_source_changed:" + ",".join(drift[:20]))

    required = [
        "scripts/quality_loop.py",
        "scripts/repair_loop.py",
        "architecture-skill/scripts/verify_skill_package.py",
    ]
    missing = [path for path in required if not (root / path).is_file()]
    if missing:
        errors.append("missing_legacy_entrypoints:" + ",".join(missing))

    result = dict(policy)
    result["status"] = "PASS" if not errors else "FAIL"
    result["errors"] = errors
    return result


def main() -> int:
    result = evaluate(ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
