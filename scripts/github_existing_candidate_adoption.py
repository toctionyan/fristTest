#!/usr/bin/env python3
from __future__ import annotations

"""Certify an already-existing Draft PR without granting source write authority.

This controller exists for governed migration/adoption of a candidate that was
created before the repair control plane became authoritative.  It is deliberately
not a repair path: the exact candidate file set and Git blob identities must be
pre-authorized by a trusted profile already present on ``main``.  The controller
never edits candidate source, tests, or baseline metadata.  It can only bind the
existing immutable candidate, run fixed profile verification plus Quick quality,
and emit a governance-pending publication receipt.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from task_run import TaskRunStore, stable_task_id  # type: ignore  # noqa: E402

PROFILE_SCHEMA = "governed-existing-candidate-adoption-profile@1"
PLAN_SCHEMA = "governed-existing-candidate-adoption-plan@1"
AUTHORITY_SCHEMA = "governed-existing-candidate-no-write-authority@1"
VALIDATION_SCHEMA = "governed-existing-candidate-validation@1"
PUBLICATION_SCHEMA = "github-governed-repair-draft-publication@1"
MAX_OUTPUT = 40_000


class AdoptionError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AdoptionError(f"JSON object required: {path}")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run(command: list[str], *, cwd: Path, timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=env,
    )


def _git(workspace: Path, *args: str) -> str:
    completed = _run(["git", *args], cwd=workspace, timeout=180)
    if completed.returncode:
        raise AdoptionError((completed.stderr or completed.stdout or "git failed").strip()[:MAX_OUTPUT])
    return completed.stdout.strip()


def _normalize_path(raw: object) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise AdoptionError(f"invalid repository path: {raw!r}")
    normalized = pure.as_posix()
    if normalized != value:
        raise AdoptionError(f"non-canonical repository path: {raw!r}")
    return normalized


def _validate_profile(profile: dict[str, Any]) -> None:
    if profile.get("schema") != PROFILE_SCHEMA:
        raise AdoptionError("unsupported adoption profile")
    if not str(profile.get("profile_id") or "").strip():
        raise AdoptionError("adoption profile id is missing")
    if profile.get("authority_effect") is not False or profile.get("existing_candidate_only") is not True:
        raise AdoptionError("adoption profile gained source authority")
    expected = profile.get("allowed_changed_files")
    if not isinstance(expected, dict) or not expected:
        raise AdoptionError("adoption profile must bind a non-empty exact file set")
    for raw, sha in expected.items():
        _normalize_path(raw)
        if len(str(sha or "")) != 40:
            raise AdoptionError(f"invalid expected Git blob SHA for {raw}")
    guards = profile.get("required_guard_ids")
    if not isinstance(guards, list) or not guards or len(set(map(str, guards))) != len(guards):
        raise AdoptionError("required_guard_ids must be unique and non-empty")
    commands = profile.get("verification_commands")
    if not isinstance(commands, list) or not commands:
        raise AdoptionError("verification_commands are missing")
    command_ids = {str(row.get("id") or "") for row in commands if isinstance(row, dict)}
    if not set(map(str, guards)).issubset(command_ids):
        raise AdoptionError("each required guard must have a fixed verification command")
    if profile.get("production_closed") is not False:
        raise AdoptionError("adoption profile illegally closes production")


def _pr_fields(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": int(pr.get("number") or 0),
        "is_draft": bool(pr.get("isDraft")),
        "state": str(pr.get("state") or ""),
        "head_branch": str(pr.get("headRefName") or ""),
        "head_sha": str(pr.get("headRefOid") or ""),
        "base_branch": str(pr.get("baseRefName") or ""),
        "url": str(pr.get("url") or ""),
    }


def _inspect_candidate(*, workspace: Path, profile: dict[str, Any], pr: dict[str, Any]) -> dict[str, Any]:
    workspace = workspace.resolve()
    _validate_profile(profile)
    fields = _pr_fields(pr)
    if fields["number"] != int(profile.get("source_pr_number") or 0):
        raise AdoptionError("profile/PR number mismatch")
    if fields["state"] != "OPEN" or fields["is_draft"] is not True:
        raise AdoptionError("existing-candidate adoption requires an open Draft PR")
    if fields["base_branch"] != str(profile.get("base_branch") or ""):
        raise AdoptionError("profile/PR base branch mismatch")
    if not fields["url"].startswith("https://github.com/") or "/pull/" not in fields["url"]:
        raise AdoptionError("invalid Draft PR URL")
    head = _git(workspace, "rev-parse", "HEAD")
    if head != fields["head_sha"] or len(head) != 40:
        raise AdoptionError("candidate checkout does not equal the exact Draft PR head")
    if _git(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
        raise AdoptionError("candidate workspace must start clean")

    base_ref = f"origin/{fields['base_branch']}"
    base_sha = _git(workspace, "rev-parse", base_ref)
    ancestor = _run(["git", "merge-base", "--is-ancestor", base_sha, head], cwd=workspace, timeout=180)
    if ancestor.returncode != 0:
        raise AdoptionError("Draft PR head is not based on the current default-branch head")

    expected_raw = profile["allowed_changed_files"]
    expected = {_normalize_path(path): str(sha) for path, sha in expected_raw.items()}
    actual_paths = [
        _normalize_path(line)
        for line in _git(workspace, "diff", "--name-only", f"{base_sha}..{head}", "--").splitlines()
        if line.strip()
    ]
    if set(actual_paths) != set(expected) or len(actual_paths) != len(expected):
        raise AdoptionError(
            "existing candidate path set does not equal trusted adoption profile: "
            + _canonical({"actual": sorted(actual_paths), "expected": sorted(expected)})
        )

    forbidden_exact = {_normalize_path(item) for item in profile.get("forbidden_changed_exact") or []}
    forbidden_prefixes = tuple(str(item or "").strip().replace("\\", "/") for item in profile.get("forbidden_changed_prefixes") or [])
    for path in actual_paths:
        if path in forbidden_exact or any(path.startswith(prefix) for prefix in forbidden_prefixes if prefix):
            raise AdoptionError(f"adoption profile includes forbidden authority path: {path}")
        blob = _git(workspace, "rev-parse", f"HEAD:{path}")
        if blob != expected[path]:
            raise AdoptionError(f"candidate blob identity drift: {path}")

    tree_sha = _git(workspace, "rev-parse", "HEAD^{tree}")
    profile_sha = _fingerprint(profile)
    authority = {
        "schema": AUTHORITY_SCHEMA,
        "profile_id": profile["profile_id"],
        "profile_sha256": profile_sha,
        "source_pr_number": fields["number"],
        "source_head_sha": head,
        "base_sha": base_sha,
        "validated_tree_sha": tree_sha,
        "exact_changed_files": expected,
        "write_authority_effect": False,
        "source_writes_allowed": False,
        "test_or_oracle_writes_allowed": False,
        "workflow_writes_allowed": False,
        "baseline_writes_allowed": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    authority["authority_sha256"] = _fingerprint(authority)
    return {
        "fields": fields,
        "base_sha": base_sha,
        "head_sha": head,
        "tree_sha": tree_sha,
        "changed_paths": actual_paths,
        "profile_sha256": profile_sha,
        "authority": authority,
    }


def inspect(*, workspace: Path, profile_path: Path, pr_json_path: Path, output_path: Path, authority_path: Path, task_run_path: Path, repository: str) -> dict[str, Any]:
    profile = _load(profile_path)
    pr = _load(pr_json_path)
    evidence = _inspect_candidate(workspace=workspace, profile=profile, pr=pr)
    authority = evidence["authority"]
    _write(authority_path, authority)
    binding = {
        "repository": repository,
        "source_pr_number": evidence["fields"]["number"],
        "head_sha": evidence["head_sha"],
        "profile_id": profile["profile_id"],
        "profile_sha256": evidence["profile_sha256"],
        "authority_sha256": authority["authority_sha256"],
    }
    task = TaskRunStore.open_or_create(
        task_run_path.resolve(),
        task_id=stable_task_id("github-existing-candidate-adoption", binding),
        task_kind="github-governed-existing-candidate-adoption",
        binding=binding,
        required_conditions=(
            "failure_ingested",
            "classification_complete",
            "source_changed",
            "validation_passed",
            "draft_pr_published",
            "governance_closed",
            "baseline_accepted",
            "exact_head_certified",
            "ready_for_review",
        ),
    )
    task.checkpoint(
        status="VALIDATING",
        phase="EXISTING_CANDIDATE_BOUND",
        workspace_fingerprint=evidence["tree_sha"],
        evidence_refs=[str(profile_path), str(authority_path), evidence["fields"]["url"]],
        metadata={
            "governed_repair_state": "INDEPENDENT_REVIEW",
            "candidate_origin": "existing_pr_adoption",
            "write_authority_effect": False,
            "production_closed": False,
        },
    )
    task.mark_condition("failure_ingested", evidence_refs=["adoption:existing-candidate-bound"])
    task.mark_condition("classification_complete", evidence_refs=["classification:existing_candidate_adoption"])
    task.mark_condition("source_changed", evidence_refs=[f"existing-source-sha:{evidence['head_sha']}"])

    plan = {
        "schema": PLAN_SCHEMA,
        "status": "EXISTING_CANDIDATE_BOUND",
        "repository": repository,
        "profile_id": profile["profile_id"],
        "profile_sha256": evidence["profile_sha256"],
        "authority_sha256": authority["authority_sha256"],
        "source_pr_number": evidence["fields"]["number"],
        "draft_pr_url": evidence["fields"]["url"],
        "repair_branch": evidence["fields"]["head_branch"],
        "repair_base_branch": evidence["fields"]["base_branch"],
        "published_source_sha": evidence["head_sha"],
        "base_sha": evidence["base_sha"],
        "validated_tree_sha": evidence["tree_sha"],
        "changed_paths": evidence["changed_paths"],
        "required_guard_ids": list(profile["required_guard_ids"]),
        "candidate_origin": "existing_pr_adoption",
        "write_authority_effect": False,
        "source_writes_allowed": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    plan["plan_sha256"] = _fingerprint(plan)
    _write(output_path, plan)
    return plan


def _expand_command(raw: list[Any], *, workspace: Path) -> list[str]:
    replacements = {
        "{python}": sys.executable,
        "{agent_python}": str(workspace / "services" / "agent-service" / ".venv" / "bin" / "python"),
    }
    return [replacements.get(str(item), str(item)) for item in raw]


def run_profile(*, workspace: Path, profile_path: Path, plan_path: Path, output_path: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    profile = _load(profile_path)
    plan = _load(plan_path)
    if plan.get("schema") != PLAN_SCHEMA or plan.get("status") != "EXISTING_CANDIDATE_BOUND":
        raise AdoptionError("invalid existing-candidate plan")
    if plan.get("profile_sha256") != _fingerprint(profile):
        raise AdoptionError("adoption profile drifted after candidate binding")
    if _git(workspace, "rev-parse", "HEAD") != str(plan.get("published_source_sha") or ""):
        raise AdoptionError("candidate head drifted after binding")
    rows: list[dict[str, Any]] = []
    for raw in profile.get("verification_commands") or []:
        if not isinstance(raw, dict):
            raise AdoptionError("invalid verification command row")
        command_id = str(raw.get("id") or "").strip()
        argv = raw.get("argv")
        cwd_raw = str(raw.get("cwd") or ".")
        if not command_id or not isinstance(argv, list) or not argv:
            raise AdoptionError("invalid verification command")
        cwd = (workspace / cwd_raw).resolve()
        try:
            cwd.relative_to(workspace)
        except ValueError as exc:
            raise AdoptionError("verification cwd escaped candidate workspace") from exc
        completed = _run(_expand_command(argv, workspace=workspace), cwd=cwd, timeout=3600)
        row = {
            "id": command_id,
            "exit_code": completed.returncode,
            "passed": completed.returncode == 0,
            "stdout": completed.stdout[-MAX_OUTPUT:],
            "stderr": completed.stderr[-MAX_OUTPUT:],
        }
        if command_id in {"dependency-basis-contract", "dependency-basis-contract-mutation-proof"} and completed.returncode == 0:
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise AdoptionError(f"{command_id} did not emit JSON") from exc
            if not isinstance(payload, dict) or payload.get("status") != "PASS":
                raise AdoptionError(f"{command_id} did not prove PASS")
            if command_id == "dependency-basis-contract":
                if payload.get("authority_effect") is not False or payload.get("final_dependency_authority") != "deterministic_dependency_proof_reducer":
                    raise AdoptionError("dependency contract attempted to gain final dependency authority")
            else:
                if payload.get("all_mutations_killed") is not True or payload.get("workspace_unchanged") is not True:
                    raise AdoptionError("dependency mutation proof is incomplete")
        rows.append(row)
        if completed.returncode != 0:
            break
    passed = len(rows) == len(profile.get("verification_commands") or []) and all(row["passed"] for row in rows)
    result = {
        "schema": VALIDATION_SCHEMA,
        "status": "PROFILE_VALIDATION_PASSED" if passed else "PROFILE_VALIDATION_FAILED",
        "profile_id": profile["profile_id"],
        "profile_sha256": plan["profile_sha256"],
        "candidate_sha": plan["published_source_sha"],
        "results": rows,
        "write_authority_effect": False,
        "production_closed": False,
    }
    _write(output_path, result)
    if not passed:
        raise AdoptionError("fixed adoption profile validation failed")
    return result


def _validate_quick(summary: dict[str, Any], required_guard_ids: list[str]) -> dict[str, str]:
    if summary.get("mode") != "quick" or summary.get("run_kind") != "verification":
        raise AdoptionError("adoption Quick evidence has the wrong mode/run kind")
    if summary.get("decision") != "PASS" or summary.get("loop_status") != "CI_VERIFIED" or summary.get("completion_eligible") is not True:
        raise AdoptionError("adoption Quick evidence did not reach CI_VERIFIED PASS")
    statuses = {
        str(row.get("id") or ""): str(row.get("status") or "")
        for row in summary.get("results") or []
        if isinstance(row, dict)
    }
    required = {str(item) for item in summary.get("required_gate_ids") or []}
    if "python-test-suites" not in required or statuses.get("python-test-suites") != "PASS":
        raise AdoptionError("anti-drift python-test-suites gate is not required and PASS")
    if "python-test-suites" not in set(required_guard_ids):
        raise AdoptionError("adoption profile omitted the permanent python-test-suites guard")
    return statuses


def finalize(*, workspace: Path, profile_path: Path, plan_path: Path, authority_path: Path, profile_validation_path: Path, quick_summary_path: Path, task_run_path: Path, validation_output_path: Path, publication_output_path: Path, workflow_run_id: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    profile = _load(profile_path)
    plan = _load(plan_path)
    authority = _load(authority_path)
    profile_validation = _load(profile_validation_path)
    if plan.get("plan_sha256") != _fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"}):
        raise AdoptionError("existing-candidate plan fingerprint mismatch")
    if authority.get("schema") != AUTHORITY_SCHEMA or authority.get("write_authority_effect") is not False or authority.get("source_writes_allowed") is not False:
        raise AdoptionError("no-write adoption authority is invalid")
    authority_without_hash = {k: v for k, v in authority.items() if k != "authority_sha256"}
    if authority.get("authority_sha256") != _fingerprint(authority_without_hash):
        raise AdoptionError("no-write adoption authority fingerprint mismatch")
    if authority.get("authority_sha256") != plan.get("authority_sha256"):
        raise AdoptionError("plan/authority binding mismatch")
    if profile_validation.get("schema") != VALIDATION_SCHEMA or profile_validation.get("status") != "PROFILE_VALIDATION_PASSED":
        raise AdoptionError("profile validation is not PASS")
    if profile_validation.get("candidate_sha") != plan.get("published_source_sha"):
        raise AdoptionError("profile validation candidate SHA mismatch")
    if _git(workspace, "rev-parse", "HEAD") != str(plan.get("published_source_sha") or ""):
        raise AdoptionError("candidate head moved before publication")
    if _git(workspace, "rev-parse", "HEAD^{tree}") != str(plan.get("validated_tree_sha") or ""):
        raise AdoptionError("candidate tree moved before publication")
    if _git(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
        raise AdoptionError("candidate workspace is dirty after read-only certification")

    quick = _load(quick_summary_path)
    quick_statuses = _validate_quick(quick, list(profile["required_guard_ids"]))
    command_status = {
        str(row.get("id") or ""): "PASS" if row.get("passed") is True else "FAIL"
        for row in profile_validation.get("results") or []
        if isinstance(row, dict)
    }
    for guard in profile["required_guard_ids"]:
        guard = str(guard)
        if guard == "python-test-suites":
            if quick_statuses.get(guard) != "PASS":
                raise AdoptionError(f"permanent guard not reverified: {guard}")
        elif command_status.get(guard) != "PASS":
            raise AdoptionError(f"permanent guard not reverified: {guard}")

    validation = {
        "schema": VALIDATION_SCHEMA,
        "status": "EXISTING_CANDIDATE_CERTIFIED",
        "candidate_sha": plan["published_source_sha"],
        "validated_tree_sha": plan["validated_tree_sha"],
        "profile_id": profile["profile_id"],
        "profile_sha256": plan["profile_sha256"],
        "authority_sha256": plan["authority_sha256"],
        "required_guard_ids": list(profile["required_guard_ids"]),
        "profile_validation": profile_validation,
        "quick_required_gate_ids": list(quick.get("required_gate_ids") or []),
        "quick_gate_statuses": quick_statuses,
        "write_authority_effect": False,
        "workspace_unchanged": True,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    validation["validation_sha256"] = _fingerprint(validation)
    _write(validation_output_path, validation)

    gates = {
        "G0_SCOPE_AUTHORITY": {"status": "PASS", "evidence": [f"adoption-profile:{profile['profile_id']}", f"authority-sha256:{plan['authority_sha256']}", "source-writes:false"]},
        "G1_CONTRACT_PROJECTION": {"status": "PASS", "evidence": ["guard:dependency-basis-contract"]},
        "G2_SEMANTIC_INVARIANT": {"status": "PASS", "evidence": ["guard:dependency-basis-runtime-regression", f"invariant:{profile['violated_invariant']}"]},
        "G3_MUTATION": {"status": "PASS", "evidence": ["guard:dependency-basis-contract-mutation-proof", "guard:python-test-suites"]},
        "G4_FINAL_AUTHORITY": {"status": "PASS", "evidence": [f"authority-owner:{profile['authority_owner']}", "candidate-write-authority:false"]},
        "G5_INTEGRATION_CERTIFICATION": {"status": "PASS", "evidence": ["quick:CI_VERIFIED", f"validation-sha256:{validation['validation_sha256']}"]},
        "G6_GOVERNANCE_EXACT_HEAD": {"status": "PENDING", "evidence": []},
    }
    authority_digest = str(plan["authority_sha256"])
    publication = {
        "schema": PUBLICATION_SCHEMA,
        "status": "DRAFT_REPAIR_PR_PUBLISHED_AWAITING_GOVERNANCE",
        "governed_repair_state": "GOVERNANCE_REQUIRED",
        "repository": plan["repository"],
        "source_run_id": str(workflow_run_id),
        "draft_pr_url": plan["draft_pr_url"],
        "repair_branch": plan["repair_branch"],
        "repair_base_branch": plan["repair_base_branch"],
        "published_source_sha": plan["published_source_sha"],
        "validated_tree_sha": plan["validated_tree_sha"],
        "changed_paths": list(plan["changed_paths"]),
        "required_guard_ids": list(profile["required_guard_ids"]),
        "violated_invariant": profile["violated_invariant"],
        "authority_owner": profile["authority_owner"],
        "required_permanent_guard": profile["required_permanent_guard"],
        "rca_sha256": plan["profile_sha256"],
        "write_grant_sha256": authority_digest,
        "candidate_origin": "existing_pr_adoption",
        "authority_record_schema": AUTHORITY_SCHEMA,
        "write_authority_effect": False,
        "gates": gates,
        "draft_pr_published": True,
        "governance_closed": False,
        "baseline_accepted": False,
        "exact_head_certified": False,
        "ready_for_review": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    _write(publication_output_path, publication)

    task = TaskRunStore(task_run_path.resolve(), _load(task_run_path))
    task.mark_condition("validation_passed", evidence_refs=[str(validation_output_path), f"validation-sha256:{validation['validation_sha256']}"])
    task.mark_condition("draft_pr_published", evidence_refs=[str(publication_output_path), str(plan["draft_pr_url"])])
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="STAGE4_GOVERNANCE_REQUIRED",
        workspace_fingerprint=str(plan["validated_tree_sha"]),
        evidence_refs=[str(publication_output_path), str(validation_output_path), str(authority_path)],
        metadata={
            "governed_repair_state": "GOVERNANCE_REQUIRED",
            "candidate_origin": "existing_pr_adoption",
            "write_authority_effect": False,
            "gates": gates,
            "governance_closed": False,
            "baseline_accepted": False,
            "exact_head_certified": False,
            "ready_for_review": False,
            "production_closed": False,
        },
    )
    return publication


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("--workspace", required=True)
    inspect_parser.add_argument("--profile", required=True)
    inspect_parser.add_argument("--pr-json", required=True)
    inspect_parser.add_argument("--repository", required=True)
    inspect_parser.add_argument("--output", required=True)
    inspect_parser.add_argument("--authority", required=True)
    inspect_parser.add_argument("--task-run", required=True)

    run_parser = sub.add_parser("run-profile")
    run_parser.add_argument("--workspace", required=True)
    run_parser.add_argument("--profile", required=True)
    run_parser.add_argument("--plan", required=True)
    run_parser.add_argument("--output", required=True)

    final_parser = sub.add_parser("finalize")
    final_parser.add_argument("--workspace", required=True)
    final_parser.add_argument("--profile", required=True)
    final_parser.add_argument("--plan", required=True)
    final_parser.add_argument("--authority", required=True)
    final_parser.add_argument("--profile-validation", required=True)
    final_parser.add_argument("--quick-summary", required=True)
    final_parser.add_argument("--task-run", required=True)
    final_parser.add_argument("--validation-output", required=True)
    final_parser.add_argument("--publication-output", required=True)
    final_parser.add_argument("--workflow-run-id", required=True)

    args = parser.parse_args()
    try:
        if args.command == "inspect":
            inspect(
                workspace=Path(args.workspace),
                profile_path=Path(args.profile),
                pr_json_path=Path(args.pr_json),
                output_path=Path(args.output),
                authority_path=Path(args.authority),
                task_run_path=Path(args.task_run),
                repository=args.repository,
            )
        elif args.command == "run-profile":
            run_profile(
                workspace=Path(args.workspace),
                profile_path=Path(args.profile),
                plan_path=Path(args.plan),
                output_path=Path(args.output),
            )
        else:
            finalize(
                workspace=Path(args.workspace),
                profile_path=Path(args.profile),
                plan_path=Path(args.plan),
                authority_path=Path(args.authority),
                profile_validation_path=Path(args.profile_validation),
                quick_summary_path=Path(args.quick_summary),
                task_run_path=Path(args.task_run),
                validation_output_path=Path(args.validation_output),
                publication_output_path=Path(args.publication_output),
                workflow_run_id=args.workflow_run_id,
            )
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, AdoptionError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc), "write_authority_effect": False, "merge_allowed": False, "deploy_allowed": False, "production_closed": False}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
