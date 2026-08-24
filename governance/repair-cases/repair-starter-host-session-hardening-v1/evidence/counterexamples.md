# Counterexample evidence

Direct tests reject or block these counterexamples:

- changed `next_action.kind`, action policy, session policy, unknown fields, or
  stale state digest;
- stale revision, changed registration, changed selection identity, wrong Host
  execution ID, and wrong resume kind;
- external or Human resume without durable evidence;
- external event/correlation mismatch through the existing Starter runtime;
- concurrent writers using the same old revision;
- duplicate start and Graph END paired with TaskRun `COMPLETED`;
- reconciliation of an unchanged pre-resume graph snapshot after TaskRun has
  already entered `WORKFLOW_RUNTIME_RESUMED`.

The last case is deliberately `BLOCKED`; the controller cannot prove that an
in-flight side effect did not occur and therefore does not trade at-most-once
safety for automatic liveness.
