# Task Execution Transparency and Recovery Contract

Status: architecture contract for bounded engineering task execution.

## Goal

A long-running task should minimize routine user interruptions while remaining fully transparent when queried. A failed attempt is evidence, not automatically a terminal task result. The system must retain every required step and every failed attempt, continue bounded recovery when existing authority permits it, and return control to a human only for a genuine authority, goal, acceptance, protected-oracle, independent-review, or exhausted-safe-recovery boundary.

## Single authority model

`TaskRun` remains the sole durable task-lifecycle authority. GitHub PRs, commits, workflows, jobs, steps, failure envelopes, and repair receipts are external evidence. `execution-progress@1` is a read-only projection and must never mint completion, repair, merge, deploy, or production authority.

No second status owner is introduced.

## Whole-task completion

A task is complete only when every required completion condition is satisfied by non-empty evidence and the final TaskRun checkpoint is `COMPLETED`. Completion must not be inferred from the most recent successful action, PR merge, or individual CI run.

For a canonical landing task, required conditions may include, as applicable:

1. implementation prepared;
2. local/focused validation passed;
3. pull request published;
4. exact-head pull-request CI passed;
5. canonical merge completed;
6. resulting `main` SHA reread;
7. required `main`/post-merge validation passed;
8. landed-system acceptance passed;
9. no unresolved required failure or Human Gate remains.

A missing expected child workflow is `PENDING`, not success.

## Failure history

Every failed attempt remains durable evidence even if a later attempt succeeds. The progress projection distinguishes:

- `recovered_failures`: one or more earlier failed attempts followed by a terminal success for the same stage;
- `unresolved_failures`: the latest attempt remains failed/blocked;
- `RECOVERING`: an unresolved failure exists but an already-authorized bounded recovery action is active;
- `BLOCKED`: a true human-owned boundary prevents further safe automatic continuation.

The user-facing summary must never hide an unresolved failure behind another green stage.

## Recovery dispositions

`engineering-failure-recovery-policy@1` separates failure from user interruption:

- `AUTO_REPAIR`: exact bounded source write authority already exists;
- `AUTO_RETRY`: transient failure may retry the exact same candidate within budget;
- `AUTO_DIAGNOSE`: source writes remain closed while bounded read-only diagnosis continues;
- `WAIT_EXTERNAL`: no source mutation; wait for an external environment recovery signal;
- `HUMAN_REQUIRED`: the next safe step requires a human-owned decision or authority.

Unknown evidence never authorizes source writes. It receives bounded read-only diagnosis first, then becomes `HUMAN_REQUIRED` if no safe route is established within budget.

## Protected boundaries

Automatic recovery must never acquire authority to:

- change the user goal;
- weaken acceptance criteria;
- weaken protected tests;
- alter an oracle or accepted baseline merely to make CI green;
- expand write scope without evidence;
- bypass an independent review requirement;
- merge without an existing exact merge grant;
- deploy, release, or enter production.

A protected baseline/oracle change is a Human/Authority Gate, not a source-repair opportunity.

## Status query contract

When asked for progress, the projection must show at least:

- overall task status;
- completed/total required steps when a task plan or completion contract exists;
- every required stage and its current state;
- current stage;
- recovered failures and failed attempt numbers;
- unresolved failures;
- whether automatic recovery is active and the bounded action;
- whether user intervention is currently required;
- the current blocker when human intervention is required.

`run finished` and `task completed successfully` are different statements. A terminal failed workflow makes that workflow finished, but the task remains recovering, failed, or blocked until its completion contract is satisfied.

## Safety invariant

Execution may be quiet while it can recover safely; status queries must be complete and lossless. Automatic continuation must not trade transparency for autonomy, and transparency must not turn every recoverable failure into a user interruption.
