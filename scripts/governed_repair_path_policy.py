from __future__ import annotations

"""Canonical path-capability policy for automatic governed source repair.

This module is the single machine-readable source for *where* an automatic
repair write grant may point. Product-source repair keeps its historical policy
unchanged. A second, deliberately narrower control-plane implementation domain
may repair only engineering verifier implementation files that an upstream
semantic router has already bound to the exact failing PR and failing tests.

Neither domain may write tests/oracles, workflows, governance data, repair
authority, secrets, dependency manifests, merge state, deploy state, or
production state.
"""

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

PATH_POLICY_SCHEMA = "governed-repair-path-policy@1"
PATH_POLICY_ID = "customer-agent/governed-repair-path-policy@1"
MAX_WRITE_PATHS = 16

REPAIR_DOMAIN_PRODUCT = "PRODUCT_CODE"
REPAIR_DOMAIN_CONTROL_PLANE = "CONTROL_PLANE_IMPLEMENTATION"
REPAIR_DOMAINS = frozenset({REPAIR_DOMAIN_PRODUCT, REPAIR_DOMAIN_CONTROL_PLANE})

SUPPORTED_SUFFIXES = frozenset(
    {
        ".py",
        ".json",
        ".toml",
        ".yml",
        ".yaml",
        ".sh",
        ".bash",
        ".js",
        ".mjs",
        ".cjs",
        ".jsx",
        ".ts",
        ".tsx",
    }
)
AUTOMATIC_SOURCE_ROOTS = ("services/", "web/", "contracts/")
FORBIDDEN_PATH_PARTS = frozenset({"tests", "test", "e2e", "__tests__"})
FORBIDDEN_BASENAMES = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "dockerfile",
    }
)
PROTECTED_PREFIXES = (
    "governance/",
    "skill-system/",
    ".github/",
    ".git/",
    ".quality/",
)
# Keep this historical product projection byte-for-byte equivalent to the
# existing Patch Owner filter. New M8.6 control-plane files are not made product
# writable: product repair already rejects all ``scripts/`` paths by root, while
# the separate control domain below is independently allow-listed.
PROTECTED_EXACT = frozenset(
    {
        "scripts/quality_loop.py",
        "scripts/repair_loop.py",
        "scripts/github_failure_ingest.py",
        "scripts/github_agent_fixer.py",
        "scripts/github_repair_orchestrator.py",
        "scripts/github_repair_orchestrator_control_plane.py",
        "scripts/github_repair_authority.py",
        "scripts/github_repair_rca.py",
        "skill-system/registry/product-source-baseline.json",
    }
)

# This is intentionally not a generic ``scripts/`` capability. It is only the
# implementation side of the engineering verifier family. The semantic router
# must additionally prove that the exact file is already changed in the source
# PR and paired with a failing test module before this path policy is consulted.
CONTROL_PLANE_IMPLEMENTATION_PREFIXES = ("scripts/verify_engineering_",)
CONTROL_PLANE_IMPLEMENTATION_SUFFIXES = frozenset({".py"})


class RepairPathPolicyError(ValueError):
    """A requested automatic repair path violates the canonical policy."""


def policy_payload() -> dict[str, Any]:
    return {
        "schema": PATH_POLICY_SCHEMA,
        "policy_id": PATH_POLICY_ID,
        "max_write_paths": MAX_WRITE_PATHS,
        "supported_suffixes": sorted(SUPPORTED_SUFFIXES),
        "automatic_source_roots": list(AUTOMATIC_SOURCE_ROOTS),
        "forbidden_path_parts": sorted(FORBIDDEN_PATH_PARTS),
        "forbidden_basenames": sorted(FORBIDDEN_BASENAMES),
        "protected_prefixes": list(PROTECTED_PREFIXES),
        "protected_exact": sorted(PROTECTED_EXACT),
        "test_filename_rules": ["test_*", "*.test.*", "*.spec.*"],
        "environment_filename_rules": [".env", ".env.example"],
        "lock_suffix_forbidden": True,
        "existing_regular_file_required_at_execution": True,
        "repair_domains": {
            REPAIR_DOMAIN_PRODUCT: {
                "automatic_source_roots": list(AUTOMATIC_SOURCE_ROOTS),
                "test_write_allowed": False,
                "acceptance_write_allowed": False,
            },
            REPAIR_DOMAIN_CONTROL_PLANE: {
                "allowed_prefixes": list(CONTROL_PLANE_IMPLEMENTATION_PREFIXES),
                "allowed_suffixes": sorted(CONTROL_PLANE_IMPLEMENTATION_SUFFIXES),
                "requires_exact_source_pr_change": True,
                "requires_failed_test_module_pairing": True,
                "test_write_allowed": False,
                "acceptance_write_allowed": False,
                "workflow_write_allowed": False,
                "governance_write_allowed": False,
            },
        },
    }


def policy_fingerprint(payload: Mapping[str, Any] | None = None) -> str:
    value: Mapping[str, Any] = policy_payload() if payload is None else payload
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_repo_path(raw: object) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != value
    ):
        raise RepairPathPolicyError(f"invalid repository path: {raw!r}")
    return value


def _common_violation(path: str) -> str | None:
    lowered = path.casefold()
    pure = PurePosixPath(path)
    parts = {part.casefold() for part in pure.parts}
    name = pure.name.casefold()

    if path in PROTECTED_EXACT or any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return "protected_control_plane_path"
    if parts & FORBIDDEN_PATH_PARTS:
        return "test_or_evaluation_path"
    if name.startswith("test_") or ".test." in name or ".spec." in name:
        return "test_or_evaluation_filename"
    if name in FORBIDDEN_BASENAMES or name.endswith(".lock"):
        return "dependency_or_build_manifest"
    if lowered.endswith(("/.env", "/.env.example")):
        return "environment_configuration"
    return None


def path_policy_violation(
    raw: object,
    *,
    repair_domain: str = REPAIR_DOMAIN_PRODUCT,
) -> str | None:
    """Return a stable violation code, or ``None`` when the path is eligible."""

    try:
        path = normalize_repo_path(raw)
    except RepairPathPolicyError:
        return "invalid_repository_path"

    if repair_domain not in REPAIR_DOMAINS:
        return "unknown_repair_domain"
    common = _common_violation(path)
    if common:
        return common

    pure = PurePosixPath(path)
    if repair_domain == REPAIR_DOMAIN_PRODUCT:
        if not any(path.startswith(root) for root in AUTOMATIC_SOURCE_ROOTS):
            return "outside_automatic_product_source_roots"
        if pure.suffix.lower() not in SUPPORTED_SUFFIXES:
            return "unsupported_file_type"
        return None

    if not any(path.startswith(prefix) for prefix in CONTROL_PLANE_IMPLEMENTATION_PREFIXES):
        return "outside_bounded_control_plane_implementation_prefixes"
    if pure.suffix.lower() not in CONTROL_PLANE_IMPLEMENTATION_SUFFIXES:
        return "unsupported_control_plane_implementation_type"
    return None


def validate_repair_paths(
    paths: Iterable[object],
    *,
    repair_domain: str = REPAIR_DOMAIN_PRODUCT,
) -> tuple[str, ...]:
    """Normalize and fail closed unless every path is eligible for the exact domain."""

    if repair_domain not in REPAIR_DOMAINS:
        raise RepairPathPolicyError(f"unknown_repair_domain: {repair_domain}")
    result: list[str] = []
    for raw in paths:
        path = normalize_repo_path(raw)
        violation = path_policy_violation(path, repair_domain=repair_domain)
        if violation:
            raise RepairPathPolicyError(f"{violation}: {path}")
        if path in result:
            raise RepairPathPolicyError(f"duplicate_write_path: {path}")
        result.append(path)
    if not result:
        raise RepairPathPolicyError("repair_candidate_path_set_empty")
    if len(result) > MAX_WRITE_PATHS:
        raise RepairPathPolicyError(
            f"repair_candidate_path_count_exceeds_{MAX_WRITE_PATHS}"
        )
    return tuple(result)


def validate_automatic_repair_paths(paths: Iterable[object]) -> tuple[str, ...]:
    """Historical product-source API; its authority remains unchanged."""

    return validate_repair_paths(paths, repair_domain=REPAIR_DOMAIN_PRODUCT)


def mutation_detection_matrix() -> dict[str, bool]:
    """Prove representative capability-expansion mutations change policy identity."""

    baseline = policy_payload()
    mutations: dict[str, dict[str, Any]] = {}

    allow_tests = json.loads(json.dumps(baseline))
    allow_tests["forbidden_path_parts"] = [
        value for value in allow_tests["forbidden_path_parts"] if value != "tests"
    ]
    mutations["tests_became_writable"] = allow_tests

    allow_workflows = json.loads(json.dumps(baseline))
    allow_workflows["protected_prefixes"] = [
        value for value in allow_workflows["protected_prefixes"] if value != ".github/"
    ]
    mutations["workflow_boundary_removed"] = allow_workflows

    widen_roots = json.loads(json.dumps(baseline))
    widen_roots["automatic_source_roots"].append("scripts/")
    mutations["product_control_plane_root_added"] = widen_roots

    widen_control = json.loads(json.dumps(baseline))
    widen_control["repair_domains"][REPAIR_DOMAIN_CONTROL_PLANE]["allowed_prefixes"] = ["scripts/"]
    mutations["control_plane_pattern_widened"] = widen_control

    allow_control_tests = json.loads(json.dumps(baseline))
    allow_control_tests["repair_domains"][REPAIR_DOMAIN_CONTROL_PLANE]["test_write_allowed"] = True
    mutations["control_plane_tests_became_writable"] = allow_control_tests

    expand_count = json.loads(json.dumps(baseline))
    expand_count["max_write_paths"] = MAX_WRITE_PATHS + 1
    mutations["write_budget_expanded"] = expand_count

    baseline_sha = policy_fingerprint(baseline)
    return {
        name: policy_fingerprint(mutated) != baseline_sha
        for name, mutated in mutations.items()
    }
