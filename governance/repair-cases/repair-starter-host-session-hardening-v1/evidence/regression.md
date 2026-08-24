# Regression evidence

Focused and related results:

- hardened Host orchestrator: PASS, 9 tests;
- Starter runtime, Host bridge, TaskRun bridge, LangGraph runtime, Dispatcher,
  Provider adapters, and Host orchestrator: PASS, 51 tests;
- complete `skill-unit`: PASS, 841 tests.

Required profile results before final contract verification:

- `skill-static`: PASS;
- `skill-unit`: PASS;
- `skill-host-integration`: PASS;
- `skill-security`: PASS, 7 tests;
- `project-compatibility-smoke`: PASS, 671 protected files and no drift.

Final `contract-verify` reruns all five profiles against the candidate after the
diff and ClosureMatrix are frozen.
