# Authority-boundary evidence

The bootstrap adds coordination, not a new completion or write authority.

- The trusted Provider listener owns authentication and converts its native
  payload into the closed normalized ingest request.
- `DurableExternalEventScheduler` owns immutable inbox events, per-Session
  delivery serialization, reservations, and terminal delivery receipts only.
- `StarterHostCommandTransport` and `StarterHostOrchestrator` remain the sole
  public Host execution/session transition boundary.
- LangGraph remains Workflow sequence and resume authority.
- TaskRun remains lifecycle and completion authority; Graph END still projects
  `VALIDATING`.
- `WriteAuthorityGuard` remains the only mutating-capability permit boundary.
- Human Gate decisions remain explicit and cannot be supplied by an external
  event.
- `automatic_merge=false`, `scheduler_is_authority=false`,
  `external_event_completes_taskrun=false`, and `provider_polling=false` are
  closed bootstrap policies.
- The generated customer project remains independently runnable; Scheduler
  files live under Harness-owned `.harness` state and are not a runtime
  dependency of the delivered application.
