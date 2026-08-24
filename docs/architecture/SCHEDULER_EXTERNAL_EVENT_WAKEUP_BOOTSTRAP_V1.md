# Scheduler / External Event Wake-up Bootstrap v1

## Outcome

An initialized ChatGPT/Codex Harness can now stop at `WAIT_EXTERNAL_EVENT`, terminate the current Host process, and later resume the exact same Host session, Workflow step, LangGraph thread, and TaskRun from one durable provider event.

The scheduler is a one-shot delivery component. It does not poll GitHub, GitLab, Jenkins, or any other Provider and does not run a background loop. A trusted provider listener authenticates and normalizes the external source; a webhook invokes `ingest` and then `wake`, or a system scheduler invokes bounded `run-once` against the durable inbox.

```text
Provider webhook / trusted listener
        |
        | authenticated normalized event
        v
external-event ingest
        |
        | immutable exact-wait event artifact
        v
one-shot wake / bounded run-once
        |
        | per-session lock + delivery reservation
        v
existing Starter Host transport
        |
        +--> RESUME_EXTERNAL
        `--> RECONCILE only for the exact persisted pending transition
                 |
                 v
       same LangGraph checkpoint + same TaskRun
```

## Exact event binding

Ingest first reads the canonical Host session. It is legal only when all of these identities agree:

- Host session phase is exactly `WAITING_EXTERNAL`;
- runtime state contains `provider`, `correlation_ref`, and `resume_event`;
- the normalized event carries that exact provider, correlation, and event name;
- the event has non-empty durable evidence references;
- the session's TaskRun is exactly `WAITING_EXTERNAL_RESULT / WORKFLOW_WAITING_EXTERNAL`;
- the latest TaskRun checkpoint contains the same Workflow and external-wait handle.

The persisted `external-wakeup-event@1` binds the Host, session, TaskRun, Workflow, expected session revision, wait-checkpoint sequence, wait handle, event bytes, and evidence. Its deterministic event ID is computed from that full identity, and its immutable representation has a second SHA-256 seal.

No fuzzy matching, Provider fallback, nearest session, or event-name inference exists. An event that arrives before the exact wait has been published is rejected and may be retried by the trusted listener after the wait exists.

## Delivery, concurrency, and crash recovery

Wake-up acquires one lock per Host session, not merely per event. Two different events racing for the same wait therefore cannot both claim it.

Before calling the Host, the scheduler writes an immutable reservation. Delivery then uses only the existing closed operations:

1. `RESUME_EXTERNAL` when the session still has the exact expected revision and wait;
2. `RECONCILE` when a prior process already persisted the exact `RESUMING_EXTERNAL` pending transition;
3. recovery from TaskRun evidence when the Host completed the resume but the scheduler process died before writing its receipt.

Recovery evidence must contain a later `WORKFLOW_RUNTIME_RESUMED` checkpoint with the same Workflow, correlation, resume kind, and event artifact reference. A merely advanced revision is insufficient. Ambiguous resuming state is left to the existing Orchestrator reconciliation policy; the scheduler never mutates session or TaskRun files directly.

One terminal `external-wakeup-receipt@1` records `DELIVERED`, `REJECTED_STALE`, or `BLOCKED_UNCERTAIN`. Repeating wake on the same event returns that exact receipt and does not call resume a second time.

## Commands

The Concrete Host initializer returns the same trusted environment used by the Host CLI:

```bash
export HARNESS_HOST_FACTORY=concrete_host_bootstrap:build_orchestrator
export HARNESS_HOST_BOOTSTRAP=/path/to/project/.harness/host/bootstrap.json
```

A provider-neutral caller may still write one closed trusted request:

```json
{
  "schema": "external-event-ingest-request@1",
  "host_id": "codex",
  "session_id": "customer-repair-17",
  "event": {
    "provider": "github.actions",
    "correlation_ref": "run-32687603837",
    "event": "ci.completed",
    "conclusion": "success",
    "evidence_refs": ["github-run:32687603837"]
  },
  "authority_effect": false
}
```

Then it invokes:

```bash
python3 -B skillctl.py scheduler --host-id codex ingest --request event.json
python3 -B skillctl.py scheduler --host-id codex wake \
  --event-ref file:.harness/runtime/external-events/<event-id>.json
```

For real GitHub Actions delivery, the concrete Host now supplies a signed
`workflow_run` listener that verifies HMAC, persists raw provider evidence, and
calls these same Scheduler operations without hand-written normalization:

```bash
python3 -B skillctl.py provider-webhook \
  --host-id codex serve --bind 127.0.0.1 --port 8787
```

See [Provider Webhook / External Event Transport v1](PROVIDER_WEBHOOK_EXTERNAL_EVENT_TRANSPORT_V1.md).

Alternatively, cron, launchd, systemd timer, GitHub webhook automation, or an IDE extension may trigger one bounded inbox snapshot:

```bash
python3 -B skillctl.py scheduler --host-id codex run-once
```

`run-once` processes at most `max_events_per_run` persisted files and exits. It does not fetch Provider status, sleep, or remain resident.

## Provider extension

The generic scheduler does not authenticate GitHub signatures or reinterpret GitLab/Jenkins payloads. Each Integration listener remains responsible for:

1. authenticating the original webhook or API response;
2. resolving its own provider-native run identity;
3. writing durable provider evidence;
4. normalizing provider, correlation, event name, conclusion, and evidence refs;
5. submitting that normalized data to the same scheduler ingest contract.

The existing `EventDrivenCIProviderAdapter` remains the owner of provider-result interpretation (`green`, `red`, or `blocked`). The scheduler cannot choose a Workflow route from the conclusion.

## Authority boundaries

```text
event received          != provider authenticated by generic scheduler
event persisted         != Host session resumed
wakeup delivered        != CI green
CI green                != Quality green
TaskRun resume           != TaskRun completed
Graph END               != TaskRun completed
scheduler configuration != write authority
Human decision          != scheduler event
```

The scheduler and every event/reservation/receipt explicitly state:

- `authority_effect=false`;
- `completion_authority_changed=false`;
- `merge_authority_changed=false`.

It cannot create a ChangePermit, approve a Human Gate, satisfy Quality, declare Problem Ledger closure, complete a TaskRun, merge a pull request, deploy, release, or close production. Graph END continues to project to `TaskRun VALIDATING` and an independent completion policy remains required.

## Standalone application boundary

Events, reservations, locks, receipts, Host sessions, TaskRuns, and LangGraph checkpoints stay below `.harness/**`. The customer Agent's source, package manifest, tests, deployment files, and runtime dependencies do not import this scheduler. The developed project can still be copied, deployed, and run after Harness state is removed.

Keeping `.harness` is optional for future Harness-assisted maintenance; it is not required by the delivered application's runtime.
