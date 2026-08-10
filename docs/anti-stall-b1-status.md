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
