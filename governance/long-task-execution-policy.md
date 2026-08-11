# Long-Task Execution Policy

Status: repository-wide execution invariant for ChatGPT, Codex, Claude Code, CI-facing agents, and human-supervised agent runs.

This policy governs long-running engineering work, multi-milestone work, external CI/provider waits, resumable Quality runs, and any task where stopping at an arbitrary clock boundary could leave state inconsistent. It does not weaken the repository's existing Change Contract, Quality, ReleaseRun, production, or authorization boundaries.

## Core rule

**Time budget follows correctness boundaries; split orchestration, not atomic correctness units.**

A Goal may run for many milestones. A Milestone is a correctness-bounded unit with explicit acceptance evidence. An Atomic Work Unit is any operation that would become semantically invalid, misleading, or unrecoverable if interrupted in the middle.

Examples of Atomic Work Units include:

- an in-progress state transition or transaction;
- an indivisible merge or governed release transition;
- a code/schema change that is inconsistent without its required companion changes;
- an already-running complete test command or migration-style operation;
- a coordinator ledger transition;
- any operation whose interruption would leave the repository or durable state semantically invalid.

Do not cancel, split, or truncate an Atomic Work Unit merely to satisfy a reporting clock.

## Observation and reporting cadence

For long tasks, target a user-visible progress checkpoint every **10-15 minutes** when a meaningful checkpoint is available. This is an observation/reporting target, not a maximum engineering work-unit duration.

Use **18-20 minutes** only as a planning threshold for orchestration that can be split safely. If a block is expected to exceed that range and has a real correctness-preserving boundary, split it before starting. If it is indivisible, finish the atomic unit safely and report at the nearest Safe Checkpoint.

Short 3-5 minute steps that belong to one coherent engineering outcome should normally be bundled into one Milestone instead of forcing a user-visible stop after each small action.

## Safe Checkpoint

A **Safe Checkpoint** is a point where all of the following are true:

1. repository and durable state are internally consistent;
2. the completed work is recoverable from durable evidence;
3. stopping does not change the semantics of an in-progress operation;
4. the next agent can resume from exact identities rather than reconstructed memory;
5. any required Gate already reached in this Milestone has a known terminal or explicitly waiting state.

Only use a Safe Checkpoint as a normal user-visible stopping boundary. Do not manufacture a checkpoint by weakening validation, cancelling an atomic test, or leaving partial state behind.

## External work and WAITING_EXTERNAL

When GitHub Actions, a model/provider request, deployment system, or another external process is the active dependency:

1. launch the complete external operation once;
2. capture its durable identity before yielding;
3. verify the latest observable liveness or step state;
4. report `RUNNING_WAITING_EXTERNAL` when the external dependency is alive but no productive local write can proceed;
5. stop the conversational turn at a Safe Checkpoint instead of keeping the UI busy only to wait;
6. on resume, re-read the exact external run rather than launching a duplicate.

Never cancel or restart an otherwise healthy external run merely because the 10-15 minute observation target was reached.

An external-wait record should carry enough evidence to identify the dependency, for example workflow run/job/step, provider request ID, expiry or heartbeat, candidate SHA, or equivalent durable reference.

## Durable resume identity

Every resumable Milestone must leave a **durable resume identity**. Use the strongest available identities for the task, such as:

- exact repository SHA and branch;
- PR number plus exact head SHA;
- workflow run ID, job ID, current step, and conclusion/liveness;
- Quality run/evidence identity and target fingerprint;
- ReleaseRun issue number, release_run_id, attempt, candidate SHA, and workflow run ID;
- TaskRun/ledger ID and the last accepted state transition.

Do not resume from statements like "the previous test", "that PR", or "it looked almost done" when a durable identity exists.

Before a write after any interruption, re-read the relevant durable authority and verify that its identity and state still match the intended continuation.

## Gate and validation behavior

**Failed gates block progression.** A timeout, failure, stale hash, missing evidence, environment block, or nonterminal required Gate must not be converted into PASS because a reporting interval expired.

After each Milestone, run the smallest validation that actually proves that Milestone's acceptance criteria. Broader validation remains required when the governing contract requires it.

Do not:

- reduce test scope only to make the timebox fit;
- skip a required Gate;
- rerun already-completed expensive validation without a concrete reason;
- claim convergence from unrelated green checks;
- treat a PR-head PASS as proof for a different main SHA;
- hand-edit machine ledgers that have repository-owned transition authority.

**Never claim completion without terminal validation evidence.**

## Execution state vocabulary

Use these states consistently when reporting long-running work:

- `RUNNING_ACTIVE`: the current atomic unit is making observable progress;
- `RUNNING_WAITING_EXTERNAL`: a durable external dependency is active and local productive work is blocked on it;
- `SUSPECTED_STALL`: expected progress/liveness evidence has not advanced for the configured interval and further inspection is required;
- `COMPLETED`: required work and validation reached terminal success evidence;
- `FAILED`: a required operation or Gate reached terminal failure evidence.

The following are platform/UI classifications, not repository execution states:

- `SAFETY_CHECK_WAIT`: the host explicitly reports an additional platform safety check;
- `MODEL_LIMIT`: the host explicitly reports a model/usage allowance limit.

Do not infer `SAFETY_CHECK_WAIT` or `MODEL_LIMIT` from a slow GitHub job, and do not infer a repository stall solely from a ChatGPT UI spinner. Repository liveness must come from repository/external-run evidence.

## Goal and Milestone lifecycle

Use this lifecycle for complex work:

`GOAL -> PLAN -> MILESTONE -> ATOMIC WORK UNIT -> VALIDATION -> SAFE CHECKPOINT -> next MILESTONE -> FINAL ACCEPTANCE`

A Goal must define an outcome, constraints, verification evidence, and a stopping condition. Milestones must be chosen by engineering correctness boundaries rather than by a fixed number of tool calls or an arbitrary number of minutes.

When a Gate fails, remain in the current Milestone until the failure is classified and the governing repair path says it is safe to proceed. Do not cross a failed Milestone boundary.

## Release and production boundaries

Long-task orchestration never expands release authority. In particular:

- attempt budgets remain authoritative;
- product and production closure remain separate authorities;
- `production_closed=true` requires its existing governed authority;
- a reporting checkpoint never authorizes another release attempt;
- recovery/reconciliation must use repository-owned coordinator/recovery paths rather than direct ledger edits.

The fact that work can resume safely does not itself authorize the next state transition.
