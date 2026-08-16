from __future__ import annotations

import hashlib
import json
import os
import subprocess
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
IGNORED_PARTS = {".venv", ".pytest_cache", "node_modules", "__pycache__"}
MACHINE_LOCAL_PARTS = {"runtime"}
PERMIT_BASELINE_STATUSES = {"approved", "implementing", "review", "verified"}


def _git_tracked_protected_paths(root: Path) -> list[str] | None:
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if top.returncode != 0:
        return None
    try:
        if Path(top.stdout.strip()).resolve() != root.resolve():
            return None
    except (OSError, RuntimeError):
        return None
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", *PROTECTED_NAMES],
        capture_output=True,
        check=False,
        timeout=10,
    )
    if listed.returncode != 0:
        return None
    return sorted(
        item.decode("utf-8") for item in listed.stdout.split(b"\0") if item
    )


def snapshot(root: Path = ROOT) -> dict[str, str]:
    root = root.resolve()
    tracked = _git_tracked_protected_paths(root)
    if tracked is not None:
        return {
            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in tracked
            if (root / relative).is_file()
        }

    # Packaged/offline workspaces may intentionally omit .git. Keep the
    # compatibility verifier usable there, while excluding machine-local
    # runtime state that the repository itself declares non-source.
    rows: dict[str, str] = {}
    for name in PROTECTED_NAMES:
        protected_root = root / name
        if not protected_root.exists():
            continue
        for path in sorted(item for item in protected_root.rglob("*") if item.is_file()):
            relative = path.relative_to(root)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            if any(part in MACHINE_LOCAL_PARTS for part in relative.parts) and path.name != ".gitkeep":
                continue
            rows[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
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
        active_payload = json.loads(active.read_text(encoding="utf-8"))
        active_status = str(active_payload.get("status") or "").strip().lower()
        if active_status in PERMIT_BASELINE_STATUSES:
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


def _requires_product_source_equality(authority: str) -> bool:
    # A pull_request checkout is an unaccepted candidate snapshot. Its protected
    # source is expected to differ from the accepted historical registry until
    # governance and baseline acceptance close. Permit-bound repairs remain
    # strict because their immutable permit snapshot is the current authority.
    return not (
        authority == "historical-registry-baseline"
        and os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
    )


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
    if changed and _requires_product_source_equality(authority):
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
