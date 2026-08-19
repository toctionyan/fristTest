from __future__ import annotations

"""Pure semantic router for bounded autonomous repair domains.

The router owns no write, GitHub, merge, test, or production authority. It turns
already-collected failure evidence into one fail-closed repair class and exact
candidate scope. Product-source behavior remains unchanged. Control-plane
self-repair is deliberately narrower: a failed unittest/pytest module must pair
by name with an exact source-PR change under ``scripts/verify_engineering_*.py``.
Tests/oracles are evidence only and never become writable here.
"""

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

ROUTE_SCHEMA = "engineering-autonomous-repair-route@1"

PRODUCT_CODE_REPAIRABLE = "PRODUCT_CODE_REPAIRABLE"
CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE = "CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE"
TEST_HARNESS_REPAIRABLE = "TEST_HARNESS_REPAIRABLE"
TRANSIENT_INFRA_RETRYABLE = "TRANSIENT_INFRA_RETRYABLE"
ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
AUTHORITY_ORACLE_CHANGE_REQUIRED = "AUTHORITY_ORACLE_CHANGE_REQUIRED"
HUMAN_GATE = "HUMAN_GATE"
UNKNOWN = "UNKNOWN"

REPAIR_DOMAIN_PRODUCT = "PRODUCT_CODE"
REPAIR_DOMAIN_CONTROL_PLANE = "CONTROL_PLANE_IMPLEMENTATION"
REPAIR_DOMAIN_NONE = "NONE"

_CONTROL_PREFIX = "scripts/verify_engineering_"
_CONTROL_SUFFIX = ".py"
_TEST_MODULE_RE = re.compile(r"\((test_[A-Za-z0-9_]+)\.[A-Za-z0-9_.]+\)")
_PYTEST_MODULE_RE = re.compile(r"(?:^|[\s/])(test_[A-Za-z0-9_]+)\.py(?::|\s)", re.MULTILINE)
_FAILURE_MARKERS = (
    "assertionerror",
    "traceback (most recent call last)",
    "failed (failures=",
    " failed ",
    "== failures ==",
    "error:",
)
_TEST_PARTS = frozenset({"test", "tests", "e2e", "__tests__"})

_NONREPAIRABLE_CLASSIFICATION_MAP = {
    "environment": ENVIRONMENT_BLOCKED,
    "timeout": TRANSIENT_INFRA_RETRYABLE,
    "cancelled": TRANSIENT_INFRA_RETRYABLE,
    "runner_or_platform": TRANSIENT_INFRA_RETRYABLE,
    "stale": TRANSIENT_INFRA_RETRYABLE,
    "policy_or_approval": HUMAN_GATE,
    "protected_baseline_drift": AUTHORITY_ORACLE_CHANGE_REQUIRED,
    "production_diagnostic": HUMAN_GATE,
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _path(value: object) -> str:
    raw = _text(value).replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return ""
    return pure.as_posix() if pure.as_posix() == raw else ""


def _unique_paths(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        path = _path(value)
        if path and path not in result:
            result.append(path)
    return result


def _is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    parts = {part.casefold() for part in pure.parts}
    name = pure.name.casefold()
    return bool(parts & _TEST_PARTS or name.startswith("test_") or ".test." in name or ".spec." in name)


def _is_bounded_control_implementation(path: str) -> bool:
    return path.startswith(_CONTROL_PREFIX) and path.endswith(_CONTROL_SUFFIX) and not _is_test_path(path)


def _implementation_test_module(path: str) -> str:
    stem = PurePosixPath(path).stem
    if not stem.startswith("verify_"):
        return ""
    return "test_" + stem[len("verify_") :]


def _failed_test_modules(text: str) -> list[str]:
    result: list[str] = []
    for pattern in (_TEST_MODULE_RE, _PYTEST_MODULE_RE):
        for match in pattern.finditer(text):
            module = match.group(1)
            if module not in result:
                result.append(module)
    return result


def _has_executable_test_failure(text: str) -> bool:
    low = text.casefold()
    return bool(_failed_test_modules(text) and any(marker in low for marker in _FAILURE_MARKERS))


def _base_route(
    *,
    repair_class: str,
    repair_domain: str,
    automatic_write_allowed: bool,
    human_required: bool,
    allowed_write_paths: list[str],
    reason: str,
    failed_test_modules: list[str],
    evidence_refs: list[str],
) -> dict[str, Any]:
    route: dict[str, Any] = {
        "schema": ROUTE_SCHEMA,
        "repair_class": repair_class,
        "repair_domain": repair_domain,
        "automatic_write_allowed": bool(automatic_write_allowed),
        "human_required": bool(human_required),
        "allowed_write_paths": list(allowed_write_paths),
        "failed_test_modules": list(failed_test_modules),
        "reason": reason,
        "evidence_refs": list(evidence_refs),
        "test_write_allowed": False,
        "acceptance_write_allowed": False,
        "oracle_write_allowed": False,
        "scope_expansion_allowed": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    route["route_sha256"] = _digest(route)
    return route


def route_failure(
    *,
    workflow_name: str,
    conclusion: str,
    legacy_classification: str,
    combined_text: str,
    failed_gates: Iterable[Mapping[str, Any]] = (),
    legacy_candidate_paths: Iterable[object] = (),
    source_changed_files: Iterable[object] = (),
    same_repository: bool,
) -> dict[str, Any]:
    """Return one semantic repair route without creating authority.

    The control-plane route is intentionally based on three independent facts:
    a real executable test failure, a failed test module name, and an exact
    source-PR change whose verifier name pairs with that failed test module.
    Changed-file metadata alone can never create this route.
    """

    workflow = _text(workflow_name)
    normalized_conclusion = _text(conclusion).casefold()
    classification = _text(legacy_classification).casefold()
    changed = _unique_paths(source_changed_files)
    legacy_candidates = _unique_paths(legacy_candidate_paths)
    test_modules = _failed_test_modules(combined_text)
    evidence_refs = [
        f"workflow:{workflow or 'unknown'}",
        f"conclusion:{normalized_conclusion or 'unknown'}",
        f"legacy-classification:{classification or 'unknown'}",
    ]
    for row in failed_gates:
        gate = _text(row.get("gate_id"))
        if gate:
            evidence_refs.append(f"gate:{gate}")
    for module in test_modules:
        evidence_refs.append(f"failed-test-module:{module}")

    if not same_repository:
        return _base_route(
            repair_class=HUMAN_GATE,
            repair_domain=REPAIR_DOMAIN_NONE,
            automatic_write_allowed=False,
            human_required=True,
            allowed_write_paths=[],
            reason="foreign/fork failure cannot receive repository write authority",
            failed_test_modules=test_modules,
            evidence_refs=evidence_refs,
        )

    mapped = _NONREPAIRABLE_CLASSIFICATION_MAP.get(classification)
    if mapped:
        return _base_route(
            repair_class=mapped,
            repair_domain=REPAIR_DOMAIN_NONE,
            automatic_write_allowed=False,
            human_required=mapped not in {TRANSIENT_INFRA_RETRYABLE, ENVIRONMENT_BLOCKED},
            allowed_write_paths=[],
            reason=f"legacy classification {classification} remains outside source-write authority",
            failed_test_modules=test_modules,
            evidence_refs=evidence_refs,
        )

    if classification == "code_or_contract" and legacy_candidates:
        return _base_route(
            repair_class=PRODUCT_CODE_REPAIRABLE,
            repair_domain=REPAIR_DOMAIN_PRODUCT,
            automatic_write_allowed=True,
            human_required=False,
            allowed_write_paths=legacy_candidates,
            reason="existing product code/contract failure retains historical product repair authority",
            failed_test_modules=test_modules,
            evidence_refs=evidence_refs,
        )

    if workflow == "quality" and normalized_conclusion == "failure" and _has_executable_test_failure(combined_text):
        matched: list[str] = []
        for path in changed:
            if not _is_bounded_control_implementation(path):
                continue
            expected_module = _implementation_test_module(path)
            if expected_module and expected_module in test_modules and path not in matched:
                matched.append(path)
        if matched:
            return _base_route(
                repair_class=CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE,
                repair_domain=REPAIR_DOMAIN_CONTROL_PLANE,
                automatic_write_allowed=True,
                human_required=False,
                allowed_write_paths=matched,
                reason=(
                    "executable control-plane test failure pairs with exact changed engineering verifier "
                    "implementation; tests/oracles remain read-only"
                ),
                failed_test_modules=test_modules,
                evidence_refs=evidence_refs,
            )

        changed_tests = [path for path in changed if _is_test_path(path)]
        if changed_tests:
            return _base_route(
                repair_class=TEST_HARNESS_REPAIRABLE,
                repair_domain=REPAIR_DOMAIN_NONE,
                automatic_write_allowed=False,
                human_required=True,
                allowed_write_paths=[],
                reason=(
                    "test-harness failure is evident but no bounded implementation pairing exists; "
                    "automatic test/oracle writes are forbidden"
                ),
                failed_test_modules=test_modules,
                evidence_refs=evidence_refs,
            )

    return _base_route(
        repair_class=UNKNOWN,
        repair_domain=REPAIR_DOMAIN_NONE,
        automatic_write_allowed=False,
        human_required=True,
        allowed_write_paths=[],
        reason="failure evidence is insufficient for a bounded automatic write domain",
        failed_test_modules=test_modules,
        evidence_refs=evidence_refs,
    )


def validate_route(route: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(route)
    if payload.get("schema") != ROUTE_SCHEMA:
        raise ValueError("unsupported autonomous repair route schema")
    supplied = _text(payload.pop("route_sha256", None))
    if not re.fullmatch(r"[0-9a-f]{64}", supplied) or supplied != _digest(payload):
        raise ValueError("autonomous repair route digest mismatch")
    allowed = _unique_paths(payload.get("allowed_write_paths") or [])
    if allowed != list(payload.get("allowed_write_paths") or []):
        raise ValueError("autonomous repair route write scope is malformed")
    if any(payload.get(field) is not False for field in (
        "test_write_allowed",
        "acceptance_write_allowed",
        "oracle_write_allowed",
        "scope_expansion_allowed",
        "merge_allowed",
        "deploy_allowed",
        "production_closed",
    )):
        raise ValueError("autonomous repair route crossed a protected authority boundary")
    if payload.get("automatic_write_allowed") is True:
        if payload.get("human_required") is not False or not allowed:
            raise ValueError("automatic route requires exact non-empty scope and no Human Gate")
        if payload.get("repair_class") == CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE:
            if payload.get("repair_domain") != REPAIR_DOMAIN_CONTROL_PLANE:
                raise ValueError("control-plane route domain drift")
            if any(not _is_bounded_control_implementation(path) for path in allowed):
                raise ValueError("control-plane route contains an unbounded implementation path")
        elif payload.get("repair_class") == PRODUCT_CODE_REPAIRABLE:
            if payload.get("repair_domain") != REPAIR_DOMAIN_PRODUCT:
                raise ValueError("product route domain drift")
        else:
            raise ValueError("unsupported automatic repair class")
    payload["route_sha256"] = supplied
    return payload
