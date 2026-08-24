# Provider Webhook / External Event Transport v1

## Outcome

A GitHub-enabled concrete ChatGPT/Codex Host can receive a real signed
`workflow_run` webhook, preserve the exact provider bytes as durable evidence,
and deliver one normalized `ci.completed` event through the existing Scheduler.

The transport is an ingress adapter, not another controller:

```text
GitHub workflow_run webhook
          |
          | HMAC-SHA256 over exact body bytes
          v
GitHubWorkflowRunWebhookTransport
          |
          | sealed provider evidence + replay receipt
          v
DurableExternalEventScheduler ingest / wake
          |
          v
existing Host transport -> same Workflow / TaskRun
```

## Host initialization

Configure both the GitHub API token name and a distinct webhook secret name:

```bash
python3 -B skillctl.py authoring host-init \
  --project-workspace /path/to/project \
  --github-repository owner/repository \
  --github-token-environment-variable GITHUB_TOKEN \
  --github-webhook-secret-environment-variable GITHUB_WEBHOOK_SECRET

export GITHUB_TOKEN='provider API token'
export GITHUB_WEBHOOK_SECRET='at least sixteen bytes of webhook secret'
```

Only environment-variable names are written to
`.harness/host/bootstrap.json`. Secret values are loaded when used and are
removed from the environment passed to sealed project command profiles.

`concrete-host-bootstrap@4` binds the listener to one exact GitHub repository,
the existing `github.actions` Provider ID, three bounded `.harness` roots, a
maximum body size, and fixed non-authorizing policy.

## HTTP listener

Run the built-in WSGI listener on loopback behind an HTTPS reverse proxy:

```bash
export HARNESS_HOST_FACTORY=concrete_host_bootstrap:build_orchestrator
export HARNESS_HOST_BOOTSTRAP=/path/to/project/.harness/host/bootstrap.json

python3 -B skillctl.py provider-webhook \
  --host-id codex serve --bind 127.0.0.1 --port 8787
```

Configure the GitHub webhook URL to the exact waiting Host session route:

```text
https://harness.example.test/v1/github/workflow-run/<session-id>
```

The built-in server intentionally refuses a non-loopback bind. TLS,
rate-limiting, public network policy, and reverse-proxy hardening remain outside
the Python process. The listener is event-driven; it never polls GitHub or
sleeps waiting for provider state.

For a serverless gateway or another authenticated HTTP front end, submit one
exact raw request through the same implementation:

```bash
python3 -B skillctl.py provider-webhook --host-id codex receive \
  --session-id customer-repair-17 \
  --headers github-headers.json \
  --body github-body.json
```

The headers file is a JSON string map containing `Content-Type`,
`X-GitHub-Event`, `X-GitHub-Delivery`, and `X-Hub-Signature-256`. The body file
must contain the exact bytes received from GitHub; reformatting it invalidates
the HMAC.

## Authentication and normalization

The transport accepts only:

- `POST` with `Content-Type: application/json`;
- `X-GitHub-Event: workflow_run`;
- a stable `X-GitHub-Delivery` value;
- `X-Hub-Signature-256: sha256=<digest>` matching HMAC-SHA256 of the exact raw
  body under the configured secret;
- `action=completed` and `workflow_run.status=completed`;
- the exact configured `repository.full_name`;
- a positive workflow run ID and attempt plus one full 40-character head SHA;
- a bounded provider conclusion string.

It writes `github-workflow-run-webhook-evidence@1`, including the exact body in
base64, its SHA-256 digest, provider delivery ID, repository, run/attempt, head
SHA, conclusion, session route, the received HMAC digest, and
`signature_verified=true`. The secret is never persisted. On replay, the
transport recomputes HMAC from the sealed raw bytes and rejects an artifact that
was changed and merely resealed with an ordinary SHA-256 digest.

Normalization is fixed and contains no model or heuristic choice:

| Scheduler field | GitHub source |
|---|---|
| `provider` | configured `github.actions` Provider ID |
| `correlation_ref` | `run-<workflow_run.id>` |
| `event` | `ci.completed` |
| `conclusion` | `workflow_run.conclusion` |
| `evidence_refs` | sealed provider evidence plus exact repository/run/head identity |

The listener does not map the conclusion to green, red, or blocked.
`EventDrivenCIProviderAdapter` remains the sole owner of that interpretation.

## Replay, concurrency, and failure

One lock serializes each GitHub delivery ID. Repeating the same signed bytes for
the same session returns the same sealed transport receipt without a second
Scheduler call. Reusing the delivery ID with different bytes or a different
session is a conflict and fails closed.

The transport writes provider evidence before Scheduler ingest. If the process
stops after ingest or wake but before its own receipt, GitHub may retry the same
delivery. The Scheduler's deterministic event identity, per-session lock,
reservation, TaskRun proof, and idempotent receipt close that recovery path.

A Scheduler result other than `DELIVERED` is returned as `REJECTED`; the
listener cannot reinterpret stale or uncertain delivery as success.

## Authority boundaries

```text
valid HMAC              != valid Host wait
provider evidence       != Scheduler delivery
Scheduler delivery      != CI green
CI green                != Quality green
Workflow END            != TaskRun completed
webhook configuration   != write or merge authority
```

The transport never selects a Workflow, edits Host session or TaskRun files,
interprets CI success, grants a ChangePermit, approves a Human Gate, writes
product code, completes a TaskRun, merges a pull request, deploys, releases, or
closes production. Every evidence, receipt, result, and bootstrap policy keeps
`authority_effect=false`; TaskRun remains the completion authority.

## Standalone application boundary

Provider evidence, delivery receipts, and locks remain under `.harness/**`.
Customer Agent source, manifests, tests, production dependencies, and runtime
entrypoints do not import the webhook transport. Removing Harness state does not
change the delivered application's ability to run independently.
