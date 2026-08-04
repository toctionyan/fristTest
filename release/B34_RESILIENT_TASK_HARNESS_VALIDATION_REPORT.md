# B34 Resilient Task Harness Validation Report

## Scope

B34 fixes the governed repair-loop class of premature termination and lost-progress failures. It adds durable TaskRun state, completion evidence, bounded retries, fallback routing and interruption recovery. Product Agent, Business Service, transaction and Stage 1-5 runtime authorities are unchanged.

## Implemented controls

- Atomic JSON checkpoint ledger with revision conflict detection.
- Immutable task binding to target, policy and baseline evidence.
- Completion Guard: every required condition must be satisfied and carry evidence.
- Action fingerprints and bounded strategy budgets; exhaustion switches fallback or records BLOCKED.
- Workspace fingerprint drift rejection. Only a durable `FIXER_RUNNING` checkpoint may reconcile a source mutation after host interruption.
- CLI `inspect` and `guard` commands for machine-readable status and CI gating.
- Exact workspace fingerprint required before reusing an existing run-summary.

## Validation

- Focused resilient harness: **17 passed**.
- Full Skill control-plane tests: **141 passed**.
- Existing Quality Loop controller/governance regression: **73 passed, 4 deselected**. The four deselected tests import `langchain_core`, which is absent from this local interpreter; they are unrelated protected model-smoke tests and must run in the locked GitHub environment.
- Skill control-plane profile: **PASS**.
- Architecture, module closure, version consistency and convergence: **PASS**.
- ChangePermit validation: **PASS**.

## Real interruption proof

The counterexample launches the real repair orchestrator. A fixer writes the governed source change and then sends `SIGKILL` to its parent process before the parent can record `FIXER_APPLIED`. On restart, B34 observes the durable `FIXER_RUNNING` checkpoint, reconciles the changed source against the immutable baseline, skips a replacement fixer that would fail if executed, runs validation and completes only after every completion condition has evidence.

## Limits

This closes only the project repair Harness defect. It cannot change ChatGPT platform conversation limits or guarantee that an external connector never fails. Its guarantee is that the project task state remains explicit and resumable when its host workspace and TaskRun file persist. Ephemeral GitHub runners must preserve `.quality/task-runs` as an artifact or use durable storage for cross-run resume.

`production_closed` remains **false**. Real model, PostgreSQL, browser and release certification are outside this B34 work package.
