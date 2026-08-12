#!/usr/bin/env python3
"""Durable outer controller for governed Repair -> Verify feedback loops.

The controller owns repair-round accounting across Stage 2 and Stage 3.  A
Stage-2 model/fixer cycle is not a repair round, and a GitHub workflow rerun is
not a repair round.  One repair round means one source candidate was produced
and independently validated.  Independent validation failures are typed before
routing so harness/environment retries do not consume product repair budget and
protected-oracle disagreements cannot be "fixed" by mutating the judge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from task_run import TaskRunStore  # type: ignore  # noqa: E402

LOOP_SCHEMA = "github-governed-repair-loop@1"
FEEDBACK_SCHEMA = "github-governed-repair-feedback@1"
STAGE2_SCHEMA = "github-governed-repair-stage2@1"
STAGE3_SCHEMA = "github-governed-repair-stage3@1"
FAILURE_SCHEMA = "github-failure-ingest@1"
MAX_REPAIR_ROUNDS = 8
STAGNATION_LIMIT = 2
MAX_TEXT = 80_000

_SOURCE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])((?:services/agent-service/(?:src|app)/|"
    r"services/business-service/business_service/|contracts/|web/)[A-Za-z0-9_./@+\-]+"
    r"\.(?:py|js|jsx|ts|tsx|mjs|cjs|json|ya?ml|toml|md|sh))(?![A-Za-z0-9_.-])"
)
_TEST_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])((?:tests/|services/[^\s:]+/tests/|web/[^\s:]*tests?/)[A-Za-z0-9_./@+\-]+)"
)
HARNESS_TERMS = (
    "modulenotfounderror: no module named 'agent_core'",
    "app_profile is required",
    "command not found",
    "no such file or directory",
    "targeted python runtime directory is required",
    "candidate checkout drifted",
    "validation checkout drifted",
    "playwright executable doesn't exist",
    "failed to download browser",
)
ENVIRONMENT_TERMS = (
    "could not resolve host",
    "temporary failure in name resolution",
    "connection refused",
    "service unavailable",
    "rate limit exceeded",
    "authentication failed",
    "invalid api key",
    "incorrect api key",
    "no space left on device",
    "runner lost communication",
)
ASSERTION_TERMS = (
    "assertionerror",
    "assert ",
    "differing items:",
    "full diff:",
)


class RepairLoopError(RuntimeError):
    """Fail-closed outer-loop routing error."""


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RepairLoopError(f"JSON object required: {path}")
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _int(value: object, default: int = 0) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


def _normalize_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return ""
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return ""
    return pure.as_posix()


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _task(path: Path) -> TaskRunStore:
    return TaskRunStore(path.resolve(), _load(path))


def _combined_targeted_text(targeted: dict[str, Any]) -> str:
    chunks: list[str] = []
    for row in targeted.get("results") or []:
        if not isinstance(row, dict):
            continue
        for key in ("stdout", "stderr"):
            text = str(row.get(key) or "")
            if text:
                chunks.append(text)
    return "\n".join(chunks)[-MAX_TEXT:]


def _failed_components(targeted: dict[str, Any]) -> list[str]:
    return _unique(
        str(row.get("component") or "unknown")
        for row in targeted.get("results") or []
        if isinstance(row, dict) and row.get("passed") is not True
    )


def _extract_source_paths(text: str, allowed: set[str]) -> list[str]:
    found = [_normalize_path(match) for match in _SOURCE_PATH_RE.findall(text)]
    return [path for path in _unique(found) if path in allowed]


def _failure_fingerprint(
    *,
    failure_class: str,
    repair_paths: list[str],
    targeted: dict[str, Any],
) -> str:
    rows: list[dict[str, Any]] = []
    for row in targeted.get("results") or []:
        if not isinstance(row, dict) or row.get("passed") is True:
            continue
        text = (str(row.get("stdout") or "") + "\n" + str(row.get("stderr") or ""))[-12_000:]
        # Fingerprint evidence, but do not persist protected-oracle text in loop state.
        rows.append(
            {
                "component": str(row.get("component") or "unknown"),
                "exit_code": row.get("exit_code"),
                "timed_out": row.get("timed_out") is True,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    payload = {
        "failure_class": failure_class,
        "repair_paths": repair_paths,
        "rows": rows,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def classify_targeted_failure(
    targeted: dict[str, Any],
    *,
    original_failure: dict[str, Any],
) -> tuple[str, list[str], str]:
    if targeted.get("schema") != STAGE3_SCHEMA:
        raise RepairLoopError("unsupported Stage-3 targeted result schema")
    if targeted.get("status") == "TARGETED_VALIDATION_PASSED":
        return "PASS", [], "targeted validation passed"
    if targeted.get("status") != "TARGETED_VALIDATION_FAILED":
        return "HARNESS_FAILURE", [], "Stage-3 did not produce a complete targeted verdict"

    rows = [row for row in targeted.get("results") or [] if isinstance(row, dict)]
    if any(row.get("timed_out") is True for row in rows):
        return "TRANSIENT_INFRA_FAILURE", [], "targeted validation timed out"

    text = _combined_targeted_text(targeted)
    low = text.casefold()
    if any(term in low for term in ENVIRONMENT_TERMS):
        return "ENVIRONMENT_FAILURE", [], "targeted validation hit an external environment failure"
    if any(term in low for term in HARNESS_TERMS):
        return "HARNESS_FAILURE", [], "targeted validation harness/runtime contract failed"

    allowed = {
        path
        for path in (_normalize_path(item) for item in original_failure.get("candidate_paths") or [])
        if path
    }
    source_paths = _extract_source_paths(text, allowed)
    if source_paths:
        return (
            "PRODUCT_SOURCE_FAILURE",
            source_paths,
            "independent validation implicated governed writable product source",
        )

    has_test_evidence = bool(_TEST_PATH_RE.search(text))
    has_assertion = any(term in low for term in ASSERTION_TERMS)
    if has_test_evidence and has_assertion:
        return (
            "TEST_CONTRACT_REVIEW_REQUIRED",
            [],
            "independent validation found an oracle/semantic assertion mismatch without a governed source stack path",
        )
    return "UNKNOWN_FAILURE", [], "independent validation failed without a safe repair-path classification"


def _validate_bindings(
    *,
    task: TaskRunStore,
    stage2: dict[str, Any],
    plan: dict[str, Any],
    original_failure: dict[str, Any],
) -> dict[str, Any]:
    if stage2.get("schema") != STAGE2_SCHEMA or stage2.get("status") != "REPAIR_CANDIDATE_READY":
        raise RepairLoopError("Stage-2 result is not a repair candidate")
    if plan.get("schema") != STAGE3_SCHEMA or plan.get("status") != "CANDIDATE_PREPARED":
        raise RepairLoopError("Stage-3 plan is not a prepared candidate")
    if original_failure.get("schema") != FAILURE_SCHEMA or original_failure.get("status") != "INGESTED":
        raise RepairLoopError("original failure-case evidence is invalid")
    binding = task.payload.get("binding") if isinstance(task.payload.get("binding"), dict) else {}
    expected = {
        "repository": stage2.get("repository"),
        "workflow_run_id": str(stage2.get("workflow_run_id")),
        "head_sha": stage2.get("head_sha"),
        "failure_signature": stage2.get("failure_signature"),
    }
    for key, value in expected.items():
        if not value or str(binding.get(key)) != str(value):
            raise RepairLoopError(f"TaskRun/Stage-2 binding mismatch: {key}")
        if str(original_failure.get(key)) != str(value):
            raise RepairLoopError(f"original failure/Stage-2 binding mismatch: {key}")
    if str(plan.get("source_run_id")) != str(stage2.get("workflow_run_id")):
        raise RepairLoopError("Stage-3 plan source run does not match Stage-2")
    if str(plan.get("head_sha")) != str(stage2.get("head_sha")):
        raise RepairLoopError("Stage-3 plan head does not match Stage-2")
    if str(plan.get("patch_sha256")) != str(stage2.get("patch_sha256")):
        raise RepairLoopError("Stage-3 plan patch digest does not match Stage-2")
    return binding


def _existing_loop_metadata(task: TaskRunStore) -> dict[str, Any]:
    metadata = task.payload.get("metadata") if isinstance(task.payload.get("metadata"), dict) else {}
    loop = metadata.get("repair_loop") if isinstance(metadata.get("repair_loop"), dict) else {}
    return dict(loop)


def _safe_feedback_failure(
    original: dict[str, Any],
    *,
    repair_paths: list[str],
    repair_round: int,
    verification_attempt: int,
    failure_fingerprint: str,
) -> dict[str, Any]:
    feedback = dict(original)
    feedback["classification"] = "code_or_contract"
    feedback["repair_allowed"] = True
    feedback["candidate_paths"] = list(repair_paths)
    feedback["failed_gates"] = [
        {
            "gate_id": "governed-stage3-targeted",
            "status": "FAIL",
            "category": "independent-validation",
            "owner": "governed repair outer controller",
            "failure_kind": "product_source",
            "summary": "independent validation implicated governed writable source; protected oracle contents are intentionally withheld from the repair actor",
        }
    ]
    feedback["failure_summary"] = (
        "Independent Stage-3 validation failed after repair round "
        f"{repair_round}. Re-diagnose only the governed writable source paths listed in candidate_paths. "
        "The current protected test/oracle contents are not repair input and must not be modified. "
        f"Verification attempt={verification_attempt}; failure_fingerprint={failure_fingerprint}."
    )
    feedback["loop_feedback"] = {
        "schema": FEEDBACK_SCHEMA,
        "repair_round": repair_round,
        "next_repair_round": repair_round + 1,
        "verification_attempt": verification_attempt,
        "failure_class": "PRODUCT_SOURCE_FAILURE",
        "failure_fingerprint": failure_fingerprint,
        "scope_expanded": False,
    }
    return feedback


def route_failure(
    *,
    task_run_path: Path,
    stage2_result_path: Path,
    stage3_plan_path: Path,
    targeted_result_path: Path,
    original_failure_path: Path,
    seed_patch_path: Path,
    output_dir: Path,
    stage3_run_id: str,
    stage3_run_attempt: int,
    previous_state_path: Path | None = None,
) -> dict[str, Any]:
    task = _task(task_run_path)
    stage2 = _load(stage2_result_path)
    plan = _load(stage3_plan_path)
    targeted = _load(targeted_result_path)
    original_failure = _load(original_failure_path)
    binding = _validate_bindings(
        task=task,
        stage2=stage2,
        plan=plan,
        original_failure=original_failure,
    )
    previous: dict[str, Any] = {}
    if previous_state_path and previous_state_path.is_file():
        previous = _load(previous_state_path)
        if previous.get("schema") != LOOP_SCHEMA:
            raise RepairLoopError("previous outer-loop state schema is invalid")
        if str(previous.get("source_run_id")) != str(binding.get("workflow_run_id")):
            raise RepairLoopError("previous outer-loop state belongs to another source run")

    event_key = f"{stage3_run_id}/{stage3_run_attempt}"
    if previous.get("last_verification_event") == event_key:
        duplicate = dict(previous)
        duplicate["action"] = "NOOP_DUPLICATE"
        duplicate["duplicate_event"] = event_key
        _write(output_dir / "loop-state.json", duplicate)
        shutil.copyfile(task_run_path, output_dir / "task-run.json")
        return duplicate

    loop_meta = _existing_loop_metadata(task)
    repair_round = max(
        1,
        _int(previous.get("repair_round"), 0),
        _int(loop_meta.get("repair_round"), 0),
        _int(stage2.get("repair_round"), 0),
    )
    max_rounds = max(
        1,
        min(
            MAX_REPAIR_ROUNDS,
            _int(previous.get("max_repair_rounds"), MAX_REPAIR_ROUNDS)
            or MAX_REPAIR_ROUNDS,
        ),
    )
    prior_verifications = max(
        _int(previous.get("verification_attempt"), 0),
        _int(loop_meta.get("verification_attempt"), 0),
    )
    verification_attempt = max(prior_verifications + 1, stage3_run_attempt)

    failure_class, repair_paths, classification_reason = classify_targeted_failure(
        targeted,
        original_failure=original_failure,
    )
    failure_fp = _failure_fingerprint(
        failure_class=failure_class,
        repair_paths=repair_paths,
        targeted=targeted,
    )
    stagnant_rounds = _int(previous.get("stagnant_rounds"), 0)
    if (
        failure_class == "PRODUCT_SOURCE_FAILURE"
        and previous.get("failure_class") == failure_class
        and previous.get("failure_fingerprint") == failure_fp
        and _int(previous.get("repair_round"), 0) < repair_round
    ):
        stagnant_rounds += 1
    elif failure_class == "PRODUCT_SOURCE_FAILURE":
        stagnant_rounds = 0

    action = "STOP_UNKNOWN_FAILURE"
    next_repair_round: int | None = None
    stop_reason: str | None = None
    status = "BLOCKED"

    if failure_class == "PASS":
        action = "CONTINUE_STAGE3"
        status = "VALIDATING"
    elif failure_class == "PRODUCT_SOURCE_FAILURE":
        if repair_round >= max_rounds:
            action = "STOP_MAX_REPAIR_ROUNDS"
            stop_reason = "max governed product repair rounds reached"
        elif stagnant_rounds >= STAGNATION_LIMIT:
            action = "ARCHITECTURE_REPLAN_REQUIRED"
            stop_reason = "two product repair rounds repeated the same independent validation failure"
        elif not repair_paths:
            action = "TEST_CONTRACT_REVIEW_REQUIRED"
            stop_reason = "no governed writable source path can be derived without expanding authority"
        else:
            action = "DISPATCH_REPAIR"
            next_repair_round = repair_round + 1
            status = "FAILED_RECOVERABLE"
    elif failure_class == "TEST_CONTRACT_REVIEW_REQUIRED":
        action = "TEST_CONTRACT_REVIEW_REQUIRED"
        stop_reason = classification_reason
    elif failure_class in {"HARNESS_FAILURE", "ENVIRONMENT_FAILURE", "TRANSIENT_INFRA_FAILURE"}:
        action = "RETRY_VALIDATION_SAME_CANDIDATE"
        status = "FAILED_RECOVERABLE"
    else:
        action = "STOP_UNKNOWN_FAILURE"
        stop_reason = classification_reason

    state = {
        "schema": LOOP_SCHEMA,
        "source_run_id": str(binding.get("workflow_run_id")),
        "source_run_attempt": str(binding.get("workflow_run_attempt")),
        "source_head_sha": str(binding.get("head_sha")),
        "failure_signature": str(binding.get("failure_signature")),
        "repair_round": repair_round,
        "max_repair_rounds": max_rounds,
        "next_repair_round": next_repair_round,
        "verification_attempt": verification_attempt,
        "workflow_run_attempt_observed": stage3_run_attempt,
        "last_verification_event": event_key,
        "candidate_sha": str(plan.get("candidate_sha") or ""),
        "patch_sha256": str(stage2.get("patch_sha256") or ""),
        "failure_class": failure_class,
        "failure_fingerprint": failure_fp,
        "classification_reason": classification_reason,
        "repair_paths": repair_paths,
        "failed_components": _failed_components(targeted),
        "stagnant_rounds": stagnant_rounds,
        "action": action,
        "stop_reason": stop_reason,
        "repair_budget_consumed": repair_round,
        "repair_budget_remaining": max(0, max_rounds - repair_round),
        "production_closed": False,
    }

    task.set_metadata(repair_loop=state)
    evidence_refs = [str(stage3_plan_path), str(targeted_result_path), f"loop-state:{failure_fp}"]
    if action == "DISPATCH_REPAIR":
        task.checkpoint(
            status="FAILED_RECOVERABLE",
            phase="PRODUCT_SOURCE_FAILURE",
            workspace_fingerprint=str(plan.get("validated_tree_sha") or ""),
            evidence_refs=evidence_refs,
            metadata={"repair_loop": state},
        )
    elif action == "RETRY_VALIDATION_SAME_CANDIDATE":
        task.checkpoint(
            status="FAILED_RECOVERABLE",
            phase=failure_class,
            workspace_fingerprint=str(plan.get("validated_tree_sha") or ""),
            evidence_refs=evidence_refs,
            metadata={"repair_loop": state},
        )
    elif action == "CONTINUE_STAGE3":
        task.checkpoint(
            status="VALIDATING",
            phase="TARGETED_VALIDATION_PASSED",
            workspace_fingerprint=str(plan.get("validated_tree_sha") or ""),
            evidence_refs=evidence_refs,
            metadata={"repair_loop": state},
        )
    else:
        code = action
        task.block(
            code=code,
            reason=stop_reason or classification_reason,
            attempted_strategies=("independent-validation", "typed-failure-router"),
            next_action=(
                "review the protected contract/oracle before authorizing another product repair"
                if action == "TEST_CONTRACT_REVIEW_REQUIRED"
                else "inspect outer-loop evidence and explicitly replan before another repair"
            ),
            workspace_fingerprint=str(plan.get("validated_tree_sha") or ""),
            evidence_refs=evidence_refs,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write(output_dir / "loop-state.json", state)
    shutil.copyfile(task_run_path, output_dir / "task-run.json")
    if action == "DISPATCH_REPAIR":
        if not seed_patch_path.is_file() or seed_patch_path.is_symlink():
            raise RepairLoopError("seed repair patch is missing")
        feedback = _safe_feedback_failure(
            original_failure,
            repair_paths=repair_paths,
            repair_round=repair_round,
            verification_attempt=verification_attempt,
            failure_fingerprint=failure_fp,
        )
        _write(output_dir / "failure-case.json", feedback)
        shutil.copyfile(seed_patch_path, output_dir / "seed.patch")
    return state


def _github_output(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    values = {
        "action": state.get("action") or "",
        "source_run_id": state.get("source_run_id") or "",
        "source_run_attempt": state.get("source_run_attempt") or "",
        "repair_round": state.get("repair_round") or 0,
        "next_repair_round": state.get("next_repair_round") or "",
        "verification_attempt": state.get("verification_attempt") or 0,
        "failure_class": state.get("failure_class") or "",
        "repair_budget_remaining": state.get("repair_budget_remaining") or 0,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = str(value)
            if "\n" in text:
                raise RepairLoopError(f"multiline GitHub output is not allowed: {key}")
            handle.write(f"{key}={text}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--stage2-result", required=True)
    parser.add_argument("--stage3-plan", required=True)
    parser.add_argument("--targeted-result", required=True)
    parser.add_argument("--original-failure-case", required=True)
    parser.add_argument("--seed-patch", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--stage3-run-id", required=True)
    parser.add_argument("--stage3-run-attempt", required=True, type=int)
    parser.add_argument("--previous-state")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    try:
        state = route_failure(
            task_run_path=Path(args.task_run),
            stage2_result_path=Path(args.stage2_result),
            stage3_plan_path=Path(args.stage3_plan),
            targeted_result_path=Path(args.targeted_result),
            original_failure_path=Path(args.original_failure_case),
            seed_patch_path=Path(args.seed_patch),
            output_dir=Path(args.output_dir),
            stage3_run_id=str(args.stage3_run_id),
            stage3_run_attempt=int(args.stage3_run_attempt),
            previous_state_path=Path(args.previous_state) if args.previous_state else None,
        )
        _github_output(Path(args.github_output) if args.github_output else None, state)
        return 0
    except (OSError, json.JSONDecodeError, RepairLoopError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
