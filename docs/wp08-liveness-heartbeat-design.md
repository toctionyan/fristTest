# WP-08 certification liveness heartbeat

This change makes long-running WP-08 certification externally observable without changing release authority or `production_closed` semantics.

## Runtime signals

`run_wp08_certification.py` now emits three machine-readable log prefixes:

- `[WP08 BATCH]` when a batch starts or completes.
- `[WP08 HEARTBEAT]` while a child process is alive. Default cadence: 30 seconds.
- `[WP08 LIVENESS]` when a child process exits. `[WP08 STALL]` / `[WP08 TIMEOUT]` are emitted before fail-closed termination.

Every heartbeat records the current batch index/total, child PID/alive state, elapsed time, idle time since the last child stdout/stderr activity, the most recent progress timestamp, and a progress-event counter.

The same fields are persisted into the normal WP-08 checkpoint state and into `wp08-liveness.json` beside the checkpoint. The existing workflow already uploads the complete checkpoint directory, so liveness evidence is retained with the normal WP-08 artifact.

## Stall policy

Versioned defaults in the runner are:

- heartbeat: 30 seconds;
- `SUSPECTED_STALL`: no child stdout/stderr progress for 240 seconds;
- fail-closed no-progress stall timeout: 600 seconds.

The normal per-batch timeout remains authoritative and may terminate earlier. Environment overrides (`WP08_HEARTBEAT_SECONDS`, `WP08_STALL_WARNING_SECONDS`, `WP08_STALL_TIMEOUT_SECONDS`) exist for controlled testing/operations. Invalid or non-positive values fall back to the versioned defaults.

A GitHub Job showing `in_progress` is therefore no longer sufficient evidence by itself. Healthy execution requires a recent heartbeat; an old heartbeat plus growing idle time is explicitly surfaced as `SUSPECTED_STALL`, and prolonged lack of observable progress terminates as `TIMEOUT` with `termination_reason=no_progress_stall` rather than hanging indefinitely.

## Boundaries

This instrumentation does not:

- dispatch or authorize a WP-08 ReleaseRun;
- modify the durable release ledger;
- infer business semantics;
- change batch pass/fail classification except for the new fail-closed no-progress timeout;
- claim production closure.
