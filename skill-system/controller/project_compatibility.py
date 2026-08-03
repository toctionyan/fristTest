from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = Path(__file__).resolve().parent
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from contract import load_contract  # type: ignore
from repair_governance import TRANSITION_KINDS, load_chain  # type: ignore

PROTECTED_NAMES = ("services", "web", "contracts")
BASELINE_FILE = Path("skill-system/registry/product-source-baseline.json")
IGNORED_PARTS = {".venv", "node_modules", "__pycache__"}


def snapshot(root: Path = ROOT) -> dict[str, str]:
    root = root.resolve()
    rows: dict[str, str] = {}
    for name in PROTECTED_NAMES:
        protected_root = root / name
        if not protected_root.exists():
            continue
        for path in sorted(item for item in protected_root.rglob("*") if item.is_file()):
            if any(part in IGNORED_PARTS for part in path.parts):
                continue
            rows[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return rows


def _manifest_product_snapshot(workspace_files: object) -> dict[str, str]:
    if not isinstance(workspace_files, dict):
        raise ValueError("permit baseline does not contain workspace_files")
    prefixes = tuple(f"{name}/" for name in PROTECTED_NAMES)
    rows: dict[str, str] = {}
    for raw_path, raw_record in workspace_files.items():
        path = str(raw_path)
        if not path.startswith(prefixes):
            continue
        if not isinstance(raw_record, dict) or not isinstance(raw_record.get("sha256"), str):
            raise ValueError(f"permit baseline has invalid protected file record: {path}")
        rows[path] = str(raw_record["sha256"])
    return rows


def resolve_baseline(root: Path = ROOT) -> tuple[dict[str, str], str]:
    root = root.resolve()
    active = root / "governance/active-change.json"
    if active.is_file():
        contract = load_contract(root, require_approved=False)
        if contract.profile == "skill-only" and contract.target_kind.value in TRANSITION_KINDS:
            chain = load_chain(root, contract.payload)
            return (
                _manifest_product_snapshot(chain.baseline.get("workspace_files")),
                f"change-permit:{chain.permit_digest}",
            )

    path = root / BASELINE_FILE
    if not path.is_file():
        raise ValueError("missing_product_source_baseline")
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        raise ValueError("invalid_product_source_baseline")
    return ({str(key): str(value) for key, value in files.items()}, "historical-registry-baseline")


def evaluate(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    current = snapshot(root)
    errors: list[str] = []
    try:
        baseline, authority = resolve_baseline(root)
    except (ValueError, json.JSONDecodeError) as exc:
        baseline, authority = {}, "unavailable"
        errors.append(str(exc))
    changed = sorted(path for path in set(current) | set(baseline) if current.get(path) != baseline.get(path))
    required = [
        "scripts/quality_loop.py",
        "scripts/repair_loop.py",
        "architecture-skill/scripts/verify_skill_package.py",
    ]
    missing = [path for path in required if not (root / path).is_file()]
    if changed:
        errors.append("product_source_changed:" + ",".join(changed[:20]))
    if missing:
        errors.append("missing_legacy_entrypoints:" + ",".join(missing))
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "protected_file_count": len(current),
        "baseline_file_count": len(baseline),
        "baseline_authority": authority,
    }


def main() -> int:
    result = evaluate(ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
