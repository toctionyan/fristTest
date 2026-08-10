# Anti-Stall B1 acceptance criteria

This branch keeps Quality Loop gate, claim, repair-round and convergence semantics unchanged while adding a separate remote-input acquisition harness.

B1 is acceptable only when the focused regressions prove all of the following:

- frozen working-set deduplication avoids duplicate remote reads;
- immutable snapshot cache hits consume zero remote calls and a changed ref forces refresh;
- independent misses execute in bounded batches with maximum width 4;
- one primary timeout/503/empty-result opens its circuit and permits at most one declared fallback;
- fallback failure stops that resource without a third remote path;
- concurrent cache writes preserve every cache index entry;
- process-control exceptions are not swallowed by remote failure handling;
- performance metrics report physical remote reads separately from serial remote depth;
- the anti-stall harness does not own or redefine Quality Loop gate/claim/convergence decisions.

Focused regression entry point:

`services/agent-service/tests/runtime/test_anti_stall_task_harness.py`

## 2026-08-10 PR validation checkpoint

The first full `quality-quick` run reached the standard Python suites and proved 1621 of 1622 Agent tests plus all 38 Business tests. The sole failure was the legacy controller assertion that a rejected ordinary non-empty evidence directory still reports the phrase `new and empty`. Resume admission remained fail-closed; the compatibility fix only restores that legacy wording while retaining the new exception for a verified compatible interrupted run.

The preceding gates were green: Skill control-plane validation, static Quality Loop, adversarial/runtime counterexamples, architecture convergence, systemic operational counterexamples, module closure, presentation contracts, frontend Vitest, and frontend production build. The next full PR run must prove the wording compatibility fix and then allow coverage, lifecycle, browser, and integration gates to proceed.
