# ChatGPT / Codex Host CLI Transport v1

The generic factory boundary now has a repository-owned concrete implementation and one-command project initializer. See [Concrete Host Bootstrap / Project Initializer v1](CONCRETE_HOST_BOOTSTRAP_INITIALIZER_V1.md). The trusted operator settings are `HARNESS_HOST_FACTORY=concrete_host_bootstrap:build_orchestrator` and `HARNESS_HOST_BOOTSTRAP=<project>/.harness/host/bootstrap.json`; neither can be selected by a Host command.

## Outcome

`python3 -B skillctl.py host` is the canonical one-command process boundary for
ChatGPT, Codex, and other trusted development Hosts. It accepts one closed
`starter-host-command@1` JSON object from stdin or an explicitly selected file,
calls the existing `StarterHostOrchestrator`, and emits one closed
`starter-host-command-response@1` JSON object.

The CLI is transport, not a second Harness runtime. It does not interpret user
language, choose a Skill or Workflow, execute a Skill directly, bind a Provider,
authorize a write, evaluate completion, publish a release, or merge a PR.

## Trusted bootstrap boundary

The operator selects a project bootstrap factory outside the untrusted command:

```text
python3 -B skillctl.py host \
  --factory customer_agent_host:build_orchestrator \
  --request host-command.json
```

The same factory may be configured by the operator-owned
`HARNESS_HOST_FACTORY` environment variable. A request cannot contain a module,
path, callable, shell command, Provider, checkpointer, credential, or Guard
selector. Unexpected request fields are rejected before the factory is loaded.

The factory contract is deliberately small:

```python
def build_orchestrator(*, host_id: str) -> StarterHostOrchestrator:
    return StarterHostOrchestrator(
        registry_workspace=verified_registry,
        project_workspace=project,
        registration=verified_registration,
        host_id=host_id,
        provider_adapters=activated_provider_registry,
        checkpointer=durable_checkpointer,
        workspace_fingerprint=current_workspace_fingerprint,
        write_authority_guard=existing_guard_or_none,
        human_gate_adapter=explicit_human_gate_adapter_or_none,
    )
```

This is dependency injection, not request-time code discovery. The factory owns
environment configuration and composes already-existing verified components.
It must not create another session controller, Runtime, TaskRun writer, Judge,
write guard, or completion policy.

## One closed command

Every request has exactly these fields:

```json
{
  "schema": "starter-host-command@1",
  "command_id": "cmd-audit-open-1",
  "host_id": "codex",
  "operation": "OPEN",
  "session_id": "customer-audit-1",
  "expected_revision": null,
  "payload": {
    "user_request": "检查客服 Agent 总体还有哪些问题"
  },
  "authority_effect": false
}
```

`OPEN` carries natural language unchanged. Repository code returns only the
verified entrypoint candidates. ChatGPT/Codex interprets the request and sends a
separate exact `SELECT`; there is no keyword, fuzzy, or nearest-name fallback.

## Fixed operations

| Operation | Revision | Exact payload | Existing owner called |
|---|---:|---|---|
| `OPEN` | `null` | `user_request` | `open()` |
| `READ` | `null` | empty | `read()` |
| `SELECT` | current | `selection` | `select()` |
| `CONFIRM` | current | `confirmation` | `confirm()` |
| `START` | current | `target_ref` | `start()` |
| `SUBMIT_HOST_RESULT` | current | `result` | `submit_host_result()` |
| `RESUME_EXTERNAL` | current | `event`, `evidence_refs`, `correlation_ref` | `resume_external()` |
| `RESUME_HUMAN` | current | `decision`, `evidence_refs` | `resume_human()` |
| `RECONCILE` | current | empty | `reconcile()` |

There is no generic method name, arbitrary argument bag, implicit revision,
automatic selection, automatic confirmation, `COMPLETE`, `RELEASE`, or `MERGE`
operation. Duplicate or stale transitions are rejected by the Orchestrator's
compare-and-swap revision and phase checks.

## Concrete overall-audit interaction

1. Codex sends `OPEN` with the user's sentence.
2. The response contains `SELECT_EXACT_ENTRYPOINT` and the verified candidates.
3. Codex chooses `overall_audit` and sends the exact
   `starter-host-selection@1` as the `SELECT` payload with revision `0`.
4. The response becomes `START_TASKRUN`. Codex sends `START` with an exact
   project target and revision `1`.
5. When the response is `EXECUTE_HOST_SKILL`, Codex reads the referenced durable
   Host Skill request, actually executes the Skill and permitted tools, then
   sends the complete immutable result through `SUBMIT_HOST_RESULT`.
6. Further Skills repeat step 5. A deterministic test/Quality step runs only
   through its configured Provider adapter.
7. Workflow END returns `EVALUATE_COMPLETION_POLICY`; TaskRun remains
   `VALIDATING`, not `COMPLETED`.

The user can instead send an exact command from a script. The command protocol
is the same; only the producer of the JSON changes.

## Repair, GitHub CI, and Human Gate

A repair request may select `repair_with_ci`, but the transport first returns
`CONFIRM_EXACT_EFFECT_PREVIEW`. `CONFIRM` approves only the exact preview. It
still does not create a ChangePermit or grant write access. Mutating Skill and
Provider steps reach the existing `WriteAuthorityGuard` during dispatch.

GitHub Actions yields `WAIT_EXTERNAL_EVENT`. A scheduler or webhook stores the
real event/evidence and calls `RESUME_EXTERNAL` with the exact correlation. A
Human Gate similarly requires `RESUME_HUMAN` with an explicit decision and
durable evidence. Neither wait keeps a ChatGPT/Codex process alive.

If a process dies while a session is `STARTING` or `RESUMING_*`, the next process
first sends `READ`, then `RECONCILE` with the current revision. Reconciliation
uses the persisted pending transition and durable runtime checkpoint; ambiguous
effects become `BLOCKED` instead of being blindly replayed.

## Response and failure behavior

A successful response contains the canonical full session and the identical
`next_action` projection. Its fixed policy states:

- transport is not authority;
- semantic routing is disabled;
- write authority is not granted;
- TaskRun remains completion authority;
- automatic merge is disabled;
- `authority_effect` is false.

Malformed commands return `BLOCKED` with exit code `2`. Factory/bootstrap
configuration failures also return a bounded configuration error. An
Orchestrator rejection returns exit code `3`. Responses do not include Python
tracebacks, factory objects, credentials, or raw exception messages. The Host
uses `READ` to inspect the durable canonical state before deciding whether a
new command is legal.

## Extension model

Adding a new project does not change this transport. Install or generate its
Starter, Skill contracts, Workflow declarations, Provider bindings, and trusted
factory; then point the same root CLI at that factory. Adding a Skill or Workflow
changes verified candidates and runtime behavior below the Orchestrator, not the
wire operations.

A graphical UI, ChatGPT Skill, Codex command, IDE plugin, webhook receiver, or
scheduler can all generate the same JSON envelope. They may render fields
differently for a person, but they cannot omit the exact revision/evidence or
reinterpret the response policy.

## Standalone project boundary

The CLI, factory, Starter, Host sessions, TaskRuns, receipts, and checkpoints are
development control-plane artifacts. The generated customer application does
not import them. Its source, tests, package manifest, deployment files, and
runtime dependencies remain independently runnable after the Harness is removed.
