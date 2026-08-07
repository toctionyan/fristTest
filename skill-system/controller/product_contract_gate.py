from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    from .contract import load_contract
    from .product_scope import PRODUCT_PROFILES, target_scope_matches_contract
except ImportError:
    from contract import load_contract  # type: ignore
    from product_scope import PRODUCT_PROFILES, target_scope_matches_contract  # type: ignore

ROOT = Path(__file__).resolve().parents[2]


def _product_workspace(raw: str | Path | None = None) -> Path:
    if raw is None:
        return ROOT.resolve()
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"product workspace root does not exist: {path}")
    return path


def _safe_file(raw: object, label: str, *, workspace: Path) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} is required")
    path = (workspace / raw).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"{label} escapes workspace") from exc
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {raw}")
    return path


def _metadata(text: str, key: str) -> str | None:
    match = re.search(rf"{re.escape(key)}\s*[:：]\s*([^\n]+)", text)
    return match.group(1).strip().strip("`") if match else None


def verify(workspace: Path | None = None) -> list[str]:
    product_root = _product_workspace(workspace)
    errors: list[str] = []
    try:
        contract = load_contract(product_root, require_approved=False)
    except ValueError as exc:
        return [str(exc)]
    if contract.profile not in PRODUCT_PROFILES:
        return [f"active contract is not a product profile: {contract.profile}"]
    quality_target = contract.payload.get("quality_target")
    if contract.target_kind.value in {"repair", "migration", "revert", "certification"}:
        try:
            target = _safe_file(quality_target, "quality_target", workspace=product_root)
            text = target.read_text(encoding="utf-8")
            if _metadata(text, "目标 ID") != contract.change_id:
                errors.append("quality_target_id_does_not_match_change_contract")
            if _metadata(text, "目标类型") != contract.target_kind.value:
                errors.append("quality_target_kind_does_not_match_change_contract")
            declared_mode = _metadata(text, "最低质量模式")
            if declared_mode != contract.payload.get("minimum_quality_mode"):
                errors.append("quality_target_mode_does_not_match_change_contract")
            matches, details = target_scope_matches_contract(target, contract.allowed_paths)
            if not matches:
                errors.append(
                    "quality_target_scope_does_not_match_contract:"
                    + json.dumps(details, ensure_ascii=False, sort_keys=True)
                )
        except ValueError as exc:
            errors.append(str(exc))
    if (
        contract.target_kind.value in {"repair", "migration", "revert"}
        and contract.status in {"implementing", "review", "verified", "closed"}
    ):
        baseline = contract.payload.get("baseline_evidence")
        if not isinstance(baseline, str) or not baseline.strip():
            errors.append("product_transition_requires_baseline_evidence")
        else:
            baseline_path = (product_root / baseline).resolve()
            try:
                baseline_path.relative_to(product_root)
            except ValueError:
                errors.append("baseline_evidence_escapes_workspace")
            if not (baseline_path / "baseline-record.json").is_file():
                errors.append("baseline_evidence_missing_baseline_record")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace-root",
        help="explicit product workspace root; defaults to the Controller repository root",
    )
    args = parser.parse_args()
    try:
        errors = verify(_product_workspace(args.workspace_root))
    except ValueError as exc:
        errors = [str(exc)]
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
