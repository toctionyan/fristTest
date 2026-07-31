from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Iterable

PRODUCT_ROOTS = (
    "services/agent-service/**",
    "services/business-service/**",
    "contracts/**",
    "deployment/**",
    "docs/**",
)
PRODUCT_WRITE_PROFILES = {
    "product-repair",
    "product-migration",
    "product-revert",
}
PRODUCT_READ_ONLY_PROFILES = {
    "product-diagnosis",
    "product-design",
    "product-oracle-review",
    "product-certification",
}
PRODUCT_PROFILES = PRODUCT_WRITE_PROFILES | PRODUCT_READ_ONLY_PROFILES
PRODUCT_CONTROL_FORBIDDEN = (
    "skill-system/**",
    "architecture-skill/**",
    "governance/quality-loop-policy.json",
    "governance/evidence/**",
    ".quality/**",
    ".agents/**",
    ".claude/**",
    ".codex/**",
    "AGENTS.md",
    "CLAUDE.md",
)
BROAD_WRITE_PATTERNS = {
    "services/**",
    "services/agent-service/**",
    "services/business-service/**",
    "contracts/**",
    "deployment/**",
    "docs/**",
}
MODE_ORDER = {"static": 0, "quick": 1, "integration": 2, "release": 3}


def normalize_pattern(value: str) -> str:
    normalized = value.strip().strip("`\"'").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def pattern_is_safe(value: str) -> bool:
    normalized = normalize_pattern(value)
    return bool(normalized) and normalized not in {"*", "**", ".", "./**"} and ".." not in Path(normalized).parts


def pattern_is_product_path(value: str) -> bool:
    normalized = normalize_pattern(value)
    return any(
        fnmatch.fnmatchcase(normalized, root)
        or fnmatch.fnmatchcase(root, normalized)
        or normalized.startswith(root.removesuffix("**"))
        for root in PRODUCT_ROOTS
    )


def validate_product_scope(
    *,
    profile: str,
    target_kind: str,
    allowed_paths: Iterable[str],
    forbidden_paths: Iterable[str],
    minimum_mode: str | None,
) -> list[str]:
    errors: list[str] = []
    allowed = [normalize_pattern(value) for value in allowed_paths]
    forbidden = [normalize_pattern(value) for value in forbidden_paths]
    if profile not in PRODUCT_PROFILES:
        return [f"unknown_product_profile:{profile}"]
    if minimum_mode not in MODE_ORDER:
        errors.append("invalid_minimum_quality_mode")
    if not allowed:
        errors.append("product_allowed_paths_empty")
    for value in allowed:
        if not pattern_is_safe(value):
            errors.append(f"unsafe_product_allowed_path:{value}")
        elif not pattern_is_product_path(value):
            errors.append(f"non_product_allowed_path:{value}")
    if profile in PRODUCT_WRITE_PROFILES:
        for value in allowed:
            if value in BROAD_WRITE_PATTERNS:
                errors.append(f"product_write_scope_too_broad:{value}")
        if target_kind not in {"repair", "migration", "revert"}:
            errors.append("product_write_profile_requires_transition_target")
    if profile in PRODUCT_READ_ONLY_PROFILES and target_kind in {"repair", "migration", "revert"}:
        errors.append("product_read_only_profile_cannot_write_transition")
    for required in PRODUCT_CONTROL_FORBIDDEN:
        if required not in forbidden:
            errors.append(f"product_scope_missing_control_forbidden:{required}")
    for value in allowed:
        if any(fnmatch.fnmatchcase(value, blocked) or fnmatch.fnmatchcase(blocked, value) for blocked in forbidden):
            errors.append(f"product_allowed_path_is_forbidden:{value}")
    return errors


def parse_target_allowed_paths(target_path: Path) -> tuple[str, ...]:
    text = target_path.read_text(encoding="utf-8")
    match = re.search(r"允许变更路径\s*[:：]\s*(.+)", text)
    if not match:
        raise ValueError("quality target does not declare 允许变更路径")
    values = [
        normalize_pattern(value)
        for value in re.split(r"[,，;；]", match.group(1))
        if normalize_pattern(value)
    ]
    if not values:
        raise ValueError("quality target allowed paths are empty")
    return tuple(values)


def target_scope_matches_contract(target_path: Path, contract_allowed: Iterable[str]) -> tuple[bool, dict[str, Any]]:
    target_allowed = tuple(sorted(parse_target_allowed_paths(target_path)))
    contract = tuple(sorted(normalize_pattern(value) for value in contract_allowed))
    return target_allowed == contract, {
        "target_allowed_paths": list(target_allowed),
        "contract_allowed_paths": list(contract),
    }


def profile_for_target(target_kind: str) -> str:
    mapping = {
        "diagnosis": "product-diagnosis",
        "design": "product-design",
        "oracle-review": "product-oracle-review",
        "repair": "product-repair",
        "migration": "product-migration",
        "revert": "product-revert",
        "certification": "product-certification",
    }
    try:
        return mapping[target_kind]
    except KeyError as exc:
        raise ValueError(f"unsupported product target kind: {target_kind}") from exc


def required_profiles_for_product(target_kind: str, minimum_mode: str) -> tuple[str, ...]:
    common = ("product-contract", "product-portable-conformance", "product-security")
    if target_kind in {"diagnosis", "design", "oracle-review"}:
        return common + ("product-readonly",)
    return common + (f"product-quality-{minimum_mode}",)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--target-kind", required=True)
    parser.add_argument("--minimum-mode", default="static")
    parser.add_argument("--allow", action="append", default=[])
    parser.add_argument("--forbid", action="append", default=[])
    args = parser.parse_args()
    errors = validate_product_scope(
        profile=args.profile,
        target_kind=args.target_kind,
        allowed_paths=args.allow,
        forbidden_paths=args.forbid,
        minimum_mode=args.minimum_mode,
    )
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
