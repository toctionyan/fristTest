from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from source_paths import is_runtime_artifact_path

from .common import (
    _canonical_json_fingerprint, _is_target_placeholder, _load_json, _python_selector_exists,
    _read, _safe_workspace_relative_json, _sha256_file, _sha256_text, _target_fingerprint,
    _target_metadata, _target_section, _verify_evidence_attestation,
)
from .constants import *

def _load_requirement_profile(
    workspace: Path,
    *,
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    raw_catalog = str(manifest.get("requirement_catalog") or "").strip()
    raw_profile = str(manifest.get("requirement_profile") or "").strip()
    if not raw_catalog and not raw_profile:
        return None
    if not raw_catalog or not raw_profile:
        raise ValueError(
            "claim manifest must declare requirement_catalog and requirement_profile together"
        )
    catalog_path = _safe_workspace_relative_json(
        workspace, raw_catalog, field="claim manifest requirement_catalog"
    )
    if not catalog_path.is_file():
        raise ValueError(
            "requirement catalog does not exist: "
            + catalog_path.relative_to(workspace.resolve()).as_posix()
        )
    catalog = _load_json(catalog_path)
    catalog_schema_version = int(catalog.get("schema_version") or 0)
    if catalog_schema_version not in {1, 2}:
        raise ValueError("requirement catalog schema_version must be 1 or 2")
    inventory_payload: dict[str, Any] | None = None
    inventory_ids: set[str] = set()
    if catalog_schema_version == 2:
        raw_inventory = str(catalog.get("inventory") or "").strip()
        if not raw_inventory:
            raise ValueError("requirement catalog v2 must declare an inventory path")
        inventory_path = _safe_workspace_relative_json(
            workspace, raw_inventory, field="requirement catalog inventory"
        )
        if not inventory_path.is_file():
            raise ValueError(f"requirement inventory does not exist: {raw_inventory}")
        inventory_payload = _load_json(inventory_path)
        if inventory_payload.get("schema_version") != 1:
            raise ValueError("product capability inventory schema_version must be 1")
        raw_capabilities = inventory_payload.get("capabilities")
        if not isinstance(raw_capabilities, list) or not raw_capabilities:
            raise ValueError("product capability inventory must contain capabilities")
        for index, capability in enumerate(raw_capabilities, start=1):
            if not isinstance(capability, dict):
                raise ValueError(f"inventory capability #{index} must be an object")
            capability_id = str(capability.get("id") or "").strip()
            if not capability_id or capability_id in inventory_ids:
                raise ValueError(f"inventory capability #{index} has an invalid or duplicate id")
            if not str(capability.get("owner") or "").strip() or not str(capability.get("surface") or "").strip():
                raise ValueError(f"inventory capability {capability_id} must declare owner and surface")
            inventory_ids.add(capability_id)
    raw_requirements = catalog.get("requirements")
    raw_profiles = catalog.get("profiles")
    if not isinstance(raw_requirements, list) or not raw_requirements:
        raise ValueError("requirement catalog must contain a non-empty requirements array")
    if not isinstance(raw_profiles, dict):
        raise ValueError("requirement catalog must contain profiles")

    requirements: dict[str, dict[str, Any]] = {}
    mapped_inventory_ids: set[str] = set()
    for index, item in enumerate(raw_requirements, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"requirement #{index} must be an object")
        requirement_id = str(item.get("id") or "").strip()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._:-]{2,127}", requirement_id):
            raise ValueError(f"requirement #{index} has an invalid id")
        if requirement_id in requirements:
            raise ValueError(f"duplicate requirement id: {requirement_id}")
        statement = str(item.get("statement") or "").strip()
        risk = str(item.get("risk") or "").upper()
        owner = str(item.get("owner") or "").strip()
        required_mode = str(item.get("required_mode") or "").lower()
        if not statement or not owner:
            raise ValueError(
                f"requirement {requirement_id} must declare statement and owner"
            )
        if risk not in CLAIM_RISKS or required_mode not in MODE_RANK:
            raise ValueError(
                f"requirement {requirement_id} has invalid risk or required_mode"
            )
        normalized_requirement: dict[str, Any] = {
            "statement": statement,
            "risk": risk,
            "owner": owner,
            "required_mode": required_mode,
        }
        if catalog_schema_version == 2:
            invariant = str(item.get("invariant") or "").strip()
            failure_class = str(item.get("failure_class") or "").strip()
            raw_strategies = item.get("required_strategies")
            raw_inventory_ids = item.get("inventory_ids")
            if not invariant or not failure_class:
                raise ValueError(f"requirement {requirement_id} must declare invariant and failure_class")
            if not isinstance(raw_strategies, list) or not raw_strategies:
                raise ValueError(f"requirement {requirement_id} must declare required_strategies")
            strategies = [str(value).strip() for value in raw_strategies]
            if any(not value for value in strategies) or len(strategies) != len(set(strategies)):
                raise ValueError(f"requirement {requirement_id} required_strategies must be unique non-empty values")
            if risk in {"P0", "P1"} and not {"counterexample", "mutation"}.issubset(strategies):
                raise ValueError(f"high-risk requirement {requirement_id} requires counterexample and mutation strategies")
            if not isinstance(raw_inventory_ids, list) or not raw_inventory_ids:
                raise ValueError(f"requirement {requirement_id} must map inventory_ids")
            owned_inventory_ids = [str(value).strip() for value in raw_inventory_ids]
            unknown_inventory = sorted(set(owned_inventory_ids).difference(inventory_ids))
            if unknown_inventory:
                raise ValueError(f"requirement {requirement_id} maps unknown inventory ids: {unknown_inventory}")
            normalized_requirement.update({
                "invariant": invariant,
                "failure_class": failure_class,
                "required_strategies": strategies,
                "inventory_ids": owned_inventory_ids,
            })
            mapped_inventory_ids.update(owned_inventory_ids)
        requirements[requirement_id] = normalized_requirement

    if catalog_schema_version == 2:
        missing_inventory = sorted(inventory_ids.difference(mapped_inventory_ids))
        unknown_mapped = sorted(mapped_inventory_ids.difference(inventory_ids))
        if missing_inventory or unknown_mapped:
            raise ValueError(
                "requirement catalog does not exactly cover product capability inventory; "
                f"missing={missing_inventory}, unknown={unknown_mapped}"
            )
        cumulative_profiles = ["project-quick", "project-integration", "project-product", "project-release"]
        if all(name in raw_profiles for name in cumulative_profiles):
            previous: set[str] = set()
            for name in cumulative_profiles:
                values = raw_profiles.get(name)
                if not isinstance(values, list) or not values:
                    raise ValueError(f"cumulative requirement profile is missing or empty: {name}")
                current = {str(value).strip() for value in values}
                if previous and not previous < current:
                    raise ValueError(f"requirement profile {name} must strictly include its lower certification profile")
                previous = current

    profile = raw_profiles.get(raw_profile)
    if not isinstance(profile, list) or not profile:
        raise ValueError(f"requirement profile is missing or empty: {raw_profile}")
    profile_ids = [str(value).strip() for value in profile]
    if any(not value for value in profile_ids) or len(profile_ids) != len(set(profile_ids)):
        raise ValueError(f"requirement profile {raw_profile} must contain unique ids")
    unknown = sorted(set(profile_ids).difference(requirements))
    if unknown:
        raise ValueError(
            f"requirement profile {raw_profile} references unknown requirements: {unknown}"
        )
    effective_payload: dict[str, Any] = {"catalog": catalog}
    if inventory_payload is not None:
        effective_payload["inventory"] = inventory_payload
    return {
        "path": catalog_path.relative_to(workspace.resolve()).as_posix(),
        "profile": raw_profile,
        "requirements": requirements,
        "profile_ids": profile_ids,
        "payload": effective_payload,
        "fingerprint": _canonical_json_fingerprint(effective_payload),
    }

def _validate_source_claim_binding(
    workspace: Path,
    *,
    manifest: dict[str, Any],
) -> None:
    source = manifest.get("source_claim_manifest")
    if source is None:
        return
    if not isinstance(source, dict):
        raise ValueError("source_claim_manifest must be an object")
    raw_path = str(source.get("path") or "")
    source_path = _safe_workspace_relative_json(
        workspace, raw_path, field="source_claim_manifest.path"
    )
    if not source_path.is_file():
        raise ValueError(f"source claim manifest does not exist: {raw_path}")
    source_payload = _load_json(source_path)
    expected_fingerprint = str(source.get("fingerprint") or "")
    if _canonical_json_fingerprint(source_payload) != expected_fingerprint:
        raise ValueError("source claim manifest fingerprint does not match")
    if str(source_payload.get("target_id") or "") != str(source.get("target_id") or ""):
        raise ValueError("source claim manifest target_id does not match")
    if source_payload.get("claims") != manifest.get("claims"):
        raise ValueError("generated CI claims do not exactly match the bound source manifest")

def _load_claim_manifest(
    workspace: Path,
    *,
    target_id: str,
    raw_path: str,
) -> dict[str, Any]:
    manifest_path = _safe_workspace_relative_json(workspace, raw_path, field="target 声明清单")
    if not manifest_path.is_file():
        raise ValueError(
            f"target 声明清单 does not exist: {manifest_path.relative_to(workspace.resolve())}"
        )
    payload = _load_json(manifest_path)
    if payload.get("schema_version") != CLAIM_SCHEMA_VERSION:
        raise ValueError(
            f"claim manifest schema_version must be {CLAIM_SCHEMA_VERSION}"
        )
    if str(payload.get("target_id") or "") != target_id:
        raise ValueError("claim manifest target_id does not match target 目标 ID")
    _validate_source_claim_binding(workspace, manifest=payload)
    requirement_profile = _load_requirement_profile(workspace, manifest=payload)
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise ValueError("claim manifest must contain a non-empty claims array")
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    covered_requirement_ids: set[str] = set()
    requirement_contract_errors: list[str] = []
    risk_rank = {"P0": 3, "P1": 2, "P2": 1, "P3": 0}
    for index, item in enumerate(raw_claims, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"claim #{index} must be an object")
        claim_id = str(item.get("id") or "").strip()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._:-]{2,127}", claim_id):
            raise ValueError(f"claim #{index} has an invalid id")
        if claim_id in seen:
            raise ValueError(f"duplicate claim id: {claim_id}")
        seen.add(claim_id)
        statement = str(item.get("statement") or "").strip()
        risk = str(item.get("risk") or "").upper()
        required_mode = str(item.get("required_mode") or "").lower()
        evidence_kind = str(item.get("evidence_kind") or "").lower()
        owner = str(item.get("owner") or "").strip()
        closure_requirement = str(item.get("closure_requirement") or "").lower().strip()
        required_gates = item.get("required_gates")
        evidence_refs = item.get("evidence_refs")
        raw_requirement_ids = item.get("requirement_ids")
        if not statement:
            raise ValueError(f"claim {claim_id} must declare statement")
        if risk not in CLAIM_RISKS:
            raise ValueError(f"claim {claim_id} risk must be one of {sorted(CLAIM_RISKS)}")
        if required_mode not in MODE_RANK:
            raise ValueError(f"claim {claim_id} required_mode is invalid")
        if evidence_kind not in CLAIM_EVIDENCE_KINDS:
            raise ValueError(
                f"claim {claim_id} evidence_kind must be one of {sorted(CLAIM_EVIDENCE_KINDS)}"
            )
        if not owner:
            raise ValueError(f"claim {claim_id} must declare owner")
        if closure_requirement not in CLAIM_CLOSURE_REQUIREMENTS:
            raise ValueError(
                f"claim {claim_id} closure_requirement must be one of "
                f"{sorted(CLAIM_CLOSURE_REQUIREMENTS)}"
            )
        if not isinstance(required_gates, list) or not required_gates:
            raise ValueError(f"claim {claim_id} must declare required_gates")
        gates = [str(value).strip() for value in required_gates if str(value).strip()]
        if len(gates) != len(required_gates) or len(set(gates)) != len(gates):
            raise ValueError(f"claim {claim_id} required_gates must be unique non-empty ids")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise ValueError(f"claim {claim_id} must declare evidence_refs")
        refs = [str(value).strip() for value in evidence_refs if str(value).strip()]
        if len(refs) != len(evidence_refs):
            raise ValueError(f"claim {claim_id} evidence_refs must be non-empty strings")
        normalized_refs: list[str] = []
        for ref in refs:
            if ref.startswith("gate-log:"):
                gate_id = ref.removeprefix("gate-log:").strip()
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,127}", gate_id):
                    raise ValueError(f"claim {claim_id} has invalid gate-log evidence ref: {ref}")
                normalized_refs.append(f"gate-log:{gate_id}")
                continue
            path_text = ref.split("::", 1)[0].strip()
            ref_path = Path(path_text)
            if not path_text or ref_path.is_absolute() or ".." in ref_path.parts:
                raise ValueError(f"claim {claim_id} evidence ref must be a safe workspace path or gate-log: {ref}")
            resolved = (workspace / ref_path).resolve()
            try:
                resolved.relative_to(workspace.resolve())
            except ValueError as exc:
                raise ValueError(f"claim {claim_id} evidence ref escapes the workspace: {ref}") from exc
            if not resolved.is_file():
                raise ValueError(f"claim {claim_id} evidence ref does not exist: {ref}")
            selector = ref.split("::", 1)[1].strip() if "::" in ref else ""
            if selector:
                if resolved.suffix.lower() != ".py":
                    raise ValueError(
                        f"claim {claim_id} executable selector must reference a Python test file: {ref}"
                    )
                if not _python_selector_exists(resolved, selector):
                    raise ValueError(f"claim {claim_id} test selector does not exist: {ref}")
            normalized_refs.append(ref)
        if risk in {"P0", "P1"} and evidence_kind not in {
            "counterexample",
            "integration",
            "release-provenance",
        }:
            raise ValueError(
                f"high-risk claim {claim_id} cannot rely only on static-contract evidence"
            )
        if (
            risk in {"P0", "P1"}
            and evidence_kind in {"counterexample", "integration", "release-provenance"}
            and not any(ref.startswith("gate-log:") or "::" in ref for ref in normalized_refs)
        ):
            raise ValueError(
                f"high-risk {evidence_kind} claim {claim_id} requires direct executable evidence "
                "via a test selector or gate-log"
            )
        requirement_ids: list[str] = []
        if requirement_profile is not None:
            if not isinstance(raw_requirement_ids, list) or not raw_requirement_ids:
                raise ValueError(
                    f"claim {claim_id} must map at least one requirement_id from "
                    f"profile {requirement_profile['profile']}"
                )
            requirement_ids = [str(value).strip() for value in raw_requirement_ids]
            if (
                any(not value for value in requirement_ids)
                or len(requirement_ids) != len(set(requirement_ids))
            ):
                raise ValueError(
                    f"claim {claim_id} requirement_ids must be unique non-empty ids"
                )
            unknown_requirements = sorted(
                set(requirement_ids).difference(requirement_profile["requirements"])
            )
            if unknown_requirements:
                raise ValueError(
                    f"claim {claim_id} maps unknown requirements: {unknown_requirements}"
                )
            for requirement_id in requirement_ids:
                requirement = requirement_profile["requirements"][requirement_id]
                if risk_rank[risk] < risk_rank[requirement["risk"]]:
                    requirement_contract_errors.append(
                        f"claim {claim_id} understates risk for requirement {requirement_id}"
                    )
                if MODE_RANK[required_mode] < MODE_RANK[requirement["required_mode"]]:
                    requirement_contract_errors.append(
                        f"claim {claim_id} understates required_mode for requirement {requirement_id}"
                    )
            covered_requirement_ids.update(requirement_ids)
        elif raw_requirement_ids is not None:
            raise ValueError(
                f"claim {claim_id} declares requirement_ids without a requirement catalog/profile"
            )
        normalized_claim = {
            "id": claim_id,
            "statement": statement,
            "risk": risk,
            "required_mode": required_mode,
            "evidence_kind": evidence_kind,
            "required_gates": gates,
            "evidence_refs": normalized_refs,
            "owner": owner,
            "closure_requirement": closure_requirement,
        }
        if requirement_profile is not None:
            normalized_claim["requirement_ids"] = requirement_ids
        claims.append(normalized_claim)
    if requirement_profile is not None:
        required_ids = set(requirement_profile["profile_ids"])
        missing_requirements = sorted(required_ids.difference(covered_requirement_ids))
        out_of_profile = sorted(covered_requirement_ids.difference(required_ids))
        if missing_requirements or out_of_profile:
            raise ValueError(
                "claim manifest does not exactly cover the active requirement profile; "
                f"missing={missing_requirements}, out_of_profile={out_of_profile}"
            )
        if requirement_contract_errors:
            raise ValueError("; ".join(requirement_contract_errors))
    relative = manifest_path.relative_to(workspace.resolve()).as_posix()
    effective_payload: dict[str, Any] = payload
    if requirement_profile is not None:
        effective_payload = {
            "manifest": payload,
            "requirement_catalog": requirement_profile["payload"],
        }
    return {
        "path": relative,
        "fingerprint": _canonical_json_fingerprint(effective_payload),
        "claims": claims,
        "requirement_catalog": (
            requirement_profile["path"] if requirement_profile is not None else None
        ),
        "requirement_profile": (
            requirement_profile["profile"] if requirement_profile is not None else None
        ),
        "requirement_catalog_fingerprint": (
            requirement_profile["fingerprint"] if requirement_profile is not None else None
        ),
    }

def _allowed_change_paths(section: str, *, context: str) -> tuple[str, ...]:
    """Parse the frozen, machine-checkable scope line in a target record."""
    raw = _target_metadata(section, "允许变更路径")
    if raw is None:
        raise ValueError("target 允许范围 must declare 允许变更路径：path/**, other/file")
    patterns = tuple(
        item.strip().strip("`")
        for item in re.split(r"[,，;；]", raw)
        if item.strip().strip("`")
    )
    if not patterns:
        raise ValueError("target 允许变更路径 must not be empty")
    for pattern in patterns:
        parts = Path(pattern).parts
        if (
            pattern.startswith("/")
            or "\\" in pattern
            or ".." in parts
            or any(character.isspace() for character in pattern)
        ):
            raise ValueError(f"target 允许变更路径 contains an unsafe pattern: {pattern}")
    if context == "local-change" and any(pattern in {"*", "**"} for pattern in patterns):
        raise ValueError("local-change target may not use a workspace-wide 允许变更路径 wildcard")
    return patterns

def _snapshot_ignored(relative: Path) -> bool:
    return (
        relative.as_posix() == "governance/active-change.json"
        or any(part in SNAPSHOT_IGNORED_PARTS for part in relative.parts)
        or is_runtime_artifact_path(relative)
        or relative.name in SNAPSHOT_IGNORED_NAMES
        or relative.name.endswith(".quality-run.lock")
        or relative.suffix in SNAPSHOT_IGNORED_SUFFIXES
    )

def _workspace_snapshot(
    workspace: Path, *, ignored_roots: tuple[Path, ...] = ()
) -> dict[str, Any]:
    """Hash source/release inputs while excluding runtime/evidence state."""
    workspace = workspace.resolve()
    ignored = []
    for root in ignored_roots:
        try:
            ignored.append(root.resolve().relative_to(workspace))
        except ValueError:
            continue

    def explicitly_ignored(relative: Path) -> bool:
        return any(relative == root or root in relative.parents for root in ignored)

    files: dict[str, str] = {}
    for root, directories, filenames in os.walk(workspace):
        root_path = Path(root)
        relative_root = root_path.relative_to(workspace)
        directories[:] = [
            name
            for name in directories
            if not _snapshot_ignored(relative_root / name)
            and not explicitly_ignored(relative_root / name)
        ]
        for filename in filenames:
            relative = relative_root / filename
            if _snapshot_ignored(relative) or explicitly_ignored(relative):
                continue
            path = root_path / filename
            if path.is_file():
                files[relative.as_posix()] = _sha256_file(path)
    encoded = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": 1,
        "files": files,
        "fingerprint": _sha256_text(encoded),
    }

def workspace_snapshot(
    workspace: Path, *, ignored_roots: tuple[Path, ...] = ()
) -> dict[str, Any]:
    """Public source-identity primitive shared by release construction."""
    return _workspace_snapshot(workspace.resolve(), ignored_roots=ignored_roots)

def _scope_violations(
    workspace: Path,
    *,
    baseline: dict[str, Any],
    allowed_paths: tuple[str, ...],
    ignored_roots: tuple[Path, ...] = (),
) -> list[str]:
    snapshot = baseline.get("workspace_snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("files"), dict):
        raise ValueError("baseline evidence does not contain a valid workspace source snapshot")
    before = {str(path): str(digest) for path, digest in snapshot["files"].items()}
    after = {
        str(path): str(digest)
        for path, digest in _workspace_snapshot(workspace, ignored_roots=ignored_roots)["files"].items()
    }
    changed = sorted(
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    )
    return [
        path
        for path in changed
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed_paths)
    ]

def _repair_change_fingerprint(
    workspace: Path,
    *,
    baseline: dict[str, Any],
    allowed_paths: tuple[str, ...],
    target_path: Path,
    ignored_roots: tuple[Path, ...] = (),
) -> tuple[str, list[str]]:
    """Fingerprint real in-scope repairs while excluding round bookkeeping."""
    snapshot = baseline.get("workspace_snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("files"), dict):
        raise ValueError("baseline evidence does not contain a valid workspace source snapshot")
    before = {str(path): str(digest) for path, digest in snapshot["files"].items()}
    after = {
        str(path): str(digest)
        for path, digest in _workspace_snapshot(workspace, ignored_roots=ignored_roots)["files"].items()
    }
    try:
        target_relative = target_path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        target_relative = ""
    changed = {
        path: after.get(path)
        for path in sorted(set(before) | set(after))
        if path != target_relative
        and before.get(path) != after.get(path)
        and any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed_paths)
    }
    encoded = json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(encoded), list(changed)

def _new_production_source_paths(
    workspace: Path,
    *,
    baseline: dict[str, Any],
    ignored_roots: tuple[Path, ...] = (),
) -> list[str]:
    snapshot = baseline.get("workspace_snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("files"), dict):
        raise ValueError("baseline evidence does not contain a valid workspace source snapshot")
    before = {str(path) for path in snapshot["files"]}
    after = set(_workspace_snapshot(workspace, ignored_roots=ignored_roots)["files"])
    return sorted(
        path
        for path in after - before
        if path.endswith(".py") and any(path.startswith(prefix) for prefix in PRODUCTION_SOURCE_PREFIXES)
    )

def _validate_abstraction_record(
    workspace: Path,
    *,
    baseline: dict[str, Any],
    target: dict[str, Any],
    ignored_roots: tuple[Path, ...] = (),
) -> None:
    new_sources = _new_production_source_paths(
        workspace, baseline=baseline, ignored_roots=ignored_roots
    )
    record = str(target["new_abstraction_record"])
    if record.lower() in {"无", "none", "not-applicable", "ci-not-applicable"}:
        if new_sources:
            raise ValueError(
                "new production source files require an 新增抽象记录 instead of 无: "
                + ", ".join(new_sources[:20])
            )
        return
    path = Path(record)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md":
        raise ValueError("新增抽象记录 must be a safe workspace-relative .md path or 无")
    record_path = workspace / path
    if not record_path.is_file():
        raise ValueError(f"新增抽象记录 does not exist: {record}")
    body = _read(record_path)
    if "<!--" in body or any(marker not in body for marker in ABSTRACTION_RECORD_MARKERS):
        raise ValueError("新增抽象记录 is incomplete; use the required replacement/verification fields")

def _parse_target(target: Path, *, workspace: Path) -> dict[str, Any]:
    if not target.is_file():
        raise ValueError(f"target record does not exist: {target}")
    text = _read(target)
    missing = [heading for heading in TARGET_HEADINGS if heading not in text]
    if missing:
        raise ValueError("target record is incomplete; missing: " + ", ".join(missing))
    sections = {heading: _target_section(text, heading) for heading in TARGET_HEADINGS}
    for heading, body in sections.items():
        if not body or "<!--" in body or "-->" in body:
            raise ValueError(f"target record section is empty or still a template: {heading}")
    objective = sections["# 目标"]
    target_id = _target_metadata(objective, "目标 ID")
    change_ref = _target_metadata(objective, "变更标识")
    raw_context = (_target_metadata(objective, "执行上下文") or "").lower()
    target_kind = (_target_metadata(objective, "目标类型") or "").lower()
    raw_replan_evidence = _target_metadata(objective, "重规划来源证据")
    raw_replan_gate = _target_metadata(objective, "重规划失败 Gate")
    context_aliases = {"ci": "ci", "local-change": "local-change", "本地变更": "local-change"}
    context = context_aliases.get(raw_context)
    if not target_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", target_id):
        raise ValueError("target record must declare a stable 目标 ID using [A-Za-z0-9._:-]")
    if _is_target_placeholder(target_id):
        raise ValueError("target record 目标 ID is still a template placeholder")
    if _is_target_placeholder(change_ref) or (change_ref or "").strip().lower() in {"commit-sha", "pr-number", "working-tree"}:
        raise ValueError("target record must declare a non-placeholder 变更标识")
    if context not in TARGET_CONTEXTS:
        raise ValueError("target record must declare 执行上下文：local-change or ci")
    if target_kind not in TARGET_KINDS:
        raise ValueError("target record must declare 目标类型：diagnosis, design, oracle-review, repair, migration, revert or certification")
    if bool(raw_replan_evidence) != bool(raw_replan_gate):
        raise ValueError("target must declare 重规划来源证据 and 重规划失败 Gate together")
    replan_evidence: str | None = None
    replan_failed_gate: str | None = None
    if raw_replan_evidence and raw_replan_gate:
        if context != "local-change":
            raise ValueError("architecture replan lineage is only valid for local-change targets")
        replan_evidence = raw_replan_evidence.strip().strip("`")
        replan_path = Path(replan_evidence)
        if (
            not replan_evidence
            or replan_path.is_absolute()
            or ".." in replan_path.parts
        ):
            raise ValueError("重规划来源证据 must be a safe workspace-relative evidence directory")
        try:
            (workspace / replan_path).resolve().relative_to(workspace.resolve())
        except ValueError as exc:
            raise ValueError("重规划来源证据 escapes the workspace") from exc
        replan_failed_gate = raw_replan_gate.strip().strip("`")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,127}", replan_failed_gate):
            raise ValueError("重规划失败 Gate must be a valid Gate id")
    allowed_paths = _allowed_change_paths(sections["## 允许范围"], context=context)
    new_abstraction_record = _target_metadata(sections["## 允许范围"], "新增抽象记录")
    if not new_abstraction_record:
        raise ValueError("target 允许范围 must declare 新增抽象记录：relative/path.md or 无")
    minimum_mode = (_target_metadata(sections["## 验收条件"], "最低质量模式") or "").lower()
    if minimum_mode not in MODE_RANK:
        raise ValueError("target 验收条件 must declare 最低质量模式：static, quick, integration or release")
    claim_manifest_raw = _target_metadata(sections["## 验收条件"], "声明清单")
    if not claim_manifest_raw:
        raise ValueError("target 验收条件 must declare 声明清单：governance/claims/<name>.json")
    claim_manifest = _load_claim_manifest(
        workspace,
        target_id=target_id,
        raw_path=claim_manifest_raw,
    )
    acceptance_ids_raw = _target_metadata(sections["## 验收条件"], "验收 ID")
    if not acceptance_ids_raw:
        raise ValueError("target 验收条件 must declare acceptance IDs with 验收 ID")
    acceptance_ids = [
        value.strip().strip("`")
        for value in re.split(r"[,，;；]", acceptance_ids_raw)
        if value.strip().strip("`")
    ]
    claim_ids = [str(claim["id"]) for claim in claim_manifest["claims"]]
    if len(acceptance_ids) != len(set(acceptance_ids)) or set(acceptance_ids) != set(claim_ids):
        missing_ids = sorted(set(claim_ids).difference(acceptance_ids))
        extra_ids = sorted(set(acceptance_ids).difference(claim_ids))
        raise ValueError(
            "target acceptance IDs must exactly match claim manifest IDs; "
            f"missing={missing_ids}, extra={extra_ids}"
        )
    if target_kind in TRANSITION_TARGET_KINDS:
        invalid_closure = [
            str(claim["id"])
            for claim in claim_manifest["claims"]
            if claim["closure_requirement"] != "regression-transition"
        ]
        if invalid_closure:
            raise ValueError(
                f"{target_kind} target claims must require regression-transition closure: "
                + ", ".join(invalid_closure)
            )
    derived_minimum_mode = max(
        (str(claim["required_mode"]) for claim in claim_manifest["claims"]),
        key=lambda value: MODE_RANK[value],
    )
    if MODE_RANK[minimum_mode] < MODE_RANK[derived_minimum_mode]:
        raise ValueError(
            "target 最低质量模式 is lower than the claim-derived mode: "
            f"declared={minimum_mode}, derived={derived_minimum_mode}"
        )
    effective_minimum_mode = max(
        (minimum_mode, derived_minimum_mode), key=lambda value: MODE_RANK[value]
    )
    maximum = re.search(r"最大轮次\s*[:：]\s*(\d+)", sections["## 修复轮次"])
    current = re.search(r"当前轮次\s*[:：]\s*(\d+)", sections["## 修复轮次"])
    if not maximum or not current:
        raise ValueError("target record must declare 最大轮次 and 当前轮次")
    max_rounds, current_round = int(maximum.group(1)), int(current.group(1))
    if not 1 <= max_rounds <= MAX_REPAIR_ROUNDS or not 1 <= current_round <= max_rounds:
        raise ValueError(
            f"target record repair rounds must satisfy 1 <= 当前轮次 <= 最大轮次 <= {MAX_REPAIR_ROUNDS}"
        )
    if context == "local-change" and "baseline" not in sections["## 基线"].lower() and "基线" not in sections["## 基线"]:
        raise ValueError("local-change target must describe its baseline")
    fingerprint = _target_fingerprint(text)
    return {
        "id": target_id,
        "change_ref": change_ref,
        "context": context,
        "kind": target_kind,
        "replan_evidence": replan_evidence,
        "replan_failed_gate": replan_failed_gate,
        "current_round": current_round,
        "max_rounds": max_rounds,
        "allowed_paths": allowed_paths,
        "new_abstraction_record": new_abstraction_record.strip().strip("`"),
        "minimum_mode": effective_minimum_mode,
        "minimum_mode_declared": minimum_mode,
        "minimum_mode_derived": derived_minimum_mode,
        "claim_manifest": claim_manifest["path"],
        "claim_manifest_fingerprint": claim_manifest["fingerprint"],
        "requirement_catalog": claim_manifest["requirement_catalog"],
        "requirement_profile": claim_manifest["requirement_profile"],
        "requirement_catalog_fingerprint": claim_manifest[
            "requirement_catalog_fingerprint"
        ],
        "claims": claim_manifest["claims"],
        "acceptance_ids": acceptance_ids,
        "fingerprint": fingerprint,
        "path": str(target),
    }

def _target_identity(target: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(target["id"]),
        "change_ref": str(target["change_ref"]),
        "context": str(target["context"]),
        "fingerprint": str(target["fingerprint"]),
        "claim_manifest_fingerprint": str(target["claim_manifest_fingerprint"]),
    }

def _validate_replan_predecessor(
    workspace: Path,
    *,
    target: dict[str, Any],
) -> dict[str, Any] | None:
    """Verify that an architecture successor is bound to genuine stopped evidence."""
    raw_evidence = str(target.get("replan_evidence") or "")
    failed_gate_id = str(target.get("replan_failed_gate") or "")
    if not raw_evidence:
        return None
    evidence_dir = (workspace / raw_evidence).resolve()
    if not evidence_dir.is_dir():
        raise ValueError(f"replan predecessor evidence does not exist: {raw_evidence}")
    attestation_error = _verify_evidence_attestation(workspace, evidence_dir)
    if attestation_error:
        raise ValueError(f"replan predecessor attestation is invalid: {attestation_error}")
    summary_path = evidence_dir / "run-summary.json"
    repair_plan_path = evidence_dir / "repair-plan.json"
    if not summary_path.is_file() or not repair_plan_path.is_file():
        raise ValueError("replan predecessor must contain run-summary.json and repair-plan.json")
    summary = _load_json(summary_path)
    repair_plan = _load_json(repair_plan_path)
    if summary.get("run_kind") != "verification" or summary.get("decision") != FAIL:
        raise ValueError("replan predecessor must be a failed verification run")
    if (
        summary.get("loop_status") != "ARCHITECTURE_REPLAN_REQUIRED"
        or repair_plan.get("loop_status") != "ARCHITECTURE_REPLAN_REQUIRED"
    ):
        raise ValueError("replan predecessor was not stopped for architecture replanning")
    predecessor_identity = summary.get("target_identity")
    if not isinstance(predecessor_identity, dict) or not predecessor_identity.get("id"):
        raise ValueError("replan predecessor does not contain a valid target identity")
    if str(predecessor_identity.get("id")) == str(target.get("id")):
        raise ValueError("replan predecessor cannot reference the successor target itself")
    result_statuses = {
        str(item.get("id")): str(item.get("status") or "")
        for item in (summary.get("results") or [])
        if isinstance(item, dict) and item.get("id")
    }
    if result_statuses.get(failed_gate_id) != FAIL:
        raise ValueError(
            f"replan predecessor does not prove failed Gate {failed_gate_id}"
        )
    repair_gate_ids = {
        str(item.get("gate_id"))
        for item in (repair_plan.get("repairs") or [])
        if isinstance(item, dict) and item.get("gate_id")
    }
    if failed_gate_id not in repair_gate_ids:
        raise ValueError(
            f"replan predecessor repair plan is not owned by Gate {failed_gate_id}"
        )
    if not any(
        isinstance(item, dict) and item.get("status") == "FAILED"
        for item in (summary.get("claim_results") or [])
    ):
        raise ValueError("replan predecessor does not contain a failed acceptance claim")
    attestation = _load_json(evidence_dir / "evidence-attestation.json")
    return {
        "evidence_dir": raw_evidence,
        "target_identity": predecessor_identity,
        "failed_gate_id": failed_gate_id,
        "loop_status": "ARCHITECTURE_REPLAN_REQUIRED",
        "attestation_manifest_fingerprint": attestation.get("manifest_fingerprint"),
    }

