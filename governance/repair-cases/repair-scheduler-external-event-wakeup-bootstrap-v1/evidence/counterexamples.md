# Counterexample evidence

Direct tests reject or contain these counterexamples:

- a webhook payload that names the wrong provider, correlation reference,
  resume event, Session, Host execution, or TaskRun;
- an event received after the exact wait has already advanced;
- duplicate redelivery of the same event and two distinct events racing for the
  same one-shot wait;
- an event file changed after sealing, a symlink, or a `file:` reference outside
  the initialized inbox;
- a reservation left by process death before Host publication;
- a completed Host resume whose durable receipt was not written before process
  death; and
- a wake request that lacks exact durable TaskRun/checkpoint evidence.

The Scheduler never treats receipt presence, provider success, Graph END, or a
CI event as TaskRun completion. Unprovable interrupted states become
`BLOCKED_UNCERTAIN`; stale events become `REJECTED_STALE`.
