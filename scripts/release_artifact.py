from __future__ import annotations

"""Shared clean-release construction and verification primitives."""

import hashlib
import json
import os
import re
import stat
import shutil
import sys
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from source_paths import (
    RUNTIME_ARTIFACT_LABELS,
    RUNTIME_ARTIFACT_PREFIXES,
    is_runtime_artifact_path,
)

TOP_LEVEL_FILES = {
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "CHANGELOG.md",
    "Makefile",
    "README.md",
    "VERSION",
    "skillctl.py",
}
TOP_LEVEL_DIRS = {
    ".agents",
    ".claude",
    ".codex",
    ".github",
    "architecture-skill",
    "contracts",
    "deployment",
    "docs",
    "governance",
    "scripts",
    "services",
    "skill-system",
}
FORBIDDEN_PARTS = {
    ".git",
    ".idea",
    ".quality",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "coverage",
    "node_modules",
}
FORBIDDEN_NAMES = {
    ".coverage",
    ".DS_Store",
    ".env",
    "quality-evidence.key",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log"}
RELEASE_METADATA = {
    "release/FILE_LIST.txt",
    "release/MANIFEST.json",
    "release/SHA256SUMS.txt",
    "release/VALIDATION_REPORT.md",
}


def is_release_metadata(name: str) -> bool:
    return name == "release" or name.startswith("release/")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_forbidden(relative: Path) -> bool:
    if relative.name in FORBIDDEN_NAMES or relative.suffix.lower() in FORBIDDEN_SUFFIXES:
        return True
    if any(part in FORBIDDEN_PARTS for part in relative.parts) or is_runtime_artifact_path(relative):
        return True
    if relative.parts and relative.parts[0] == "release":
        return True
    if "dist" in relative.parts:
        # A clean frontend dist is rebuilt after copy; source dist is never trusted.
        return True
    return False


def iter_source_files(workspace: Path) -> Iterable[tuple[Path, Path]]:
    for name in sorted(TOP_LEVEL_FILES):
        path = workspace / name
        if path.is_file():
            yield path, Path(name)
    for dirname in sorted(TOP_LEVEL_DIRS):
        root = workspace / dirname
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(workspace)
            if not is_forbidden(relative):
                yield path, relative


def copy_release_sources(workspace: Path, stage: Path) -> list[str]:
    copied: list[str] = []
    for source, relative in iter_source_files(workspace):
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative.as_posix())
    return copied


def files_under(root: Path) -> list[str]:
    return [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]


def payload_files(stage: Path) -> list[str]:
    return [name for name in files_under(stage) if not is_release_metadata(name)]


def source_fingerprint(stage: Path) -> str:
    entries = {name: sha256_file(stage / name) for name in payload_files(stage)}
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return sha256_text(canonical)


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout[-6000:]}\nstderr:\n{completed.stderr[-6000:]}"
        )


def build_frontend(workspace: Path, stage: Path, *, mode: str) -> str:
    source_frontend = workspace / "services/agent-service/frontend"
    stage_frontend = stage / "services/agent-service/frontend"
    dist = stage_frontend / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    if mode == "skip":
        raise RuntimeError("clean release cannot skip frontend rebuild")
    if mode == "npm-ci":
        _run(["npm", "ci", "--ignore-scripts=false"], cwd=stage_frontend)
        _run(["npm", "run", "build"], cwd=stage_frontend)
        shutil.rmtree(stage_frontend / "node_modules", ignore_errors=True)
        return "npm-ci-stage"
    if mode == "existing-node-modules":
        node_modules = source_frontend / "node_modules"
        if not node_modules.is_dir():
            raise RuntimeError("existing-node-modules mode requires source frontend/node_modules")
        out_dir = dist.resolve()
        _run(
            ["npm", "run", "build", "--", "--outDir", str(out_dir), "--emptyOutDir"],
            cwd=source_frontend,
        )
        return "locked-source-node_modules"
    raise RuntimeError("frontend mode must be npm-ci or existing-node-modules")


def load_evidence_summary(
    workspace: Path,
    evidence_dir: Path | None,
    *,
    policy_path: Path | None = None,
    required_mode: str | None = None,
) -> dict[str, Any] | None:
    """Load only fully attested evidence for the exact current source snapshot.

    A clean-release must never trust a mutable ``run-summary.json`` by itself.
    The entire evidence directory is verified with the quality controller's
    HMAC trust key, and the recorded policy/source identities must still match
    the workspace being packaged.
    """
    if evidence_dir is None:
        return None
    workspace = workspace.resolve()
    evidence_dir = evidence_dir.resolve()
    path = evidence_dir / "run-summary.json"
    if not path.is_file():
        raise RuntimeError("evidence directory does not contain run-summary.json")

    from quality_loop import (
        EVIDENCE_REQUIRED_FIELDS,
        EVIDENCE_SCHEMA_VERSION,
        verify_evidence_attestation,
        workspace_snapshot,
    )

    try:
        verify_evidence_attestation(workspace, evidence_dir)
    except ValueError as exc:
        raise RuntimeError(f"release evidence attestation failed: {exc}") from exc

    payload = json.loads(path.read_text(encoding="utf-8"))
    missing_fields = [field for field in EVIDENCE_REQUIRED_FIELDS if field not in payload]
    if missing_fields:
        raise RuntimeError(
            "release evidence summary is missing required fields: " + ", ".join(missing_fields)
        )
    if payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise RuntimeError("release evidence schema version is not current")
    if payload.get("run_kind") != "verification":
        raise RuntimeError("baseline evidence cannot authorize a release")
    if payload.get("decision") != "PASS" or payload.get("completion_eligible") is not True:
        raise RuntimeError("release evidence is not a complete PASS")
    if payload.get("loop_status") not in {"CONVERGED", "CI_VERIFIED"}:
        raise RuntimeError("release evidence did not close the quality target")
    if payload.get("rerun_from") is not None:
        raise RuntimeError("targeted regression evidence cannot authorize a release")
    if payload.get("unverified_claim_ids"):
        raise RuntimeError("release evidence contains unverified quality claims")
    claim_results = payload.get("claim_results")
    if not isinstance(claim_results, list) or not claim_results:
        raise RuntimeError("release evidence does not contain claim verification results")
    if any(item.get("status") != "VERIFIED" for item in claim_results if isinstance(item, dict)):
        raise RuntimeError("one or more release quality claims are not VERIFIED")
    claim_evidence_file = evidence_dir / str(
        payload.get("claim_manifest_evidence_file") or ""
    )
    if not claim_evidence_file.is_file():
        raise RuntimeError("release evidence claim manifest is missing")
    claim_payload = json.loads(claim_evidence_file.read_text(encoding="utf-8"))
    claim_canonical = json.dumps(
        claim_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if payload.get("claim_manifest_fingerprint") != sha256_text(claim_canonical):
        raise RuntimeError("release evidence claim manifest fingerprint is invalid")
    current_claim_path = workspace / str(payload.get("claim_manifest") or "")
    if not current_claim_path.is_file():
        raise RuntimeError("current workspace claim manifest is missing")
    current_claim_payload = json.loads(current_claim_path.read_text(encoding="utf-8"))
    current_claim_canonical = json.dumps(
        current_claim_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if sha256_text(current_claim_canonical) != payload.get("claim_manifest_fingerprint"):
        raise RuntimeError("current workspace claim manifest differs from release evidence")
    if required_mode is not None:
        ranks = {"static": 0, "quick": 1, "integration": 2, "release": 3}
        actual_mode = str(payload.get("mode") or "")
        if actual_mode not in ranks or ranks[actual_mode] < ranks[required_mode]:
            raise RuntimeError(
                f"artifact requires {required_mode} evidence; received {actual_mode or 'unknown'}"
            )
    if payload.get("missing_prerequisites"):
        raise RuntimeError("release evidence has missing prerequisites")
    run_identity_fingerprint = str(payload.get("ci_run_identity_fingerprint_sha256") or "").strip().casefold()
    if required_mode == "release":
        if not re.fullmatch(r"[0-9a-f]{64}", run_identity_fingerprint):
            raise RuntimeError("release evidence run identity fingerprint is missing")
        expected_run_identity = str(os.getenv("PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT") or "").strip().casefold()
        if not expected_run_identity or run_identity_fingerprint != expected_run_identity:
            raise RuntimeError("release evidence belongs to another CI run or attempt")
    if payload.get("workspace_snapshot_start_fingerprint") != payload.get(
        "workspace_snapshot_fingerprint"
    ):
        raise RuntimeError("release evidence source changed while quality Gates were running")

    selected = payload.get("selected_gate_ids")
    required = payload.get("required_gate_ids")
    if not isinstance(selected, list) or not selected or selected != required:
        raise RuntimeError("release evidence did not execute the full required gate set")
    results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError("release evidence results are missing")
    statuses = {str(item.get("id")): item.get("status") for item in results if isinstance(item, dict)}
    if any(statuses.get(str(gate_id)) != "PASS" for gate_id in required):
        raise RuntimeError("one or more required release evidence gates are not PASS")
    contracts = payload.get("gate_contract_fingerprints")
    if not isinstance(contracts, dict) or set(contracts) != set(required):
        raise RuntimeError("release evidence Gate contract fingerprints are incomplete")
    if any(
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest.lower())
        for digest in contracts.values()
    ):
        raise RuntimeError("release evidence Gate contract fingerprint is invalid")

    policy = (policy_path or workspace / "governance/quality-loop-policy.json").resolve()
    if not policy.is_file() or payload.get("policy_fingerprint") != sha256_file(policy):
        raise RuntimeError("release evidence policy fingerprint does not match current policy")

    current_snapshot = workspace_snapshot(workspace, ignored_roots=(evidence_dir,))
    if payload.get("workspace_snapshot_fingerprint") != current_snapshot.get("fingerprint"):
        raise RuntimeError("release evidence source snapshot does not match current workspace")
    snapshot_file = evidence_dir / str(payload.get("workspace_snapshot_file") or "")
    if not snapshot_file.is_file():
        raise RuntimeError("release evidence workspace snapshot file is missing")
    snapshot_payload = json.loads(snapshot_file.read_text(encoding="utf-8"))
    if snapshot_payload.get("fingerprint") != payload.get("workspace_snapshot_fingerprint"):
        raise RuntimeError("release evidence summary and snapshot file disagree")
    if snapshot_payload.get("files") != current_snapshot.get("files"):
        raise RuntimeError("release evidence file manifest does not match current workspace")
    verified = dict(payload)
    verified["_release_provenance"] = {
        "evidence_dir": str(evidence_dir),
        "attestation_sha256": sha256_file(evidence_dir / "evidence-attestation.json"),
        "run_summary_sha256": sha256_file(path),
        "workspace_snapshot_sha256": sha256_file(snapshot_file),
    }
    return verified


def create_evidence_bundle(evidence_dir: Path, output_zip: Path) -> dict[str, Any]:
    """Create a deterministic, content-addressed evidence bundle."""
    evidence_dir = evidence_dir.resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    files = [path for path in sorted(evidence_dir.rglob("*")) if path.is_file()]
    if not files:
        raise RuntimeError("release evidence directory is empty")
    with zipfile.ZipFile(
        output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in files:
            relative = path.relative_to(evidence_dir).as_posix()
            info = zipfile.ZipInfo(f"quality-evidence/{relative}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return {
        "path": str(output_zip),
        "filename": output_zip.name,
        "sha256": sha256_file(output_zip),
        "file_count": len(files),
    }


SEMANTIC_CHECKS = (
    (
        "skill-package",
        lambda stage: [
            sys.executable,
            "-B",
            str(stage / "architecture-skill/scripts/verify_skill_package.py"),
            "--skill-dir",
            str(stage / "architecture-skill"),
        ],
    ),
    (
        "version-consistency",
        lambda stage: [
            sys.executable,
            "-B",
            str(stage / "scripts/verify_version_consistency.py"),
            "--workspace-root",
            str(stage),
        ],
    ),
    (
        "architecture-convergence",
        lambda stage: [
            sys.executable,
            "-B",
            str(stage / "scripts/verify_architecture.py"),
            "--workspace-root",
            str(stage),
        ],
    ),
)


def run_release_self_checks(stage: Path) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for check_id, command_factory in SEMANTIC_CHECKS:
        command = command_factory(stage)
        completed = subprocess.run(command, cwd=stage, text=True, capture_output=True, check=False)
        try:
            payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
        except json.JSONDecodeError:
            payload = {}
        result = {
            "status": payload.get("status"),
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        results[check_id] = result
        if completed.returncode != 0 or payload.get("status") != "PASS":
            raise RuntimeError(
                f"clean-release semantic self-check failed: {check_id}\n"
                f"stdout:\n{completed.stdout[-6000:]}\nstderr:\n{completed.stderr[-6000:]}"
            )
    return results


def write_release_metadata(
    stage: Path,
    *,
    workspace: Path,
    frontend_build_mode: str,
    evidence_summary: dict[str, Any] | None,
    certification_level: str = "candidate",
) -> dict[str, Any]:
    release_dir = stage / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    version = (stage / "VERSION").read_text(encoding="utf-8").strip()
    skill_manifest = json.loads((stage / "architecture-skill/manifest.json").read_text(encoding="utf-8"))
    payload = payload_files(stage)
    (release_dir / "FILE_LIST.txt").write_text("\n".join(payload) + "\n", encoding="utf-8")
    evidence = None
    if evidence_summary is not None:
        provenance = dict(evidence_summary.get("_release_provenance") or {})
        evidence_dir = Path(str(provenance.get("evidence_dir") or ""))
        provenance_dir = release_dir / "provenance"
        provenance_dir.mkdir(parents=True, exist_ok=True)
        embedded: dict[str, str] = {}
        for source_name, output_name in (
            ("evidence-attestation.json", "evidence-attestation.json"),
            ("run-summary.json", "run-summary.json"),
            (str(evidence_summary.get("workspace_snapshot_file") or "workspace-snapshot.json"), "workspace-snapshot.json"),
            (str(evidence_summary.get("claim_manifest_evidence_file") or "claim-manifest.json"), "claim-manifest.json"),
        ):
            source = evidence_dir / source_name
            if not source.is_file():
                raise RuntimeError(f"verified evidence provenance file is missing: {source_name}")
            destination = provenance_dir / output_name
            shutil.copy2(source, destination)
            embedded[f"release/provenance/{output_name}"] = sha256_file(destination)
        evidence = {
            "mode": evidence_summary.get("mode"),
            "decision": evidence_summary.get("decision"),
            "loop_status": evidence_summary.get("loop_status"),
            "completion_eligible": evidence_summary.get("completion_eligible"),
            "workspace_snapshot_fingerprint": evidence_summary.get("workspace_snapshot_fingerprint"),
            "summary_sha256": provenance.get("run_summary_sha256"),
            "evidence_bundle_filename": provenance.get("evidence_bundle_filename"),
            "evidence_bundle_sha256": provenance.get("evidence_bundle_sha256"),
            "evidence_attestation_sha256": provenance.get("attestation_sha256"),
            "commit_sha": os.getenv("GITHUB_SHA") or provenance.get("commit_sha") or "local-uncommitted",
            "workflow_run_id": os.getenv("GITHUB_RUN_ID") or provenance.get("workflow_run_id") or "local",
            "workflow_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT") or provenance.get("workflow_run_attempt") or "0",
            "ci_run_identity_fingerprint_sha256": evidence_summary.get("ci_run_identity_fingerprint_sha256"),
            "embedded": embedded,
        }
        if not evidence["evidence_bundle_sha256"]:
            raise RuntimeError("release evidence bundle SHA256 is required")
    manifest = {
        "schema_version": 4,
        "workspace": "customer-agent-workspace",
        "version": version,
        "skill": {
            "name": skill_manifest.get("name"),
            "version": skill_manifest.get("version"),
        },
        "profile": "clean-release",
        "certification_level": certification_level,
        "generated_at": now_iso(),
        "source_snapshot_fingerprint": source_fingerprint(stage),
        "file_count": len(payload),
        "payload_file_count": len(payload),
        "frontend_build": {
            "mode": frontend_build_mode,
            "dist": "services/agent-service/frontend/dist",
        },
        "quality_evidence": evidence,
        "excluded": sorted(FORBIDDEN_PARTS | FORBIDDEN_NAMES | RUNTIME_ARTIFACT_LABELS),
        "integrity": {
            "file_list": "release/FILE_LIST.txt",
            "sha256sums": "release/SHA256SUMS.txt",
            "verification_command": "python -B scripts/verify_release_integrity.py --workspace-root .",
        },
    }
    (release_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    evidence_line = (
        f"- Quality evidence: `{evidence['mode']}` / `{evidence['loop_status']}` / complete PASS."
        if evidence
        else "- Quality evidence: not embedded; this artifact proves build integrity only."
    )
    report = f"""# V{version} / Skill V{skill_manifest.get('version')} Clean Release Validation\n\nProfile: `clean-release`\n\n## Build guarantees\n\n- Sources were copied from an explicit top-level allow-list.\n- `.env`, runtime databases, caches, virtual environments, `node_modules`, prior `dist`, and quality evidence were excluded.\n- Frontend `dist` was rebuilt from current source using `{frontend_build_mode}`.\n- `FILE_LIST.txt` and `SHA256SUMS.txt` were generated from the final staged tree.\n- The staged tree and the final zip are both re-opened and verified.\n{evidence_line}\n\n## Boundary\n\nThis report proves artifact composition and integrity. Production readiness additionally requires a complete protected `release` Quality Loop run, real model smoke, PostgreSQL integration, and deployment-specific secret validation.\n"""
    (release_dir / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")

    hash_targets = [name for name in files_under(stage) if name != "release/SHA256SUMS.txt"]
    sums = [f"{sha256_file(stage / name)}  {name}" for name in hash_targets]
    (release_dir / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    manifest["package_file_count"] = len(files_under(stage))
    (release_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Manifest changed after package_file_count; regenerate sums once.
    hash_targets = [name for name in files_under(stage) if name != "release/SHA256SUMS.txt"]
    sums = [f"{sha256_file(stage / name)}  {name}" for name in hash_targets]
    (release_dir / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return manifest


def verify_release_tree(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    if not root.is_dir():
        return {"status": "FAIL", "errors": ["release_root_missing"]}
    allowed_top_level = TOP_LEVEL_FILES | TOP_LEVEL_DIRS | {"release"}
    for path in root.iterdir():
        if path.name not in allowed_top_level:
            errors.append(f"unexpected_top_level:{path.name}")
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            errors.append(f"symlink_not_allowed:{relative.as_posix()}")
        if path.is_file() and (
            relative.name in FORBIDDEN_NAMES
            or relative.suffix.lower() in FORBIDDEN_SUFFIXES
            or any(part in FORBIDDEN_PARTS for part in relative.parts)
            or is_runtime_artifact_path(relative)
        ):
            errors.append(f"forbidden_file:{relative.as_posix()}")
    release_dir = root / "release"
    for name in ("FILE_LIST.txt", "MANIFEST.json", "SHA256SUMS.txt", "VALIDATION_REPORT.md"):
        if not (release_dir / name).is_file():
            errors.append(f"missing_release_metadata:{name}")
    if errors:
        return {"status": "FAIL", "errors": errors}

    listed = [line.strip() for line in (release_dir / "FILE_LIST.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    actual_payload = payload_files(root)
    if listed != actual_payload:
        errors.append("file_list_does_not_match_payload")

    sum_rows: dict[str, str] = {}
    for line in (release_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            digest, name = line.split("  ", 1)
        except ValueError:
            errors.append(f"invalid_sha256_row:{line}")
            continue
        sum_rows[name] = digest
    expected_hash_files = [name for name in files_under(root) if name != "release/SHA256SUMS.txt"]
    if sorted(sum_rows) != sorted(expected_hash_files):
        errors.append("sha256_file_set_mismatch")
    for name in expected_hash_files:
        if sum_rows.get(name) != sha256_file(root / name):
            errors.append(f"sha256_mismatch:{name}")

    try:
        manifest = json.loads((release_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    except Exception as exc:
        manifest = {}
        errors.append(f"manifest_invalid:{exc.__class__.__name__}")
    if manifest.get("schema_version") != 4:
        errors.append("manifest_schema_version_invalid")
    if manifest.get("profile") != "clean-release":
        errors.append("manifest_profile_invalid")
    if manifest.get("certification_level") not in {"candidate", "protected-release"}:
        errors.append("manifest_certification_level_invalid")
    if manifest.get("file_count") != len(actual_payload):
        errors.append("manifest_file_count_mismatch")
    if manifest.get("payload_file_count") != len(actual_payload):
        errors.append("manifest_payload_file_count_mismatch")
    if manifest.get("package_file_count") != len(files_under(root)):
        errors.append("manifest_package_file_count_mismatch")
    if manifest.get("source_snapshot_fingerprint") != source_fingerprint(root):
        errors.append("manifest_source_snapshot_fingerprint_mismatch")
    evidence = manifest.get("quality_evidence")
    if evidence is not None:
        required_provenance = (
            "workspace_snapshot_fingerprint",
            "evidence_bundle_filename",
            "evidence_bundle_sha256",
            "evidence_attestation_sha256",
            "commit_sha",
            "workflow_run_id",
            "embedded",
        )
        if not isinstance(evidence, dict) or any(not evidence.get(key) for key in required_provenance):
            errors.append("quality_evidence_provenance_incomplete")
        else:
            embedded = evidence.get("embedded")
            if not isinstance(embedded, dict) or not embedded:
                errors.append("quality_evidence_embedded_manifest_missing")
            else:
                for name, digest in embedded.items():
                    path = root / str(name)
                    if not path.is_file() or sha256_file(path) != digest:
                        errors.append(f"quality_evidence_embedded_hash_mismatch:{name}")
            attestation_path = root / "release/provenance/evidence-attestation.json"
            if attestation_path.is_file() and sha256_file(attestation_path) != evidence.get(
                "evidence_attestation_sha256"
            ):
                errors.append("quality_evidence_attestation_hash_mismatch")
    if manifest.get("certification_level") == "protected-release":
        if not isinstance(evidence, dict) or evidence.get("mode") != "release":
            errors.append("protected_release_requires_release_evidence")
        elif evidence.get("commit_sha") in {None, "", "local-uncommitted"}:
            errors.append("protected_release_requires_immutable_commit")
        elif evidence.get("workflow_run_id") in {None, "", "local"}:
            errors.append("protected_release_requires_ci_workflow_identity")
        elif not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("ci_run_identity_fingerprint_sha256") or "")):
            errors.append("protected_release_requires_ci_run_identity_fingerprint")
        if manifest.get("frontend_build", {}).get("mode") != "npm-ci-stage":
            errors.append("protected_release_requires_clean_npm_ci_build")
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if manifest.get("version") != version:
        errors.append("manifest_version_mismatch")
    dist = root / "services/agent-service/frontend/dist"
    if not (dist / "index.html").is_file() or not (dist / "assets").is_dir():
        errors.append("frontend_dist_missing_or_not_rebuilt")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "version": version,
        "payload_file_count": len(actual_payload),
        "package_file_count": len(files_under(root)),
        "source_snapshot_fingerprint": manifest.get("source_snapshot_fingerprint"),
    }


def create_zip(stage: Path, output_zip: Path, *, root_name: str) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in files_under(stage):
            archive.write(stage / name, arcname=f"{root_name}/{name}")


def verify_zip(output_zip: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="release-verify-") as temp:
        temp_root = Path(temp)
        with zipfile.ZipFile(output_zip) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            errors: list[str] = []
            if len(names) != len(set(names)):
                errors.append("duplicate_zip_entry")
            if any(
                not name
                or "\x00" in name
                or "\\" in name
                or name.startswith("/")
                or ".." in Path(name).parts
                for name in names
            ):
                errors.append("unsafe_zip_path")
            if any(
                info.create_system == 3 and stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)
                for info in infos
            ):
                errors.append("zip_symlink_not_allowed")
            if errors:
                return {"status": "FAIL", "errors": errors}
            archive.extractall(temp_root)
        roots = [path for path in temp_root.iterdir() if path.is_dir()]
        if len(roots) != 1:
            return {"status": "FAIL", "errors": ["zip_must_contain_one_root_directory"]}
        return verify_release_tree(roots[0])
