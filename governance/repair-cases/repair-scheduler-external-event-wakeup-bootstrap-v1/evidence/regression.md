# Regression evidence

Focused and related results:

- Scheduler + concrete bootstrap + Write Authority/Human Gate: PASS, 20 tests;
- Provider Adapter, LangGraph runtime, TaskRun bridge, Host transport,
  orchestrator, Starter runtime, Dispatcher, bootstrap, Scheduler: PASS, 71
  tests;
- complete `skill-unit`: PASS, 870 tests.

The complete `skill-control-plane` profile completed at
`2026-08-24T04:36:34.887753+00:00` with status PASS:

- `skill-static`: PASS, all five commands;
- `skill-unit`: PASS, all six commands;
- `skill-host-integration`: PASS;
- `skill-security`: PASS, 7 tests;
- `project-compatibility-smoke`: PASS, 671 protected files, no drift.

Final `contract-verify` reruns the required profiles after the diff review and
ClosureMatrix are frozen.
