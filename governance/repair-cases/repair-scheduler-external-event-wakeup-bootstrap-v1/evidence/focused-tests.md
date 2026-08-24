# Focused test evidence

The exact project dependency environment ran the Scheduler, concrete Host
bootstrap, and Write Authority/Human Gate regression suites.

Result: PASS, 20 tests. The Scheduler suite contributes eight direct tests and
proves:

- an exact normalized event is sealed once and duplicate ingestion is
  idempotent;
- provider, correlation, event name, Session, TaskRun state, and latest durable
  wait checkpoint must all match before an event is accepted;
- different events racing for one Session are serialized, and the second event
  becomes stale instead of executing another resume;
- a crash after reservation re-enters the existing Host `RECONCILE` path;
- a crash after Host success but before receipt publication is recovered only
  from exact TaskRun resume evidence;
- tampered events, symlink/outside-root references, and absent evidence fail
  closed;
- `run-once` processes a bounded inbox snapshot without polling a provider or
  sleeping; and
- the root `skillctl scheduler` boundary works with a portable initialized
  project.

The related Provider Adapter, LangGraph, TaskRun bridge, Host transport,
orchestrator, Starter runtime, Dispatcher, bootstrap, and Scheduler command
passed 71 tests. The complete `skill-unit` profile passed 870 tests.
