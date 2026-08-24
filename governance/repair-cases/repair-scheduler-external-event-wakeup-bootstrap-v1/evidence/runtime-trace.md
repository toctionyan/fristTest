# Runtime trace evidence

The focused suite exercises this durable sequence:

1. an initialized Host Session reaches `WAITING_EXTERNAL` with the exact
   provider/correlation/event tuple and TaskRun checkpoint;
2. `scheduler ingest` validates both durable authorities and atomically seals a
   normalized event beneath `.harness/runtime/external-events`;
3. `scheduler wake`, under the per-Session lock, writes one immutable
   reservation before invoking existing Host `RESUME_EXTERNAL` transport;
4. the existing Starter runtime resumes the same TaskRun and Graph thread;
5. Graph END projects the TaskRun to `VALIDATING`, never `COMPLETED`;
6. the Scheduler seals a terminal `DELIVERED` receipt.

Crash traces prove two recovery branches:

- a reservation with the original durable wait still current invokes existing
  Host `RECONCILE`; and
- Host success without a receipt is accepted only when the same TaskRun's
  durable evidence proves that exact event/correlation resume.

An unprovable branch terminates as `BLOCKED_UNCERTAIN`; a competing event after
the wait advances terminates as `REJECTED_STALE`.
