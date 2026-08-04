# B34 Resilient Task Harness Validation Report

## Scope

B34 is based on B33 Stage 5 and changes only the governed repair/control plane. It fixes premature termination and interruption recovery without changing customer-Agent semantics, Business Service authority, transaction authority, Stage 1-5 capability behavior, or production data schemas.

## Root cause closed

The prior repair orchestrator had no durable task-run completion contract. A process, connector, conversation, or host interruption could occur after a Fixer or Judge produced evidence but before the next in-memory step was recorded. Re-entry could repeat the same action, lose the continuation point, or let a caller report completion before every required gate had evidence.

## Implemented controls

1. Added `TaskRunStore` with immutable task binding, atomic JSON replacement, revision conflict detection, and durable checkpoints.
2. Added an explicit state machine and Completion Guard. `COMPLETED` is valid only when every required condition and the final checkpoint contain evidence.
3. Added action/state fingerprints, bounded per-strategy retry budgets, deterministic fallback selection, and machine-readable blocker records.
4. Integrated checkpoints around Fixer execution, targeted validation, full validation, and issue closure in `repair_loop.py`.
5. Reconciled the sole permitted crash window, `FIXER_RUNNING`, from the frozen baseline. Out-of-scope drift blocks the run; the Fixer is never blindly replayed.
6. Reused compatible durable Judge summaries without consuming another Judge execution budget.
7. Added `task_run_cli.py inspect|guard`; `guard` returns success only for evidence-complete `COMPLETED` runs.
8. Changed the B34 verifier from one long, buffered process into a resumable multi-check TaskRun. Every check writes a result immediately, emits live start/end status, and resumes by skipping durable PASS results.

## Counterexamples closed

- Completion requested while one required gate is missing: rejected with `PrematureCompletionError`.
- Process killed after Fixer mutation but before `FIXER_APPLIED`: recovery skips the Fixer and continues validation.
- Interrupted Fixer changes an out-of-scope path: run becomes `BLOCKED` and does not replay the Fixer.
- No-progress Fixer: two attempts persist across restarts; the next attempt is blocked rather than looped forever.
- Persisted JSON manually changed to `COMPLETED` without evidence: loading fails closed.
- Existing compatible Judge summary after interruption: reused without executing the Judge again.
- Concurrent stale writer: revision conflict rejected.
- Verification interrupted between checks: the next invocation skips already-passed checks and continues from the first pending condition.

## Final validation

The final verification TaskRun is `verify-b34-resilient-harness-4685ad6ef492412fb3a8`, bound to source fingerprint `295dc5842c0ff230cb32e949579df791c8ba11d22c5830124a15c5ec9b639d08`.

- TaskRun control plane: **20 passed**.
- Complete Skill control plane: **148 passed**.
- Repair Loop interruption/governance contracts: **8 passed**, 28 unrelated tests deselected.
- Resumable verifier self-test: **1 passed**.
- Stage 1-5 core runtime impact regression: **96 passed**.
- Broad context, transaction, and governance impact regression: **607 passed**, 4 protected real-model tests explicitly deselected.
- Architecture Gate, Module Vertical Closure, Version Consistency, and Architecture Convergence: **PASS**.
- Completion Guard: `status=COMPLETED`, `completion_eligible=true`, no missing or invalid conditions, 9/9 checks passed.

## Actual interruption evidence

During validation, an outer execution window terminated a run after two checks had already persisted PASS. The next invocation read the same TaskRun, printed `SKIP ... (durable PASS)` for both checks, and continued with the pending third check. It did not restart the verification from zero. The old recovery TaskRun, final report, and resume log are preserved under the B34 governance evidence directory.

## Immutable ledger evidence closure

The final source also moves `ISSUE-ENV-001` away from disposable `.quality` output and moves `ISSUE-REL-001` away from a mutable architecture-test source. Both historical obligations now resolve through immutable JSON records under `governance/repair-cases/`, while the live tests and release workflow remain the executable authorities. `scripts/verify_task_ledger.py` passes with `WP-08` and `WP-09` still explicitly open or blocked.

## Environment boundary

The local container uses Python 3.13.5 while the lock requires Python 3.12.13 and does not provide `langchain_core` or `langgraph`. Real DeepSeek/OpenAI, managed PostgreSQL/pgvector, dual-service browser, restart/concurrency, and host certification were not run locally and are not counted as PASS.

## Status

`GOVERNED_B34_RESILIENT_TASK_HARNESS_CANDIDATE`

The B34 repair-control work package is locally closed and regression verified. Production certification remains open, so `production_closed=false`.

## Terminal Change Permit authority fix

GitHub publication exposed two fail-closed governance defects: `project_compatibility.py` selected `governance/active-change.json` even when status was `closed` or `rejected`, and runtime `.pytest_cache` files were classified as product source. The controller now treats only `approved`, `implementing`, `review`, and `verified` as temporary Permit-authority states; terminal states fall back to the promoted historical baseline, and `.pytest_cache` is excluded as runtime tooling state.

Validation: **5 focused tests passed**; project compatibility returned **PASS**, authority `historical-registry-baseline`, protected/baseline counts **546/546**. Complete Skill control plane: **148 passed**. Immutable evidence: `governance/repair-cases/repair-v20.17-b34-resilient-task-harness/evidence/terminal-change-permit-authority.json`.
