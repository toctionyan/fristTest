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
- Single authoritative `REPAIR_RUNTIME_CONTROLLER_FILES` dependency manifest owned by `repair_loop.py`; minimal workspace fixtures consume that manifest instead of maintaining divergent file lists.
- Historical issue evidence is bound to an immutable B34 repair-case record instead of a mutable architecture test source.
- The protected product-source baseline is regenerated from the exact 546-file B34 candidate snapshot after the governed test change.

## Validation

### Local governed validation

- Focused resilient harness: **17 passed**.
- Full Skill control-plane tests: **141 passed**.
- Existing Quality Loop controller/governance regression: **73 passed, 4 deselected**. The four deselected tests import `langchain_core`, which is absent from the local interpreter; they are unrelated protected model-smoke tests and are covered by the locked GitHub environment.
- Skill control-plane profile: **PASS**.
- Architecture, module closure, version consistency and convergence: **PASS**.
- ChangePermit validation: **PASS**.

### GitHub dependency-closure validation

The fail-closed publisher reconstructed the exact three-file dependency-closure patch on B34 commit `72cce126e4124e3abefb282de40e00ae1a91cb1b`, verified the patch and changed-path scope, installed the locked Python 3.12.13 Agent environment including LangChain/LangGraph, and ran:

- Existing GitHub Quick adversarial/governance group: **137 passed**.
- B34 TaskRun, CLI and real interruption-recovery group: **17 passed**.

Only after both groups passed was the direct-source commit `685b2af7e9a1da55ec2993525493f322bd9a9ba3` published to `agent/b34-resilient-task-harness-20260804`.

### GitHub governance-authority correction

The B34 publisher kept the historical B28 evidence archive unchanged, replaced the mutable `ISSUE-REL-001` architecture-test reference with an immutable B34 repair-case evidence record, regenerated the protected-source baseline from the exact candidate tree, and then ran:

- Authoritative Task Ledger validation: **PASS**.
- Project compatibility against 546 protected files: **PASS**.
- Complete Skill control-plane profile: **PASS**.

Only after all three checks passed was direct-source commit `07ac4a1a4fc16a7b0dc7c02dac1e67fd760a729e` published.

## Real interruption proof

The counterexample launches the real repair orchestrator. A fixer writes the governed source change and then sends `SIGKILL` to its parent process before the parent can record `FIXER_APPLIED`. On restart, B34 observes the durable `FIXER_RUNNING` checkpoint, reconciles the changed source against the immutable baseline, skips a replacement fixer that would fail if executed, runs validation and completes only after every completion condition has evidence.

## Limits

This closes only the project repair Harness defect. It cannot change ChatGPT platform conversation limits or guarantee that an external connector never fails. Its guarantee is that the project task state remains explicit and resumable when its host workspace and TaskRun file persist. Ephemeral GitHub runners must preserve `.quality/task-runs` as an artifact or use durable storage for cross-run resume.

`production_closed` remains **false** until the repository Quick, PostgreSQL/double-service/browser Integration and protected production certification are independently completed.
