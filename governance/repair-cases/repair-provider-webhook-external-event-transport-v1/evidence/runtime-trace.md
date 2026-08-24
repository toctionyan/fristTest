# Runtime trace

The focused end-to-end transport test exercised this exact sequence:

1. Receive `POST /v1/github/workflow-run/<session-id>` with GitHub headers and
   the exact raw request body.
2. Resolve the configured secret at runtime and verify the raw-body HMAC-SHA256
   using constant-time comparison.
3. Validate the closed `workflow_run` completion payload and exact configured
   repository.
4. Persist sealed provider evidence containing the base64 raw body, payload
   digest, received HMAC, delivery identity, run identity, and no authority.
5. Normalize only to provider `github.actions`, correlation id
   `run-<workflow_run.id>`, event `ci.completed`, and the provider conclusion.
6. Call the existing Scheduler `ingest`, then `wake` for the named Host session.
7. Persist a delivery receipt only after the Scheduler returns `DELIVERED`.
8. Replay the same delivery from the durable receipt without invoking the
   Scheduler again.

The Scheduler, Host, Workflow, TaskRun, and adapter ownership boundaries remain
unchanged throughout the trace.
