#!/usr/bin/env python3
"""Reproducible, bounded quality controller for this workspace.

The controller runs declared gates once.  It never edits source code and never
retries a failed command by itself: a developer or Codex records a target,
captures a baseline, applies one bounded repair, and asks for the smallest
valid dependency-closed regression. Evidence, source immutability, convergence
trend and the bounded repair-round budget are all machine-recorded. Historical
PASS rows never replace execution of this run's prerequisites.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import ast
import datetime as dt
import fnmatch
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from source_paths import is_runtime_artifact_path

CONTROL_PLANE_DIR = SCRIPTS_DIR.parent / "skill-system" / "controller"
if str(CONTROL_PLANE_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_PLANE_DIR))
from progress import evaluate_progress  # type: ignore
from trusted_judge import verify_candidate as verify_trusted_candidate  # type: ignore


PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED_BY_ENVIRONMENT"
UPSTREAM_SKIPPED = "SKIPPED_UPSTREAM_FAILURE"
MODES = ("static", "quick", "integration", "release")
MODE_RANK = {mode: index for index, mode in enumerate(MODES)}
TARGET_HEADINGS = ("# 目标", "## 允许范围", "## 禁止范围", "## 验收条件", "## 基线", "## 修复轮次")
TARGET_CONTEXTS = {"local-change", "ci"}
TARGET_KINDS = {"diagnosis", "design", "oracle-review", "repair", "migration", "revert", "certification"}
TRANSITION_TARGET_KINDS = {"repair", "migration", "revert"}
NO_CHANGE_TARGET_KINDS = {"diagnosis", "design", "oracle-review", "certification"}
BLOCKING_LEVELS = {"required", "release"}
EVIDENCE_SCHEMA_VERSION = 6
MAX_REPAIR_ROUNDS = 8


class QualityRunConflictError(RuntimeError):
    """Raised when another controller already owns this target/evidence run."""

STAGNATION_LIMIT = 2
EVIDENCE_REQUIRED_FIELDS = (
    "schema_version",
    "workspace_version",
    "mode",
    "run_kind",
    "decision",
    "loop_status",
    "generated_at",
    "evidence_dir",
    "target",
    "target_identity",
    "target_minimum_mode_declared",
    "target_minimum_mode_derived",
    "target_minimum_mode_effective",
    "replan_predecessor",
    "claim_manifest",
    "claim_manifest_fingerprint",
    "claim_manifest_evidence_file",
    "claim_results",
    "unverified_claim_ids",
    "policy_fingerprint",
    "rerun_from",
    "prior_evidence",
    "reused_prerequisites",
    "missing_prerequisites",
    "workspace_snapshot_start_fingerprint",
    "workspace_snapshot_fingerprint",
    "workspace_snapshot_file",
    "selected_gate_ids",
    "required_gate_ids",
    "gate_contract_fingerprints",
    "completion_eligible",
    "evidence_attestation_file",
    "results",
)
TARGET_PLACEHOLDERS = {
    "change-yyyymmdd-short-name",
    "change-yyyy-mm-dd-short-name",
    "example-target",
    "sample-target",
    "todo",
    "tbd",
    "待填写",
    "unknown",
}
SNAPSHOT_IGNORED_PARTS = {
    ".git",
    ".quality",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".run-locks",
    "node_modules",
    "coverage",
}
SNAPSHOT_IGNORED_NAMES = {".coverage", ".DS_Store", ".env"}
SNAPSHOT_IGNORED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pyc"}
PRODUCTION_SOURCE_PREFIXES = (
    "services/agent-service/src/",
    "services/agent-service/app/",
    "services/business-service/business_service/",
)
ABSTRACTION_RECORD_MARKERS = ("新增项", "唯一职责", "替换或删除项", "删除证据", "验证")
RERUN_CONTRACT = "dependency_closure_then_downstream"
CLAIM_SCHEMA_VERSION = 1
CLAIM_RISKS = {"P0", "P1", "P2", "P3"}
CLAIM_CLOSURE_REQUIREMENTS = {"regression-transition", "current-pass"}
CLAIM_EVIDENCE_KINDS = {
    "static-contract",
    "counterexample",
    "integration",
    "release-provenance",
}


def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def _safe_run_id() -> str:
    return dt.datetime.now(dt.UTC).strftime("run-%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"policy must be an object: {path}")
    return payload




def _evidence_signing_key(workspace: Path) -> bytes:
    """Return a stable local key or a protected CI-provided key.

    The key is intentionally kept outside evidence directories.  Local keys
    protect against accidental/manual evidence edits; protected CI must inject
    QUALITY_EVIDENCE_SIGNING_KEY and retain evidence as an immutable artifact.
    """
    configured = os.getenv("QUALITY_EVIDENCE_SIGNING_KEY")
    if configured:
        key = configured.encode("utf-8")
        if len(key) < 32:
            raise ValueError("QUALITY_EVIDENCE_SIGNING_KEY must contain at least 32 bytes")
        return key
    key_path = workspace / ".quality" / "quality-evidence.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.is_file():
        key_path.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
    key = key_path.read_text(encoding="utf-8").strip().encode("utf-8")
    if len(key) < 32:
        raise ValueError("local quality evidence signing key is invalid")
    return key


def _evidence_file_hashes(evidence_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(evidence_dir.rglob("*")):
        if not path.is_file() or path.name == "evidence-attestation.json":
            continue
        relative = path.relative_to(evidence_dir).as_posix()
        files[relative] = _sha256_file(path)
    return files


def _write_evidence_attestation(workspace: Path, evidence_dir: Path) -> str:
    files = _evidence_file_hashes(evidence_dir)
    canonical = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest_fingerprint = _sha256_text(canonical)
    key = _evidence_signing_key(workspace)
    signature = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    payload = {
        "schema_version": 1,
        "algorithm": "hmac-sha256",
        "key_id": hashlib.sha256(key).hexdigest()[:16],
        "manifest_fingerprint": manifest_fingerprint,
        "files": files,
        "signature": signature,
    }
    filename = "evidence-attestation.json"
    (evidence_dir / filename).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return filename


def _verify_evidence_attestation(workspace: Path, evidence_dir: Path) -> str | None:
    path = evidence_dir / "evidence-attestation.json"
    if not path.is_file():
        return "evidence does not contain evidence-attestation.json"
    try:
        payload = _load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"evidence attestation is unreadable: {exc}"
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        return "evidence attestation has no file manifest"
    expected_files = {str(name): str(digest) for name, digest in files.items()}
    actual_files = _evidence_file_hashes(evidence_dir)
    if expected_files != actual_files:
        return "evidence files were added, removed, or modified after attestation"
    canonical = json.dumps(expected_files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if payload.get("manifest_fingerprint") != _sha256_text(canonical):
        return "evidence manifest fingerprint is invalid"
    try:
        key = _evidence_signing_key(workspace)
    except ValueError as exc:
        return str(exc)
    expected_signature = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(payload.get("signature") or ""), expected_signature):
        return "evidence signature is invalid for this workspace/CI trust key"
    return None


def verify_evidence_attestation(workspace: Path, evidence_dir: Path) -> None:
    """Verify that every evidence file is covered by the workspace/CI trust key."""
    error = _verify_evidence_attestation(workspace.resolve(), evidence_dir.resolve())
    if error is not None:
        raise ValueError(error)


def _clean_text(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    half = limit // 2
    return value[:half] + "\n... <truncated> ...\n" + value[-half:]


def _npm_executable(workspace: Path) -> Path | None:
    """Return a reproducible npm executable without assuming a global alias.

    CI normally supplies npm on PATH.  Local quality runs may instead use the
    checked/managed Node runtime under `.quality/tools`; its sibling `node`
    must be put on PATH before npm is invoked because npm's launcher uses
    `/usr/bin/env node`.
    """
    system_npm = shutil.which("npm")
    if system_npm:
        return Path(system_npm).resolve()
    tools_root = workspace / ".quality" / "tools"
    if not tools_root.is_dir():
        return None
    candidates = sorted(
        (
            path
            for path in tools_root.glob("node-*/bin/npm")
            if path.is_file() and os.access(path, os.X_OK) and (path.parent / "node").is_file()
        ),
        key=lambda path: str(path),
    )
    # Do not resolve npm's symlink.  Official Node distributions expose
    # `bin/npm -> ../lib/node_modules/.../npm-cli.js`; resolving it would make
    # `npm.parent` point at npm's JavaScript directory instead of `bin/`, so
    # `/usr/bin/env node` could no longer find the sibling Node executable.
    return candidates[-1].absolute() if candidates else None


def _interpolate(value: str, *, workspace: Path, evidence_dir: Path, mode: str) -> str:
    npm = _npm_executable(workspace)
    return value.format(
        workspace=str(workspace),
        python=sys.executable,
        npm=str(npm) if npm else "npm",
        evidence_dir=str(evidence_dir),
        mode=mode,
    )


def _target_section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    following = [
        text.find(next_heading, start)
        for next_heading in TARGET_HEADINGS
        if next_heading != heading and text.find(next_heading, start) >= 0
    ]
    end = min(following) if following else len(text)
    return text[start:end].strip()


def _target_metadata(section: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\s*[-*]?\s*{re.escape(key)}\s*[:：]\s*(.+?)\s*$", section)
    return match.group(1).strip() if match else None


def _target_fingerprint(text: str) -> str:
    # Advancing the round is the only intentional mutable field in a target.
    normalized = re.sub(r"(当前轮次\s*[:：]\s*)\d+", r"\1<round>", text)
    normalized = re.sub(r"\r\n?", "\n", normalized).strip() + "\n"
    return _sha256_text(normalized)


def _is_target_placeholder(value: str | None) -> bool:
    normalized = (value or "").strip().strip("`").lower()
    if not normalized or normalized in TARGET_PLACEHOLDERS:
        return True
    return any(marker in normalized for marker in ("yyyy", "<", ">", "{", "}"))


def _python_selector_exists(path: Path, selector: str) -> bool:
    """Return whether a pytest-style Python selector exists in the file AST."""
    clean_parts = [part.split("[", 1)[0] for part in selector.split("::") if part]
    if not clean_parts:
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    current: list[ast.stmt] = list(tree.body)
    for index, part in enumerate(clean_parts):
        node = next(
            (
                item
                for item in current
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and item.name == part
            ),
            None,
        )
        if node is None:
            return False
        if index < len(clean_parts) - 1:
            if not isinstance(node, ast.ClassDef):
                return False
            current = list(node.body)
    return True


def _safe_workspace_relative_json(workspace: Path, raw: str, *, field: str) -> Path:
    value = raw.strip().strip("`")
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() != ".json"
    ):
        raise ValueError(f"{field} must be a safe workspace-relative .json path")
    resolved = (workspace / path).resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"{field} must stay inside the workspace") from exc
    return resolved


def _canonical_json_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256_text(canonical)


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


def _python_ast_parse(workspace: Path) -> dict[str, Any]:
    roots = [workspace / "services", workspace / "architecture-skill", workspace / "scripts"]
    parsed = 0
    errors: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts or ".venv" in path.parts or "node_modules" in path.parts:
                continue
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                parsed += 1
            except SyntaxError as exc:
                errors.append(f"{path.relative_to(workspace)}:{exc.lineno}:{exc.msg}")
    return {
        "exit_code": 0 if not errors else 1,
        "stdout": f"parsed {parsed} Python files\n",
        "stderr": "\n".join(errors),
        "metadata": {"parsed_files": parsed, "syntax_errors": errors},
    }


def _probe_http(url: str, *, path: str, timeout_seconds: float) -> bool:
    base = url.rstrip("/")
    request = urllib.request.Request(f"{base}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= int(response.status) < 400
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        return False


def _probe_tcp_url(url: str, *, timeout_seconds: float) -> bool:
    normalized = re.sub(r"^postgresql\+[A-Za-z0-9_+-]+://", "postgresql://", url)
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.hostname
    if not host:
        return False
    port = int(parsed.port or (5432 if parsed.scheme.startswith("postgres") else 80))
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _environment_problem(workspace: Path, step: dict[str, Any]) -> list[str]:
    requirements = step.get("environment") or {}
    missing: list[str] = []
    for command in requirements.get("commands", []):
        command_name = str(command)
        available = bool(_npm_executable(workspace)) if command_name == "npm" else bool(shutil.which(command_name))
        if not available:
            missing.append(f"command:{command}")
    for key in requirements.get("variables", []):
        if not os.getenv(str(key)):
            missing.append(f"environment:{key}")
    for key, allowed in (requirements.get("expected_values") or {}).items():
        values = {str(value) for value in (allowed if isinstance(allowed, list) else [allowed])}
        if os.getenv(str(key)) not in values:
            missing.append(f"environment:{key}:expected={'|'.join(sorted(values))}")
    for probe in requirements.get("probes", []):
        if not isinstance(probe, dict):
            missing.append("probe:invalid")
            continue
        kind = str(probe.get("kind") or "")
        variable = str(probe.get("url_env") or "")
        value = os.getenv(variable) if variable else None
        timeout = float(probe.get("timeout_seconds", 3))
        if not value:
            missing.append(f"environment:{variable or 'probe_url'}")
            continue
        if kind == "http" and not _probe_http(value, path=str(probe.get("path") or "/health"), timeout_seconds=timeout):
            missing.append(f"probe:http:{variable}")
        elif kind == "tcp" and not _probe_tcp_url(value, timeout_seconds=timeout):
            missing.append(f"probe:tcp:{variable}")
        elif kind not in {"http", "tcp"}:
            missing.append(f"probe:unknown:{kind}")
    return missing


def _terminate_process_group(proc: subprocess.Popen[Any], *, grace_seconds: float) -> None:
    """Stop the gate process and every descendant in its dedicated session."""
    if os.name != "posix":  # pragma: no cover - Windows fallback
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=grace_seconds)
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(proc.pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _run_shell(workspace: Path, evidence_dir: Path, mode: str, step: dict[str, Any]) -> dict[str, Any]:
    argv_raw = step.get("argv")
    if not isinstance(argv_raw, list) or not argv_raw:
        return {"exit_code": 2, "stdout": "", "stderr": "step argv must be a non-empty array", "metadata": {}}
    argv = [_interpolate(str(item), workspace=workspace, evidence_dir=evidence_dir, mode=mode) for item in argv_raw]
    cwd = workspace / _interpolate(str(step.get("cwd", ".")), workspace=workspace, evidence_dir=evidence_dir, mode=mode)
    if not cwd.is_dir():
        return {"exit_code": 2, "stdout": "", "stderr": f"step cwd does not exist: {cwd}", "metadata": {}}
    env = os.environ.copy()
    # A quality-loop run may itself be launched by pytest-cov. Propagating
    # that bootstrap into nested pytest/npm gates causes recursive collection,
    # unstable timings and duplicate coverage files. Each declared gate owns
    # its own coverage evidence, so strip only the outer bootstrap variables.
    for key in tuple(env):
        if key.startswith("COV_CORE_") or key == "COVERAGE_PROCESS_START":
            env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Every Gate gets an explicit, writable evidence boundary.  Build tools and
    # browser runners must put generated artifacts here instead of rewriting
    # governed workspace inputs (for example frontend/dist).  These values are
    # controller-owned and therefore deliberately override inherited shell
    # values before the Gate-specific environment is applied below.
    env["QUALITY_EVIDENCE_DIR"] = str(evidence_dir)
    env["QUALITY_LOOP_MODE"] = mode
    env["QUALITY_GATE_ID"] = str(step.get("id") or "")
    npm = _npm_executable(workspace)
    if npm is not None:
        env["PATH"] = str(npm.parent) + os.pathsep + env.get("PATH", "")
    for key, value in (step.get("env") or {}).items():
        env[str(key)] = _interpolate(str(value), workspace=workspace, evidence_dir=evidence_dir, mode=mode)
    timeout = int(step.get("timeout_seconds", 300))
    started = time.monotonic()
    timed_out = False
    with tempfile.TemporaryDirectory(prefix=f"quality-step-{step.get('id', 'gate')}-") as temp:
        stdout_path = Path(temp) / "stdout.log"
        stderr_path = Path(temp) / "stderr.log"
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdout=stdout_file,
                stderr=stderr_file,
                env=env,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_group(proc, grace_seconds=10)
                if proc.poll() is None:
                    proc.wait(timeout=2)
            else:
                # A successful gate is not allowed to leave background workers
                # alive. They can retain controller descriptors and corrupt the
                # next Gate even though the declared command already returned.
                _terminate_process_group(proc, grace_seconds=0.5)
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    duration_ms = int((time.monotonic() - started) * 1000)
    if timed_out:
        stderr = (stderr or "") + f"\nquality_loop_step_timeout_after_{timeout}s"
    return {
        "exit_code": 124 if timed_out else int(proc.returncode or 0),
        "stdout": stdout or "",
        "stderr": stderr or "",
        "duration_ms": duration_ms,
        "metadata": {"argv": argv},
    }


def _runtime_environment_block_evidence(raw: dict[str, Any]) -> dict[str, str] | None:
    """Accept a dynamic environment block only through an explicit protocol.

    Exit 78 by itself is not trusted: undeclared test failures must remain red.
    A Gate that discovers provider/network unavailability at runtime must emit a
    final JSON object with the exact blocked status and a non-empty reason.
    """
    if int(raw.get("exit_code") or 0) != 78:
        return None
    for line in reversed(str(raw.get("stdout") or "").splitlines()):
        try:
            payload = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        reason = str(payload.get("reason") or "").strip()
        if payload.get("status") == BLOCKED and reason:
            return {"status": BLOCKED, "reason": reason}
    return None


def _structured_stdout_payload(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Return a structured Gate payload only when stdout is one JSON object.

    Gate stdout remains human-readable evidence.  This parser is deliberately
    conservative so arbitrary log fragments can never silently influence the
    controller decision.
    """

    text = str(raw.get("stdout") or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _dimension_decision(results: list[dict[str, Any]]) -> str:
    if not results:
        return "NOT_ASSESSED"
    statuses = {str(item.get("status") or "") for item in results}
    if FAIL in statuses or UPSTREAM_SKIPPED in statuses:
        return FAIL
    if BLOCKED in statuses:
        return BLOCKED
    return PASS


_PRODUCTION_BUNDLE_GATE_ID = "production-certification-bundle"
_REAL_MODEL_BUNDLE_GATE_ID = "preproduction-real-model-certification-bundle"
_REAL_MODEL_BROWSER_GATE_IDS = (
    "configured-model-browser-conversation",
    "configured-model-browser-campaign",
)
_REAL_MODEL_REQUIRED_GATE_IDS = (_REAL_MODEL_BUNDLE_GATE_ID,) + _REAL_MODEL_BROWSER_GATE_IDS
_REAL_MODEL_IDENTITY_FIELDS = (
    "provider",
    "endpoint",
    "model",
    "credential_fingerprint_sha256_16",
)
_REAL_MODEL_BUNDLE_COMPONENTS = ("smoke", "semantic", "lifecycle")
_REAL_MODEL_MINIMUM_CALLS = {"smoke": 1, "semantic": 12, "lifecycle": 2}


def _production_certification_dimension(
    results: list[dict[str, Any]],
    *,
    mode: str,
) -> dict[str, Any]:
    if mode != "release":
        return {
            "status": "NOT_DECLARED",
            "reason": "production certification requires one complete release-mode run",
        }
    gate = next((row for row in results if str(row.get("id") or "") == _PRODUCTION_BUNDLE_GATE_ID), None)
    if gate is None:
        return {"status": FAIL, "reason": "required_production_bundle_gate_missing"}
    status = str(gate.get("status") or "")
    if status == BLOCKED:
        return {"status": BLOCKED, "reason": "production_environment_unavailable", "blocked_gate_ids": [_PRODUCTION_BUNDLE_GATE_ID]}
    if status != PASS:
        return {"status": FAIL, "reason": "production_bundle_gate_not_passed", "failed_gate_ids": [_PRODUCTION_BUNDLE_GATE_ID]}
    metadata = gate.get("metadata") if isinstance(gate.get("metadata"), dict) else {}
    assessment = metadata.get("structured_assessment") if isinstance(metadata.get("structured_assessment"), dict) else {}
    identity = assessment.get("real_model_identity") if isinstance(assessment.get("real_model_identity"), dict) else {}
    valid = (
        assessment.get("contract") == "production-certification-bundle@1"
        and assessment.get("status") == PASS
        and assessment.get("components") == ["real_model", "postgres", "browser"]
        and int(assessment.get("component_count") or 0) == 3
        and bool(re.fullmatch(r"prodcert-[0-9a-f]{32,96}", str(assessment.get("session_id") or "")))
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(assessment.get("workspace_fingerprint_sha256") or "")))
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(assessment.get("toolchain_fingerprint_sha256") or "")))
        and all(str(identity.get(field) or "").strip() for field in _REAL_MODEL_IDENTITY_FIELDS)
        and identity.get("official_endpoint") is True
        and identity.get("https") is True
        and int(assessment.get("real_model_total_attested_calls") or 0) >= 15
        and int(assessment.get("postgres_restart_count") or 0) >= 2
        and bool(re.fullmatch(r"[0-9a-f]{16}", str(assessment.get("postgres_database_instance_fingerprint_sha256_16") or "")))
        and bool(re.fullmatch(r"pgvector/pgvector@sha256:[0-9a-f]{64}", str(assessment.get("postgres_container_image_reference") or "")))
        and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", str(assessment.get("postgres_container_image_id_sha256") or "")))
        and int(assessment.get("browser_journey_count") or 0) >= 2
        and bool(str(assessment.get("browser_version") or "").strip())
        and assessment.get("evidence_scope") == "single-live-production-certification-session"
    )
    if not valid:
        return {"status": FAIL, "reason": "production_bundle_evidence_invalid", "gate_id": _PRODUCTION_BUNDLE_GATE_ID}
    return {
        "status": PASS,
        "contract": "production-certification-dimension@1",
        "gate_ids": [_PRODUCTION_BUNDLE_GATE_ID],
        "session_id": str(assessment["session_id"]),
        "workspace_fingerprint_sha256": str(assessment["workspace_fingerprint_sha256"]),
        "toolchain_fingerprint_sha256": str(assessment["toolchain_fingerprint_sha256"]),
        "real_model_identity": {field: identity.get(field) for field in (*_REAL_MODEL_IDENTITY_FIELDS, "official_endpoint", "https")},
        "real_model_total_attested_calls": int(assessment["real_model_total_attested_calls"]),
        "postgres_restart_count": int(assessment["postgres_restart_count"]),
        "postgres_container_image_reference": str(assessment["postgres_container_image_reference"]),
        "postgres_container_image_id_sha256": str(assessment["postgres_container_image_id_sha256"]),
        "browser_journey_count": int(assessment["browser_journey_count"]),
        "evidence_scope": "single-live-production-certification-session",
    }


def _real_model_certification_dimension(
    results: list[dict[str, Any]],
    *,
    mode: str,
) -> dict[str, Any]:
    """Consume one live bundle as the only release real-model authority."""
    if mode != "release":
        return {
            "status": "NOT_DECLARED",
            "reason": "real-model certification requires one complete release-mode run",
        }

    by_id = {str(row.get("id") or ""): row for row in results}
    if _PRODUCTION_BUNDLE_GATE_ID in by_id:
        production = _production_certification_dimension(results, mode=mode)
        if production.get("status") != PASS:
            return {
                "status": production.get("status"),
                "reason": production.get("reason"),
                **({"blocked_gate_ids": production.get("blocked_gate_ids")} if production.get("blocked_gate_ids") else {}),
                **({"failed_gate_ids": production.get("failed_gate_ids")} if production.get("failed_gate_ids") else {}),
            }
        return {
            "status": PASS,
            "contract": "real-model-certification-dimension@3",
            "bundle_contract": "production-certification-bundle@1",
            "gate_ids": [_PRODUCTION_BUNDLE_GATE_ID],
            "identity": production["real_model_identity"],
            "session_id": production["session_id"],
            "workspace_fingerprint_sha256": production["workspace_fingerprint_sha256"],
            "toolchain_fingerprint_sha256": production["toolchain_fingerprint_sha256"],
            "total_attested_model_calls": production["real_model_total_attested_calls"],
            "evidence_scope": "single-live-production-certification-session",
        }
    if _REAL_MODEL_BUNDLE_GATE_ID not in by_id:
        return {
            "status": FAIL,
            "reason": "required_real_model_bundle_gate_missing",
            "missing_gate_ids": [_REAL_MODEL_BUNDLE_GATE_ID],
        }
    missing = [gate_id for gate_id in _REAL_MODEL_BROWSER_GATE_IDS if gate_id not in by_id]
    if missing:
        return {
            "status": FAIL,
            "reason": "required_real_model_gates_missing",
            "missing_gate_ids": missing,
        }

    blocked = [
        gate_id for gate_id in _REAL_MODEL_REQUIRED_GATE_IDS
        if str(by_id[gate_id].get("status") or "") == BLOCKED
    ]
    if blocked:
        return {
            "status": BLOCKED,
            "reason": "real_model_environment_unavailable",
            "blocked_gate_ids": blocked,
        }
    failed = [
        gate_id for gate_id in _REAL_MODEL_REQUIRED_GATE_IDS
        if str(by_id[gate_id].get("status") or "") != PASS
    ]
    if failed:
        return {
            "status": FAIL,
            "reason": "required_real_model_gate_not_passed",
            "failed_gate_ids": failed,
        }

    metadata = by_id[_REAL_MODEL_BUNDLE_GATE_ID].get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    assessment = metadata.get("structured_assessment")
    assessment = assessment if isinstance(assessment, dict) else {}
    identity = assessment.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    safe_identity = {
        field: str(identity.get(field) or "").strip()
        for field in _REAL_MODEL_IDENTITY_FIELDS
    }
    components = assessment.get("components")
    components = list(components) if isinstance(components, list) else []
    call_counts = assessment.get("attested_model_calls_by_component")
    call_counts = call_counts if isinstance(call_counts, dict) else {}
    try:
        component_count = int(assessment.get("component_count"))
        total_calls = int(assessment.get("total_attested_model_calls"))
    except (TypeError, ValueError):
        component_count = 0
        total_calls = 0
    valid_call_counts = all(
        isinstance(call_counts.get(name), int)
        and int(call_counts[name]) >= minimum
        for name, minimum in _REAL_MODEL_MINIMUM_CALLS.items()
    )
    valid = (
        str(assessment.get("contract") or "") == "real-model-certification-bundle@1"
        and str(assessment.get("status") or "") == PASS
        and bool(str(assessment.get("session_id") or "").strip())
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(assessment.get("workspace_fingerprint_sha256") or "")))
        and all(safe_identity.values())
        and identity.get("official_endpoint") is True
        and identity.get("https") is True
        and component_count == len(_REAL_MODEL_BUNDLE_COMPONENTS)
        and components == list(_REAL_MODEL_BUNDLE_COMPONENTS)
        and valid_call_counts
        and total_calls >= sum(_REAL_MODEL_MINIMUM_CALLS.values())
    )
    if not valid:
        return {
            "status": FAIL,
            "reason": "real_model_bundle_evidence_invalid",
            "gate_id": _REAL_MODEL_BUNDLE_GATE_ID,
        }

    return {
        "status": PASS,
        "contract": "real-model-certification-dimension@2",
        "bundle_contract": "real-model-certification-bundle@1",
        "gate_ids": list(_REAL_MODEL_REQUIRED_GATE_IDS),
        "identity": safe_identity,
        "session_id": str(assessment["session_id"]),
        "workspace_fingerprint_sha256": str(assessment["workspace_fingerprint_sha256"]),
        "component_count": component_count,
        "components": components,
        "total_attested_model_calls": total_calls,
        "evidence_scope": "single-current-release-bundle",
    }


def _quality_dimensions(results: list[dict[str, Any]], *, mode: str = "quick") -> dict[str, Any]:
    """Expose functional, architecture and explicit real-model certification truth."""

    functional_categories = {
        "contract",
        "counterexample-regression",
        "frontend-test",
        "integration",
        "preproduction",
        "presentation",
        "unit-contract",
    }
    functional_results = [
        row for row in results if str(row.get("category") or "") in functional_categories
    ]

    architecture_gate = next(
        (row for row in results if str(row.get("id") or "") == "architecture-convergence"),
        None,
    )
    structured = None
    if architecture_gate is not None:
        candidate = (architecture_gate.get("metadata") or {}).get("structured_assessment")
        if isinstance(candidate, dict):
            structured = candidate
    architecture = {
        "status": (
            str(structured.get("architecture_status"))
            if structured and structured.get("architecture_status")
            else str(architecture_gate.get("status"))
            if architecture_gate is not None
            else "NOT_ASSESSED"
        ),
        "debt_status": (
            str(structured.get("architecture_debt_status"))
            if structured and structured.get("architecture_debt_status")
            else None
        ),
        "gate_status": (
            str(architecture_gate.get("status"))
            if architecture_gate is not None
            else "NOT_ASSESSED"
        ),
    }
    return {
        "functional": {"status": _dimension_decision(functional_results)},
        "architecture": architecture,
        "real_model_certification": _real_model_certification_dimension(results, mode=mode),
        "production_certification": _production_certification_dimension(results, mode=mode),
    }


def _run_step(workspace: Path, evidence_dir: Path, mode: str, step: dict[str, Any]) -> dict[str, Any]:
    started_at = _now()
    missing = _environment_problem(workspace, step)
    if missing:
        raw = {
            "exit_code": 78,
            "stdout": "",
            "stderr": "missing or unavailable required environment: " + ", ".join(missing),
            "metadata": {"missing_environment": missing},
        }
        status = BLOCKED
    else:
        try:
            kind = str(step.get("kind", "shell"))
            if kind == "python_ast_parse":
                raw = _python_ast_parse(workspace)
            elif kind == "shell":
                raw = _run_shell(workspace, evidence_dir, mode, step)
            else:
                raw = {"exit_code": 2, "stdout": "", "stderr": f"unknown step kind: {kind}", "metadata": {}}
        except Exception as exc:  # fail closed while preserving repair evidence
            raw = {"exit_code": 2, "stdout": "", "stderr": repr(exc), "metadata": {"exception": exc.__class__.__name__}}
        blocked_codes = {int(code) for code in step.get("blocked_exit_codes") or []}
        runtime_block = _runtime_environment_block_evidence(raw)
        if runtime_block is not None:
            raw.setdefault("metadata", {})["runtime_environment_block"] = runtime_block
        status = BLOCKED if (
            int(raw["exit_code"]) in blocked_codes or runtime_block is not None
        ) else (PASS if int(raw["exit_code"]) == 0 else FAIL)
    structured_payload = _structured_stdout_payload(raw)
    if structured_payload is not None:
        raw.setdefault("metadata", {})["structured_assessment"] = structured_payload
    ended_at = _now()
    return {
        "id": step["id"],
        "name": step.get("name", step["id"]),
        "status": status,
        "owner": step.get("owner", "unassigned"),
        "category": step.get("category", "verification"),
        "blocking_level": step.get("blocking_level", "required"),
        "repair_playbook": step.get("repair_playbook", "apply the smallest fix within the declared target scope"),
        "depends_on": list(step.get("depends_on") or []),
        "started_at": started_at,
        "ended_at": ended_at,
        "exit_code": raw["exit_code"],
        "duration_ms": raw.get("duration_ms"),
        "stdout": _clean_text(raw.get("stdout") or ""),
        "stderr": _clean_text(raw.get("stderr") or ""),
        "metadata": raw.get("metadata") or {},
    }


def _validate_policy(policy: dict[str, Any]) -> list[dict[str, Any]]:
    steps = policy.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("quality-loop policy must contain a non-empty steps array")
    ids = [str(step.get("id") or "") for step in steps if isinstance(step, dict)]
    if len(ids) != len(steps) or not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("quality-loop step ids must be non-empty and unique")
    by_id = {str(step["id"]): step for step in steps}
    for step in steps:
        for field in ("owner", "category", "blocking_level", "repair_playbook", "rerun_contract"):
            if not str(step.get(field) or "").strip():
                raise ValueError(f"step {step['id']} must declare {field}")
        if str(step.get("blocking_level")) not in BLOCKING_LEVELS:
            raise ValueError(f"step {step['id']} has unknown blocking_level")
        if str(step.get("rerun_contract")) != RERUN_CONTRACT:
            raise ValueError(f"step {step['id']} must use rerun_contract={RERUN_CONTRACT}")
        modes = {str(mode) for mode in step.get("modes") or []}
        if not modes or not modes.issubset(set(MODES)):
            raise ValueError(f"step {step['id']} has invalid modes")
        unknown = set(step.get("depends_on") or []) - set(by_id)
        if unknown:
            raise ValueError(f"step {step['id']} depends on unknown steps: {sorted(unknown)}")
        for dependency in step.get("depends_on") or []:
            if not modes.issubset(set(by_id[str(dependency)].get("modes") or [])):
                raise ValueError(f"step {step['id']} depends on {dependency}, which is absent in one of its modes")
    # Kahn's algorithm detects cycles while retaining policy order for ties.
    remaining = {step_id: set(by_id[step_id].get("depends_on") or []) for step_id in by_id}
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = [step_id for step_id in by_id if step_id in remaining and not remaining[step_id]]
        if not ready:
            raise ValueError("quality-loop policy dependency graph contains a cycle")
        for step_id in ready:
            ordered.append(by_id[step_id])
            remaining.pop(step_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return ordered


def _steps_for_mode(ordered_steps: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    return [step for step in ordered_steps if mode in set(step.get("modes") or [])]


def _validate_claim_gate_contracts(
    target: dict[str, Any], ordered_steps: list[dict[str, Any]]
) -> None:
    by_id = {str(step["id"]): step for step in ordered_steps}
    for claim in target["claims"]:
        claim_id = str(claim["id"])
        required_mode = str(claim["required_mode"])
        unknown = [gate for gate in claim["required_gates"] if gate not in by_id]
        if unknown:
            raise ValueError(
                f"claim {claim_id} references unknown required_gates: {unknown}"
            )
        unavailable = [
            gate
            for gate in claim["required_gates"]
            if required_mode not in set(by_id[gate].get("modes") or [])
        ]
        if unavailable:
            raise ValueError(
                f"claim {claim_id} requires mode {required_mode}, but gates are absent in that mode: {unavailable}"
            )
        gate_log_refs = {
            str(ref).removeprefix("gate-log:")
            for ref in claim["evidence_refs"]
            if str(ref).startswith("gate-log:")
        }
        unbound_logs = sorted(gate_log_refs.difference(claim["required_gates"]))
        if unbound_logs:
            raise ValueError(
                f"claim {claim_id} gate-log refs must name required_gates: {unbound_logs}"
            )
        categories = {
            str(by_id[gate].get("category") or "verification")
            for gate in claim["required_gates"]
        }
        evidence_kind = str(claim["evidence_kind"])
        if evidence_kind == "counterexample":
            if MODE_RANK[required_mode] < MODE_RANK["quick"]:
                raise ValueError(f"counterexample claim {claim_id} must require quick or higher mode")
            if not categories.intersection(
                {"counterexample-regression", "unit-contract", "frontend-test", "integration", "preproduction"}
            ):
                raise ValueError(
                    f"counterexample claim {claim_id} has no test or adversarial evidence gate"
                )
        elif evidence_kind == "integration":
            if MODE_RANK[required_mode] < MODE_RANK["integration"]:
                raise ValueError(f"integration claim {claim_id} must require integration or release mode")
            if not categories.intersection({"integration", "preproduction"}):
                raise ValueError(f"integration claim {claim_id} has no integration evidence gate")
        elif evidence_kind == "release-provenance":
            if required_mode != "release":
                raise ValueError(f"release-provenance claim {claim_id} must require release mode")
            if "release" not in categories:
                raise ValueError(f"release-provenance claim {claim_id} has no release artifact evidence gate")


def _junit_case_index(evidence_dir: Path) -> list[dict[str, Any]]:
    """Collect executed pytest cases from this run's JUnit artifacts only."""
    cases: list[dict[str, Any]] = []
    junit_dir = evidence_dir / "junit"
    if not junit_dir.is_dir():
        return cases
    for xml_path in sorted(junit_dir.rglob("*.xml")):
        try:
            root = ET.parse(xml_path).getroot()
        except (ET.ParseError, OSError):
            continue
        for testcase in root.iter("testcase"):
            classname = str(testcase.attrib.get("classname") or "")
            name = str(testcase.attrib.get("name") or "")
            failed = any(
                child.tag.rsplit("}", 1)[-1] in {"failure", "error", "skipped"}
                for child in list(testcase)
            )
            cases.append(
                {
                    "classname": classname,
                    "name": name,
                    "passed": not failed,
                    "file": xml_path.relative_to(evidence_dir).as_posix(),
                }
            )
    return cases


def _test_module_from_ref(path_text: str) -> str:
    parts = list(Path(path_text).parts)
    try:
        index = parts.index("tests")
    except ValueError:
        return ""
    module_parts = parts[index:]
    if module_parts and module_parts[-1].endswith(".py"):
        module_parts[-1] = Path(module_parts[-1]).stem
    return ".".join(module_parts)


def _claim_evidence_ref_statuses(
    claim: dict[str, Any],
    *,
    result_statuses: dict[str, str],
    snapshot_files: set[str],
    junit_cases: list[dict[str, Any]],
) -> dict[str, str]:
    """Prove that claim references were produced by this exact run.

    A source selector is not enough merely because it exists in the repository:
    it must appear as a passing testcase in this run's JUnit output. A gate-log
    reference is verified only when that exact required Gate passed. Bare source
    references are accepted only when they are bound into the current source
    snapshot.
    """
    statuses: dict[str, str] = {}
    for raw_ref in claim["evidence_refs"]:
        ref = str(raw_ref)
        if ref.startswith("gate-log:"):
            gate_id = ref.removeprefix("gate-log:")
            gate_status = result_statuses.get(gate_id, "NOT_EXECUTED")
            statuses[ref] = "VERIFIED" if gate_status == PASS else gate_status
            continue
        path_text, separator, selector = ref.partition("::")
        path_text = path_text.strip()
        selector = selector.strip()
        if path_text not in snapshot_files:
            statuses[ref] = "SOURCE_NOT_BOUND"
            continue
        if not separator:
            statuses[ref] = "VERIFIED"
            continue
        expected_module = _test_module_from_ref(path_text)
        matched = [
            case
            for case in junit_cases
            if case["name"].split("[", 1)[0] == selector
            and (
                not expected_module
                or case["classname"] == expected_module
                or case["classname"].endswith(expected_module)
            )
        ]
        if any(bool(case["passed"]) for case in matched):
            statuses[ref] = "VERIFIED"
        elif matched:
            statuses[ref] = "FAILED"
        else:
            statuses[ref] = "NOT_EXECUTED"
    return statuses


def _gate_is_environment_blocked(
    gate_id: str,
    result_by_id: dict[str, dict[str, Any]],
    *,
    seen: set[str] | None = None,
) -> bool:
    """Return true only when a gate is blocked transitively by environment.

    A downstream gate skipped because an environment prerequisite was missing is
    not a code failure.  Conversely, a mixed dependency set containing any real
    FAIL must remain a failure.
    """
    visited = set(seen or ())
    if gate_id in visited:
        return False
    visited.add(gate_id)
    result = result_by_id.get(gate_id)
    if result is None:
        return False
    status = str(result.get("status") or "NOT_EXECUTED")
    if status == BLOCKED:
        return True
    if status != UPSTREAM_SKIPPED:
        return False
    failed_dependencies = [
        str(item)
        for item in ((result.get("metadata") or {}).get("failed_dependencies") or [])
    ]
    return bool(failed_dependencies) and all(
        _gate_is_environment_blocked(dependency, result_by_id, seen=visited)
        for dependency in failed_dependencies
    )


def _claim_results(
    target: dict[str, Any],
    result_by_id: dict[str, dict[str, Any]],
    *,
    mode: str,
    evidence_dir: Path,
    snapshot_files: set[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    junit_cases = _junit_case_index(evidence_dir)
    result_statuses = {
        str(gate_id): str(result.get("status") or "NOT_EXECUTED")
        for gate_id, result in result_by_id.items()
    }
    for claim in target["claims"]:
        gate_statuses = {
            gate: result_statuses.get(gate, "NOT_EXECUTED")
            for gate in claim["required_gates"]
        }
        environment_blocked_gates = [
            gate
            for gate in claim["required_gates"]
            if _gate_is_environment_blocked(str(gate), result_by_id)
        ]
        evidence_ref_statuses = _claim_evidence_ref_statuses(
            claim,
            result_statuses=result_statuses,
            snapshot_files=snapshot_files,
            junit_cases=junit_cases,
        )
        gate_states = set(gate_statuses.values())
        proof_states = set(evidence_ref_statuses.values())
        if MODE_RANK[mode] < MODE_RANK[str(claim["required_mode"])]:
            status = "INSUFFICIENT_MODE"
        elif FAIL in gate_states or "FAILED" in proof_states:
            status = "FAILED"
        elif environment_blocked_gates:
            status = "BLOCKED_BY_ENVIRONMENT"
        elif UPSTREAM_SKIPPED in gate_states:
            status = "FAILED"
        elif gate_states == {PASS} and proof_states == {"VERIFIED"}:
            status = "VERIFIED"
        else:
            status = "NOT_EXECUTED"
        results.append(
            {
                "id": claim["id"],
                "statement": claim["statement"],
                "risk": claim["risk"],
                "required_mode": claim["required_mode"],
                "evidence_kind": claim["evidence_kind"],
                "required_gates": list(claim["required_gates"]),
                "evidence_refs": list(claim["evidence_refs"]),
                "owner": claim["owner"],
                "closure_requirement": claim["closure_requirement"],
                "gate_statuses": gate_statuses,
                "environment_blocked_gates": environment_blocked_gates,
                "evidence_ref_statuses": evidence_ref_statuses,
                "status": status,
            }
        )
    return results

def _downstream_steps(steps: list[dict[str, Any]], root: str) -> list[dict[str, Any]]:
    by_id = {str(step["id"]): step for step in steps}
    known = set(by_id)
    if root not in known:
        raise ValueError(f"unknown --rerun-from gate: {root}")
    # First identify only the root's affected descendants.  Then close that
    # complete set over prerequisites.  Interleaving these two expansions can
    # either omit a downstream Gate's sibling prerequisite or pull unrelated
    # branches into the selection.
    descendants = {root}
    changed = True
    while changed:
        changed = False
        for step in steps:
            step_id = str(step["id"])
            dependencies = {str(dep) for dep in step.get("depends_on") or []}
            if step_id not in descendants and dependencies.intersection(descendants):
                descendants.add(step_id)
                changed = True
    selected = set(descendants)
    pending = list(descendants)
    while pending:
        current = pending.pop()
        for dependency in by_id[current].get("depends_on") or []:
            dependency = str(dependency)
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return [step for step in steps if step["id"] in selected]


def _gate_contract_fingerprints(steps: list[dict[str, Any]]) -> dict[str, str]:
    """Bind evidence to each exact executable Gate contract, not only its id."""
    return {
        str(step["id"]): _sha256_text(
            json.dumps(step, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        for step in steps
    }


def _write_step_evidence(evidence_dir: Path, result: dict[str, Any]) -> None:
    steps_dir = evidence_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    (steps_dir / f"{result['id']}.stdout.txt").write_text(result.get("stdout") or "", encoding="utf-8")
    (steps_dir / f"{result['id']}.stderr.txt").write_text(result.get("stderr") or "", encoding="utf-8")


def _decision(results: list[dict[str, Any]]) -> str:
    # A real code or contract failure must never be hidden behind a concurrent
    # environment block.  Pure environment unavailability remains BLOCKED; an
    # unexplained upstream skip without a blocked root remains FAIL.
    if any(item["status"] == FAIL for item in results):
        return FAIL
    if any(item["status"] == BLOCKED for item in results):
        return BLOCKED
    if any(item["status"] == UPSTREAM_SKIPPED for item in results):
        return FAIL
    return PASS


def _failure_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    # Convergence must measure root failures, not their downstream fan-out.
    # UPSTREAM_SKIPPED remains diagnostic evidence but does not make one root
    # defect look like several independent regressions.
    failed = [item for item in results if item.get("status") == FAIL]
    upstream_skipped = [item for item in results if item.get("status") == UPSTREAM_SKIPPED]
    gate_ids = sorted(str(item.get("id") or "unknown") for item in failed)
    skipped_ids = sorted(str(item.get("id") or "unknown") for item in upstream_skipped)
    signature_rows = [
        {
            "id": str(item.get("id") or "unknown"),
            "status": str(item.get("status") or "unknown"),
            "failure_kind": _failure_classification(item),
        }
        for item in failed
    ]
    signature = _sha256_text(json.dumps(signature_rows, ensure_ascii=False, sort_keys=True))
    return {
        "failure_count": len(failed),
        "failed_gate_ids": gate_ids,
        "upstream_skipped_gate_ids": skipped_ids,
        "failure_signature": signature,
    }


def _advance_convergence_state(
    state: dict[str, Any],
    *,
    current_round: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics = _failure_metrics(results)
    progress = evaluate_progress(state, metrics)
    improved = bool(progress["improved"])
    stagnant_rounds = 0 if improved else int(state.get("stagnant_rounds") or 0) + 1
    history = list(state.get("round_history") or [])
    history.append(
        {
            "round": current_round,
            **metrics,
            **progress,
            "stagnant_rounds": stagnant_rounds,
        }
    )
    state.update(
        {
            **metrics,
            "last_failure_count": metrics["failure_count"],
            "stagnant_rounds": stagnant_rounds,
            "round_history": history,
        }
    )
    return {**metrics, **progress, "stagnant_rounds": stagnant_rounds}




def _load_baseline(
    evidence_dir: Path | None,
    *,
    workspace: Path,
    target: dict[str, Any],
    policy_fingerprint: str,
) -> dict[str, Any]:
    if evidence_dir is None:
        raise ValueError("local-change verification requires --baseline-evidence from a prior --baseline pass")
    attestation_error = _verify_evidence_attestation(workspace, evidence_dir)
    if attestation_error:
        raise ValueError(attestation_error)
    record_path = evidence_dir / "baseline-record.json"
    if not record_path.is_file():
        raise ValueError("--baseline-evidence does not contain baseline-record.json")
    record = _load_json(record_path)
    if record.get("target_identity") != _target_identity(target):
        raise ValueError("baseline evidence target identity does not match the current target")
    if record.get("policy_fingerprint") != policy_fingerprint:
        raise ValueError("baseline evidence policy does not match the current policy")
    snapshot_file = str(record.get("workspace_snapshot_file") or "")
    if not snapshot_file or Path(snapshot_file).is_absolute() or ".." in Path(snapshot_file).parts:
        raise ValueError("baseline evidence does not declare a safe workspace snapshot file")
    snapshot_path = evidence_dir / snapshot_file
    if not snapshot_path.is_file():
        raise ValueError("baseline evidence does not contain its workspace source snapshot")
    snapshot = _load_json(snapshot_path)
    if snapshot.get("fingerprint") != record.get("workspace_snapshot_fingerprint"):
        raise ValueError("baseline workspace source snapshot fingerprint does not match its record")
    record = dict(record)
    record["workspace_snapshot"] = snapshot
    summary_path = evidence_dir / "run-summary.json"
    if not summary_path.is_file():
        raise ValueError("--baseline-evidence does not contain run-summary.json")
    summary = _load_json(summary_path)
    if summary.get("run_kind") != "baseline":
        raise ValueError("--baseline-evidence run-summary is not a baseline run")
    record["baseline_claim_statuses"] = {
        str(item.get("id")): str(item.get("status") or "NOT_EXECUTED")
        for item in summary.get("claim_results") or []
        if isinstance(item, dict) and item.get("id")
    }
    return record


def _state_path(state_dir: Path, target: dict[str, Any]) -> Path:
    return state_dir / f"{_sha256_text(str(target['id']))}.json"


def _load_loop_state(state_dir: Path, target: dict[str, Any]) -> dict[str, Any] | None:
    path = _state_path(state_dir, target)
    if not path.is_file():
        return None
    record = _load_json(path)
    if record.get("target_identity") != _target_identity(target):
        raise ValueError("quality-loop state belongs to a changed target; create a new 目标 ID")
    return record


def _write_loop_state(state_dir: Path, target: dict[str, Any], record: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _state_path(state_dir, target).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _verify_loop_round(
    *,
    target: dict[str, Any],
    baseline_evidence: Path | None,
    policy_fingerprint: str,
    state_dir: Path,
    baseline: bool,
    workspace: Path,
    target_path: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if target["context"] == "ci":
        return None, None
    if baseline:
        if int(target["current_round"]) != 1:
            raise ValueError("a new baseline must start at 当前轮次：1")
        return None, None
    if target["kind"] in TRANSITION_TARGET_KINDS:
        baseline_record = _load_baseline(
            baseline_evidence, workspace=workspace, target=target, policy_fingerprint=policy_fingerprint
        )
    else:
        if baseline_evidence is not None:
            baseline_record = _load_baseline(
                baseline_evidence, workspace=workspace, target=target, policy_fingerprint=policy_fingerprint
            )
        else:
            baseline_record = {
                "decision": None,
                "workspace_snapshot": _workspace_snapshot(workspace, ignored_roots=(state_dir,)),
                "baseline_claim_statuses": {},
            }
    runtime_roots = tuple(
        path
        for path in (baseline_evidence, state_dir)
        if path is not None
    )
    violations = _scope_violations(
        workspace,
        baseline=baseline_record,
        allowed_paths=tuple(target["allowed_paths"]),
        ignored_roots=runtime_roots,
    )
    if violations:
        preview = ", ".join(violations[:20])
        suffix = "" if len(violations) <= 20 else f" (+{len(violations) - 20} more)"
        raise ValueError(
            "workspace changes are outside the frozen target 允许变更路径: " + preview + suffix
            + "; update the target scope and create a new --baseline record"
        )
    _validate_abstraction_record(
        workspace,
        baseline=baseline_record,
        target=target,
        ignored_roots=runtime_roots,
    )
    state = _load_loop_state(state_dir, target)
    if state is None:
        state = {
            "schema_version": 1,
            "target_identity": _target_identity(target),
            "policy_fingerprint": policy_fingerprint,
            "baseline_evidence": str(baseline_evidence),
            "baseline_decision": baseline_record.get("decision"),
            "required_round": 1,
            "completed": False,
            "stopped": False,
            "stagnant_rounds": 0,
            "round_history": [],
        }
    if state.get("completed"):
        raise ValueError("this target already converged; stop the loop or create a new target")
    if state.get("stopped"):
        reason = str(state.get("stop_reason") or "repair budget exhausted")
        raise ValueError(f"this target is stopped ({reason}); stop patching and deliver its evidence")
    if int(state.get("required_round") or 1) != int(target["current_round"]):
        raise ValueError(
            f"target 当前轮次 must be {state.get('required_round')} after its prior result, not {target['current_round']}"
        )
    repair_fingerprint, repair_paths = _repair_change_fingerprint(
        workspace,
        baseline=baseline_record,
        allowed_paths=tuple(target["allowed_paths"]),
        target_path=target_path,
        ignored_roots=runtime_roots,
    )
    if target["kind"] in TRANSITION_TARGET_KINDS and not repair_paths:
        raise ValueError(
            f"{target['kind']} target verification requires an actual in-scope candidate change relative to its baseline"
        )
    if int(target["current_round"]) > 1 and repair_fingerprint == str(state.get("last_repair_fingerprint") or ""):
        raise ValueError(
            "no actual in-scope repair detected since the preceding failed round; "
            "changing only 当前轮次 cannot advance the quality loop"
        )
    state["current_repair_fingerprint"] = repair_fingerprint
    state["current_repair_paths"] = repair_paths
    return baseline_record, state


def _failure_classification(result: dict[str, Any]) -> str:
    if result["status"] == BLOCKED:
        if result.get("metadata", {}).get("failure_kind") == "dependency_closure":
            return "dependency_closure"
        return "environment"
    if result.get("exit_code") == 124:
        return "timeout"
    category = str(result.get("category") or "")
    if "test" in category or "coverage" in category or "regression" in category:
        return "test_or_contract"
    if category in {"architecture", "skill", "syntax", "release"}:
        return "configuration_or_architecture"
    return "verification"


def _repair_plan(
    results: list[dict[str, Any]],
    *,
    workspace: Path,
    policy_path: Path,
    mode: str,
    target_path: Path | None,
    target: dict[str, Any] | None,
    evidence_dir: Path,
    baseline_evidence: Path | None,
    loop_status: str | None,
) -> dict[str, Any]:
    repairs: list[dict[str, Any]] = []
    for result in results:
        if result["status"] not in {FAIL, BLOCKED}:
            continue
        rerun: list[str] = [
            sys.executable,
            "-B",
            str(workspace / "scripts" / "quality_loop.py"),
            "--workspace-root",
            str(workspace),
            "--policy",
            str(policy_path),
            "--mode",
            mode,
        ]
        controller_input = str(result["id"]) == "quality-controller-input"
        if not controller_input:
            rerun.extend(["--rerun-from", str(result["id"])])
        if target_path is not None:
            rerun.extend(["--target", str(target_path)])
        if baseline_evidence is not None:
            rerun.extend(["--baseline-evidence", str(baseline_evidence)])
        repairs.append(
            {
                "gate_id": result["id"],
                "owner": result["owner"],
                "category": result["category"],
                "blocking_level": result.get("blocking_level", "required"),
                "failure_kind": _failure_classification(result),
                "repair_playbook": result.get("repair_playbook"),
                "evidence": {
                    "stderr": f"steps/{result['id']}.stderr.txt",
                    "stdout": f"steps/{result['id']}.stdout.txt",
                },
                "next_action": (
                    "stop local patching and create a new architecture target with revised assumptions"
                    if loop_status == "ARCHITECTURE_REPLAN_REQUIRED"
                    else (
                        "provision the declared environment"
                        if result["status"] == BLOCKED
                        else (
                            "repair the target, policy or baseline input; if its frozen scope changes, create a new --baseline record"
                            if controller_input
                            else "apply the smallest fix within the declared target scope"
                        )
                    )
                ),
                "rerun": rerun,
            }
        )
    return {
        "generated_at": _now(),
        "mode": mode,
        "target_identity": _target_identity(target) if target else None,
        "loop_status": loop_status,
        "repairs": repairs,
    }


def _blocked_prerequisite_result(step: dict[str, Any], prerequisites: list[str], reason: str) -> dict[str, Any]:
    return {
        "id": step["id"],
        "name": step.get("name", step["id"]),
        "status": BLOCKED,
        "owner": step.get("owner", "unassigned"),
        "category": step.get("category", "verification"),
        "blocking_level": step.get("blocking_level", "required"),
        "repair_playbook": step.get("repair_playbook", "repair the policy dependency closure"),
        "depends_on": list(step.get("depends_on") or []),
        "started_at": _now(),
        "ended_at": _now(),
        "exit_code": 78,
        "duration_ms": 0,
        "stdout": "",
        "stderr": f"selected dependency closure is incomplete for: {', '.join(prerequisites)}; {reason}",
        "metadata": {"failure_kind": "dependency_closure", "missing_prerequisites": prerequisites},
    }


def _workspace_immutability_result(
    start_snapshot: dict[str, Any], end_snapshot: dict[str, Any]
) -> dict[str, Any]:
    start_files = start_snapshot.get("files") or {}
    end_files = end_snapshot.get("files") or {}
    changed = sorted(
        name
        for name in set(start_files) | set(end_files)
        if start_files.get(name) != end_files.get(name)
    )
    return {
        "id": "controller-workspace-immutability",
        "name": "controller-workspace-immutability",
        "status": FAIL,
        "owner": "quality-controller",
        "category": "quality-evidence",
        "blocking_level": "required",
        "repair_playbook": (
            "make every Gate read-only with respect to governed source; write generated evidence only "
            "under declared evidence or coverage directories"
        ),
        "depends_on": [],
        "started_at": _now(),
        "ended_at": _now(),
        "exit_code": 1,
        "duration_ms": 0,
        "stdout": "",
        "stderr": "quality Gate execution changed governed workspace files: " + ", ".join(changed[:50]),
        "metadata": {
            "failure_kind": "source_changed_during_verification",
            "changed_files": changed,
            "start_fingerprint": start_snapshot.get("fingerprint"),
            "end_fingerprint": end_snapshot.get("fingerprint"),
        },
    }


def _run_loop_unlocked(
    workspace: Path,
    policy_path: Path,
    *,
    mode: str,
    evidence_dir: Path,
    rerun_from: str | None,
    target_path: Path,
    baseline: bool,
    baseline_evidence: Path | None,
    prior_evidence: Path | None,
    state_dir: Path,
) -> dict[str, Any]:
    if prior_evidence is not None:
        raise ValueError("--prior-evidence is no longer supported; every targeted run executes its dependency closure and only a full current-source run can complete a target")
    policy = _load_json(policy_path)
    ordered_steps = _validate_policy(policy)
    if mode == "release" and target_path is not None:
        configured_signing_key = os.getenv("QUALITY_EVIDENCE_SIGNING_KEY", "")
        if len(configured_signing_key.encode("utf-8")) < 32:
            raise ValueError("release mode requires QUALITY_EVIDENCE_SIGNING_KEY with at least 32 bytes; local mutable trust keys cannot authorize a protected release")
    target = _parse_target(target_path, workspace=workspace)
    replan_predecessor = _validate_replan_predecessor(workspace, target=target)
    _validate_claim_gate_contracts(target, ordered_steps)
    all_steps = _steps_for_mode(ordered_steps, mode)
    if (
        not baseline
        and rerun_from is None
        and MODE_RANK[mode] < MODE_RANK[str(target["minimum_mode"])]
    ):
        raise ValueError(
            f"target requires 最低质量模式：{target['minimum_mode']}; --mode {mode} is insufficient"
        )
    policy_fingerprint = _sha256_file(policy_path)
    baseline_record, state = _verify_loop_round(
        target=target,
        baseline_evidence=baseline_evidence,
        policy_fingerprint=policy_fingerprint,
        state_dir=state_dir,
        baseline=baseline,
        workspace=workspace,
        target_path=target_path,
    )
    ignored_snapshot_roots = tuple(
        path for path in (evidence_dir, baseline_evidence) if path is not None
    )
    run_start_snapshot = _workspace_snapshot(workspace, ignored_roots=ignored_snapshot_roots)
    baseline_snapshot = run_start_snapshot if baseline and target["context"] == "local-change" else None
    steps = _downstream_steps(all_steps, rerun_from) if rerun_from else all_steps
    evidence_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(target_path, evidence_dir / "target.md")
    shutil.copyfile(policy_path, evidence_dir / "quality-loop-policy.json")
    claim_manifest_evidence_file = "claim-manifest.json"
    shutil.copyfile(
        workspace / str(target["claim_manifest"]),
        evidence_dir / claim_manifest_evidence_file,
    )
    requirement_catalog_evidence_file: str | None = None
    if target.get("requirement_catalog"):
        requirement_catalog_evidence_file = "requirement-catalog.json"
        shutil.copyfile(
            workspace / str(target["requirement_catalog"]),
            evidence_dir / requirement_catalog_evidence_file,
        )

    # Targeted runs re-execute their entire dependency closure.  Historical PASS
    # rows and copied JUnit/coverage files are never accepted as prerequisites.
    reusable: dict[str, dict[str, Any]] = {}

    results: list[dict[str, Any]] = []
    result_by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        dependencies = [str(dep) for dep in step.get("depends_on") or []]
        absent_prerequisites = [dep for dep in dependencies if dep not in result_by_id and dep not in reusable]
        failed_dependencies = [
            dep
            for dep in dependencies
            if dep in result_by_id and result_by_id[dep]["status"] != PASS
        ]
        if absent_prerequisites:
            result = _blocked_prerequisite_result(
                step,
                absent_prerequisites,
                "the selected dependency closure is internally incomplete",
            )
        elif failed_dependencies:
            result = {
                "id": step["id"],
                "name": step.get("name", step["id"]),
                "status": UPSTREAM_SKIPPED,
                "owner": step.get("owner", "unassigned"),
                "category": step.get("category", "verification"),
                "blocking_level": step.get("blocking_level", "required"),
                "repair_playbook": step.get("repair_playbook", "repair the failed upstream gate"),
                "depends_on": dependencies,
                "started_at": _now(),
                "ended_at": _now(),
                "exit_code": None,
                "duration_ms": 0,
                "stdout": "",
                "stderr": "upstream gate did not pass: " + ", ".join(failed_dependencies),
                "metadata": {"failed_dependencies": failed_dependencies},
            }
        else:
            print(f"[quality-loop] {mode} running {step['id']}", file=sys.stderr, flush=True)
            result = _run_step(workspace, evidence_dir, mode, step)
            print(f"[quality-loop] {step['id']} -> {result['status']}", file=sys.stderr, flush=True)
        results.append(result)
        result_by_id[str(result["id"])] = result
        _write_step_evidence(evidence_dir, result)

    run_end_snapshot = _workspace_snapshot(workspace, ignored_roots=ignored_snapshot_roots)
    if run_end_snapshot["fingerprint"] != run_start_snapshot["fingerprint"]:
        immutability_result = _workspace_immutability_result(run_start_snapshot, run_end_snapshot)
        results.append(immutability_result)
        result_by_id[str(immutability_result["id"])] = immutability_result
        _write_step_evidence(evidence_dir, immutability_result)

    decision = _decision(results)
    selected_gate_ids = [str(step["id"]) for step in steps]
    required_gate_ids = [
        str(step["id"])
        for step in all_steps
        if str(step.get("blocking_level") or "required") in BLOCKING_LEVELS
    ]
    result_statuses = {str(result["id"]): str(result["status"]) for result in results}
    claim_results = _claim_results(
        target,
        result_by_id,
        mode=mode,
        evidence_dir=evidence_dir,
        snapshot_files=set(run_end_snapshot["files"]),
    )
    unverified_claim_ids = [
        str(item["id"]) for item in claim_results if item["status"] != "VERIFIED"
    ]
    baseline_transition_unverified_claim_ids: list[str] = []
    if not baseline and target["kind"] in TRANSITION_TARGET_KINDS:
        baseline_claim_statuses = (baseline_record or {}).get("baseline_claim_statuses") or {}
        baseline_transition_unverified_claim_ids = [
            str(claim["id"])
            for claim in target["claims"]
            if claim["closure_requirement"] == "regression-transition"
            and baseline_claim_statuses.get(str(claim["id"])) != "FAILED"
        ]
    complete_gate_set = rerun_from is None and all(
        result_statuses.get(gate_id) == PASS for gate_id in required_gate_ids
    )
    completion_eligible = bool(
        not baseline
        and decision == PASS
        and complete_gate_set
        and MODE_RANK[mode] >= MODE_RANK[str(target["minimum_mode"])]
        and not unverified_claim_ids
        and not baseline_transition_unverified_claim_ids
    )

    loop_status: str | None = None
    if target["context"] == "local-change":
        if baseline:
            if target["kind"] in TRANSITION_TARGET_KINDS and (
                decision != FAIL
                or not any(item["status"] == "FAILED" for item in claim_results)
            ):
                raise ValueError(
                    f"{target['kind']} target baseline must reproduce at least one failing acceptance claim"
                )
            loop_status = "BASELINE_RECORDED"
            if baseline_snapshot is None:  # defensive: local baseline always creates one
                raise RuntimeError("local baseline did not capture a workspace source snapshot")
            snapshot_file = "workspace-baseline.json"
            (evidence_dir / snapshot_file).write_text(
                json.dumps(baseline_snapshot, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            baseline_record = {
                "schema_version": 2,
                "generated_at": _now(),
                "decision": decision,
                "evidence_dir": str(evidence_dir),
                "target_identity": _target_identity(target),
                "policy_fingerprint": policy_fingerprint,
                "mode": mode,
                "replan_predecessor": replan_predecessor,
                "workspace_snapshot_file": snapshot_file,
                "workspace_snapshot_fingerprint": baseline_snapshot["fingerprint"],
            }
            (evidence_dir / "baseline-record.json").write_text(
                json.dumps(baseline_record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _write_loop_state(
                state_dir,
                target,
                {
                    "schema_version": 2,
                    "target_identity": _target_identity(target),
                    "policy_fingerprint": policy_fingerprint,
                    "baseline_evidence": str(evidence_dir),
                    "baseline_decision": decision,
                    "replan_predecessor": replan_predecessor,
                    "required_round": 1,
                    "completed": False,
                    "stopped": False,
                    "stagnant_rounds": 0,
                    "round_history": [],
                    "updated_at": _now(),
                },
            )
        elif rerun_from is not None:
            # A focused rerun is diagnostic evidence only.  It must never consume
            # a repair round or close the target; the same source snapshot still
            # needs one complete minimum-mode pass.
            if decision == PASS:
                loop_status = "TARGETED_REGRESSION_PASSED"
            elif decision == BLOCKED:
                loop_status = "TARGETED_REGRESSION_BLOCKED"
            else:
                loop_status = "TARGETED_REGRESSION_FAILED"
        elif state is not None:
            state = dict(state)
            state["updated_at"] = _now()
            state["last_evidence"] = str(evidence_dir)
            state["last_repair_fingerprint"] = state.pop("current_repair_fingerprint", None)
            state["last_repair_paths"] = state.pop("current_repair_paths", [])
            convergence: dict[str, Any] | None = None
            if completion_eligible:
                loop_status = "CONVERGED"
                state.update(
                    {
                        "completed": True,
                        "stopped": False,
                        "stop_reason": None,
                        "required_round": int(target["current_round"]),
                        "stagnant_rounds": 0,
                    }
                )
            elif decision == FAIL:
                convergence = _advance_convergence_state(
                    state,
                    current_round=int(target["current_round"]),
                    results=results,
                )
                if int(target["current_round"]) >= int(target["max_rounds"]):
                    loop_status = "STOPPED_MAX_REPAIRS"
                    state.update(
                        {
                            "stopped": True,
                            "completed": False,
                            "stop_reason": "max repair rounds reached",
                            "required_round": int(target["current_round"]),
                        }
                    )
                elif int(convergence["stagnant_rounds"]) >= STAGNATION_LIMIT:
                    loop_status = "ARCHITECTURE_REPLAN_REQUIRED"
                    state.update(
                        {
                            "stopped": True,
                            "completed": False,
                            "stop_reason": "two consecutive rounds without measurable improvement",
                            "required_round": int(target["current_round"]),
                        }
                    )
                else:
                    loop_status = "REPAIR_REQUIRED"
                    state.update(
                        {
                            "stopped": False,
                            "completed": False,
                            "stop_reason": None,
                            "required_round": int(target["current_round"]) + 1,
                        }
                    )
            else:
                loop_status = "BLOCKED_BY_ENVIRONMENT"
            _write_loop_state(state_dir, target, state)
    else:
        if rerun_from is not None:
            loop_status = (
                "TARGETED_REGRESSION_PASSED"
                if decision == PASS
                else "TARGETED_REGRESSION_BLOCKED"
                if decision == BLOCKED
                else "TARGETED_REGRESSION_FAILED"
            )
        elif completion_eligible:
            loop_status = "CI_VERIFIED"
        elif decision == BLOCKED:
            loop_status = "BLOCKED_BY_ENVIRONMENT"
        elif decision == FAIL:
            loop_status = "CI_FAILED"

    if baseline:
        verification_snapshot = baseline_snapshot
        workspace_snapshot_file = "workspace-baseline.json"
    else:
        verification_snapshot = run_end_snapshot
        workspace_snapshot_file = "workspace-snapshot.json"
        (evidence_dir / workspace_snapshot_file).write_text(
            json.dumps(verification_snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if verification_snapshot is None:
        raise RuntimeError("quality run did not capture a workspace source snapshot")

    summary = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "generated_at": _now(),
        "workspace_version": _read(workspace / "VERSION").strip() or str(policy.get("version") or "unknown"),
        "ci_run_identity_fingerprint_sha256": str(os.getenv("PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT") or "").strip().casefold() or None,
        "mode": mode,
        "run_kind": "baseline" if baseline else "verification",
        "decision": decision,
        "loop_status": loop_status,
        "evidence_dir": str(evidence_dir),
        "judge_identity": {
            "root": os.getenv("SKILL_JUDGE_ROOT", str(Path(__file__).resolve().parents[1])),
            "trust_mode": os.getenv("SKILL_JUDGE_TRUST_MODE", "workspace-fallback"),
            "controller_sha256": _sha256_file(Path(__file__).resolve()),
        },
        "target": str(target_path),
        "target_identity": _target_identity(target),
        "target_minimum_mode_declared": target["minimum_mode_declared"],
        "target_minimum_mode_derived": target["minimum_mode_derived"],
        "target_minimum_mode_effective": target["minimum_mode"],
        "replan_predecessor": replan_predecessor,
        "claim_manifest": target["claim_manifest"],
        "claim_manifest_fingerprint": target["claim_manifest_fingerprint"],
        "claim_manifest_evidence_file": claim_manifest_evidence_file,
        "requirement_catalog": target.get("requirement_catalog"),
        "requirement_profile": target.get("requirement_profile"),
        "requirement_catalog_fingerprint": target.get(
            "requirement_catalog_fingerprint"
        ),
        "requirement_catalog_evidence_file": requirement_catalog_evidence_file,
        "claim_results": claim_results,
        "unverified_claim_ids": unverified_claim_ids,
        "baseline_transition_unverified_claim_ids": baseline_transition_unverified_claim_ids,
        "policy_fingerprint": policy_fingerprint,
        "rerun_from": rerun_from,
        "prior_evidence": None,
        "reused_prerequisites": [],
        "missing_prerequisites": [],
        "workspace_snapshot_start_fingerprint": run_start_snapshot["fingerprint"],
        "workspace_snapshot_fingerprint": verification_snapshot["fingerprint"],
        "workspace_snapshot_file": workspace_snapshot_file,
        "selected_gate_ids": selected_gate_ids,
        "required_gate_ids": required_gate_ids,
        "gate_contract_fingerprints": _gate_contract_fingerprints(all_steps),
        "completion_eligible": completion_eligible,
        "quality_dimensions": _quality_dimensions(results, mode=mode),
        "evidence_attestation_file": "evidence-attestation.json",
        "convergence": (
            {
                "current_round": int(target["current_round"]),
                "max_rounds": int(target["max_rounds"]),
                "stagnant_rounds": int((state or {}).get("stagnant_rounds") or 0),
                "last_failure_count": (state or {}).get("last_failure_count"),
                "failed_gate_ids": list((state or {}).get("failed_gate_ids") or []),
                "upstream_skipped_gate_ids": list(
                    (state or {}).get("upstream_skipped_gate_ids") or []
                ),
            }
            if target["context"] == "local-change"
            else None
        ),
        "results": results,
    }
    (evidence_dir / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    repair = _repair_plan(
        results,
        workspace=workspace,
        policy_path=policy_path,
        mode=mode,
        target_path=target_path,
        target=target,
        evidence_dir=evidence_dir,
        baseline_evidence=baseline_evidence,
        loop_status=loop_status,
    )
    (evidence_dir / "repair-plan.json").write_text(
        json.dumps(repair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_evidence_attestation(workspace, evidence_dir)
    return summary



@contextlib.contextmanager
def _exclusive_quality_run(
    *,
    evidence_dir: Path,
    state_dir: Path,
    target_path: Path,
):
    """Prevent concurrent controllers from mutating the same evidence or target state.

    Locks live outside the governed evidence directory so a rejected contender
    cannot change or invalidate the active run's attestation.  Both the output
    directory and target/state identity are locked; acquiring in sorted path
    order prevents deadlocks when two invocations overlap both resources.
    """
    evidence_lock = evidence_dir.parent / f".{evidence_dir.name}.quality-run.lock"
    target_digest = hashlib.sha256(str(target_path.resolve()).encode("utf-8")).hexdigest()
    state_lock = state_dir / ".run-locks" / f"{target_digest}.lock"
    lock_paths = sorted({evidence_lock.resolve(), state_lock.resolve()}, key=str)
    held: list[tuple[int, Path]] = []
    try:
        for lock_path in lock_paths:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                os.close(descriptor)
                owner = ""
                try:
                    owner = lock_path.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
                detail = f"; active owner: {owner}" if owner else ""
                raise QualityRunConflictError(
                    f"another quality controller already owns {lock_path}{detail}"
                ) from exc
            metadata = json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": _now(),
                    "evidence_dir": str(evidence_dir),
                    "state_dir": str(state_dir),
                    "target": str(target_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            os.ftruncate(descriptor, 0)
            os.write(descriptor, metadata.encode("utf-8"))
            os.fsync(descriptor)
            held.append((descriptor, lock_path))
        yield
    finally:
        for descriptor, _lock_path in reversed(held):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def run_loop(
    workspace: Path,
    policy_path: Path,
    *,
    mode: str,
    evidence_dir: Path,
    rerun_from: str | None,
    target_path: Path,
    baseline: bool,
    baseline_evidence: Path | None,
    prior_evidence: Path | None,
    state_dir: Path,
) -> dict[str, Any]:
    with _exclusive_quality_run(
        evidence_dir=evidence_dir,
        state_dir=state_dir,
        target_path=target_path,
    ):
        if evidence_dir.exists() and any(evidence_dir.iterdir()):
            raise QualityRunConflictError(
                f"evidence directory must be new and empty: {evidence_dir}"
            )
        return _run_loop_unlocked(
            workspace,
            policy_path,
            mode=mode,
            evidence_dir=evidence_dir,
            rerun_from=rerun_from,
            target_path=target_path,
            baseline=baseline,
            baseline_evidence=baseline_evidence,
            prior_evidence=prior_evidence,
            state_dir=state_dir,
        )

def _controller_failure(
    *,
    workspace: Path,
    policy_path: Path,
    mode: str,
    evidence_dir: Path,
    target_path: Path | None,
    baseline_evidence: Path | None,
    prior_evidence: Path | None,
    error: Exception,
) -> dict[str, Any]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "id": "quality-controller-input",
        "name": "quality-controller-input",
        "status": FAIL,
        "owner": "scripts/quality_loop.py",
        "category": "controller",
        "blocking_level": "required",
        "repair_playbook": "repair the target, policy or baseline record before running a gate",
        "depends_on": [],
        "started_at": _now(),
        "ended_at": _now(),
        "exit_code": 2,
        "duration_ms": 0,
        "stdout": "",
        "stderr": str(error),
        "metadata": {"exception": error.__class__.__name__},
    }
    _write_step_evidence(evidence_dir, result)
    snapshot = _workspace_snapshot(
        workspace,
        ignored_roots=tuple(
            path for path in (evidence_dir, baseline_evidence) if path is not None
        ),
    )
    snapshot_file = "workspace-snapshot.json"
    (evidence_dir / snapshot_file).write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    controller_contract = {
        "id": "quality-controller-input",
        "owner": "scripts/quality_loop.py",
        "category": "controller",
        "blocking_level": "required",
        "contract": "validate target, policy, baseline, protected release key and rerun semantics before gates",
    }
    target_identity = None
    target_contract: dict[str, Any] | None = None
    if target_path is not None and target_path.is_file():
        try:
            target_contract = _parse_target(target_path, workspace=workspace)
            target_identity = _target_identity(target_contract)
        except (OSError, ValueError):
            target_identity = None
            target_contract = None
    policy_fingerprint = _sha256_file(policy_path) if policy_path.is_file() else None
    summary = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "generated_at": _now(),
        "workspace_version": _read(workspace / "VERSION").strip() or "unknown",
        "ci_run_identity_fingerprint_sha256": str(os.getenv("PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT") or "").strip().casefold() or None,
        "mode": mode,
        "run_kind": "verification",
        "decision": FAIL,
        "loop_status": "INVALID_INPUT",
        "evidence_dir": str(evidence_dir),
        "judge_identity": {
            "root": os.getenv("SKILL_JUDGE_ROOT", str(Path(__file__).resolve().parents[1])),
            "trust_mode": os.getenv("SKILL_JUDGE_TRUST_MODE", "workspace-fallback"),
            "controller_sha256": _sha256_file(Path(__file__).resolve()),
        },
        "target": str(target_path) if target_path else None,
        "target_identity": target_identity,
        "target_minimum_mode_declared": (
            target_contract.get("minimum_mode_declared") if target_contract else None
        ),
        "target_minimum_mode_derived": (
            target_contract.get("minimum_mode_derived") if target_contract else None
        ),
        "target_minimum_mode_effective": (
            target_contract.get("minimum_mode") if target_contract else None
        ),
        "replan_predecessor": None,
        "claim_manifest": target_contract.get("claim_manifest") if target_contract else None,
        "claim_manifest_fingerprint": (
            target_contract.get("claim_manifest_fingerprint") if target_contract else None
        ),
        "claim_manifest_evidence_file": None,
        "claim_results": [],
        "unverified_claim_ids": [],
        "policy_fingerprint": policy_fingerprint,
        "rerun_from": None,
        "prior_evidence": str(prior_evidence) if prior_evidence else None,
        "reused_prerequisites": [],
        "missing_prerequisites": [],
        "workspace_snapshot_start_fingerprint": snapshot["fingerprint"],
        "workspace_snapshot_fingerprint": snapshot["fingerprint"],
        "workspace_snapshot_file": snapshot_file,
        "selected_gate_ids": ["quality-controller-input"],
        "required_gate_ids": ["quality-controller-input"],
        "gate_contract_fingerprints": {
            "quality-controller-input": _sha256_text(
                json.dumps(controller_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
        },
        "completion_eligible": False,
        "evidence_attestation_file": "evidence-attestation.json",
        "results": [result],
    }
    (evidence_dir / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    repair = _repair_plan(
        [result],
        workspace=workspace,
        policy_path=policy_path,
        mode=mode,
        target_path=target_path,
        target=None,
        evidence_dir=evidence_dir,
        baseline_evidence=baseline_evidence,
        loop_status="INVALID_INPUT",
    )
    (evidence_dir / "repair-plan.json").write_text(
        json.dumps(repair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_evidence_attestation(workspace, evidence_dir)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one bounded, reproducible quality validation pass.")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--policy", default="governance/quality-loop-policy.json")
    parser.add_argument("--mode", choices=MODES, default="static")
    parser.add_argument("--evidence-dir")
    parser.add_argument("--target", help="required filled quality-loop target markdown record")
    parser.add_argument("--baseline", action="store_true", help="record this pass as the pre-change baseline")
    parser.add_argument("--baseline-evidence", help="evidence directory from the required local baseline pass")
    parser.add_argument("--prior-evidence", help="removed compatibility flag; supplying it is an error because historical PASS evidence is never reused")
    parser.add_argument("--state-dir", help="local quality-loop state directory; defaults to .quality/loop-state")
    parser.add_argument(
        "--rerun-from",
        help="rerun this gate, all dependency ancestors, and all policy-declared downstream gates",
    )
    parser.add_argument("--list-steps", action="store_true")
    args = parser.parse_args()
    workspace = Path(args.workspace_root).resolve()
    policy_path = (workspace / args.policy).resolve()
    judge_root_raw = os.environ.get("SKILL_JUDGE_ROOT")
    judge_trust_mode = os.environ.get("SKILL_JUDGE_TRUST_MODE", "workspace-fallback")
    if judge_trust_mode == "external-readonly":
        if not judge_root_raw:
            print(json.dumps({"decision": FAIL, "error": "external Judge root is missing"}, ensure_ascii=False), file=sys.stderr)
            return 1
        trust_errors = verify_trusted_candidate(workspace, Path(judge_root_raw).resolve())
        if trust_errors:
            print(
                json.dumps(
                    {"decision": FAIL, "loop_status": "TRUSTED_JUDGE_INPUT_CHANGED", "errors": trust_errors},
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1
    if args.list_steps:
        try:
            steps = _steps_for_mode(_validate_policy(_load_json(policy_path)), args.mode)
            if args.rerun_from:
                steps = _downstream_steps(steps, args.rerun_from)
            print(json.dumps({"mode": args.mode, "steps": [step["id"] for step in steps]}, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"decision": FAIL, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 1
    evidence_raw = args.evidence_dir or os.getenv("QUALITY_EVIDENCE_DIR")
    evidence_dir = Path(evidence_raw).expanduser().resolve() if evidence_raw else workspace / ".quality" / "evidence" / _safe_run_id()
    target_path = Path(args.target).expanduser().resolve() if args.target else None
    baseline_evidence = Path(args.baseline_evidence).expanduser().resolve() if args.baseline_evidence else None
    prior_evidence = Path(args.prior_evidence).expanduser().resolve() if args.prior_evidence else None
    state_raw = args.state_dir or os.getenv("QUALITY_LOOP_STATE_DIR")
    state_dir = Path(state_raw).expanduser().resolve() if state_raw else workspace / ".quality" / "loop-state"
    try:
        if target_path is None:
            raise ValueError("--target is required for every quality validation run")
        summary = run_loop(
            workspace,
            policy_path,
            mode=args.mode,
            evidence_dir=evidence_dir,
            rerun_from=args.rerun_from,
            target_path=target_path,
            baseline=args.baseline,
            baseline_evidence=baseline_evidence,
            prior_evidence=prior_evidence,
            state_dir=state_dir,
        )
    except QualityRunConflictError as exc:
        # A rejected contender must never write into the active run's evidence
        # directory.  Report the conflict only on this invocation's stdout.
        summary = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "generated_at": _now(),
            "workspace_version": _read(workspace / "VERSION").strip() or "unknown",
            "ci_run_identity_fingerprint_sha256": str(os.getenv("PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT") or "").strip().casefold() or None,
            "mode": args.mode,
            "run_kind": "controller-rejection",
            "decision": FAIL,
            "loop_status": "CONCURRENT_RUN_REJECTED",
            "evidence_dir": str(evidence_dir),
            "completion_eligible": False,
            "error": str(exc),
            "results": [
                {
                    "id": "quality-controller-lock",
                    "status": FAIL,
                    "stderr": str(exc),
                }
            ],
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        summary = _controller_failure(
            workspace=workspace,
            policy_path=policy_path,
            mode=args.mode,
            evidence_dir=evidence_dir,
            target_path=target_path,
            baseline_evidence=baseline_evidence,
            prior_evidence=prior_evidence,
            error=exc,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["decision"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
