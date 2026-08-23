# ChatGPT / Codex Host Skill Bridge v1

## Outcome

The installed Harness Starter now has one host-independent bridge for real
ChatGPT or Codex Skill execution. Repository Python still does not impersonate a
model Host. Instead, it creates an immutable request, yields the current Skill
step, accepts a result only after the Host has loaded the exact `SKILL.md` and
performed structured tool calls, and resumes the same TaskRun and LangGraph
checkpoint.

The lifecycle is:

```text
user language or /harness command
  -> exact Starter entrypoint and effect preview
  -> existing write authority check when the selected Skill can mutate
  -> host-skill-execution-request@1
  -> TaskRun WAITING_EXTERNAL_RESULT / WORKFLOW_WAITING_HOST
  -> ChatGPT or Codex loads exact SKILL.md and invokes structured tools
  -> host-skill-execution-result@1 plus host-tool-receipt@1 rows
  -> same TaskRun and Skill step resume
  -> CanonicalSkillInvocationAdapter writes skill-invocation-receipt@1
  -> declared Workflow continues
```

Creating a Host request is not Skill execution and produces no canonical Skill
receipt. A Host result is evidence, not authority. Graph END still projects only
to TaskRun `VALIDATING`.

## Natural-language interaction

Strict `/harness` commands remain deterministic. Natural language uses a bounded
two-part interaction:

1. `build_starter_host_selection_request()` exposes the user text and only the
   entrypoints in the verified installed Starter, each with Workflow identity and
   effect preview.
2. ChatGPT/Codex interprets the language and returns one
   `starter-host-selection@1` containing an exact entrypoint.

Repository code does not use a keyword table or fuzzy fallback. It recomputes the
registration, candidates and request fingerprint before accepting the Host
choice. Read-only routes can proceed to TaskRun creation. A mutating route first
returns `AWAITING_CONFIRMATION` with the exact effect-preview digest. It becomes
executable only after `starter-host-selection-confirmation@1` binds the same
request, entrypoint and preview digest.

Selection and confirmation do not grant write authority. The existing
`WriteAuthorityGuard` remains mandatory when the mutating Skill step is actually
dispatched.

## Durable Skill request

`DurableHostSkillBridge.execute()` deterministically binds:

- Host (`chatgpt` or `codex`);
- TaskRun, Workflow and Skill step;
- request class;
- canonical project-relative `SKILL.md` path and current SHA-256;
- user-payload SHA-256;
- declared Skill outcomes;
- `completion_authority=TaskRun` and `authority_effect=false`.

The request is atomically stored at
`.harness/host-executions/<execution-id>/request.json`. Re-entry with a different
request for the same execution ID blocks. The runtime catches only the explicit
`HostExecutionPending` suspension, records `WAITING_HOST`, and does not consume a
Skill attempt budget until a real result resumes the step.

## Host result and tool receipts

The Host submits `host-skill-execution-result@1`. It must match the exact request
fingerprint, Host, execution ID, loaded Skill identity and one declared outcome.
The output carries its schema, content, SHA-256 and durable evidence reference.

Every reported tool call uses `host-tool-receipt@1` with:

- stable tool-call and tool names;
- argument and result SHA-256 values;
- durable evidence reference;
- explicit `mutates` flag;
- explicit `write_authority_checked` flag.

A mutating tool receipt without `write_authority_checked=true` is rejected. Tool
receipts never grant that authority; they record that the already-existing guard
boundary was passed. Result submission is immutable: an identical retry is
idempotent and a conflicting retry blocks.

The result file and resume pointer are re-hashed at resume. A stale Skill,
different TaskRun/Workflow/step, moved result reference, changed result bytes,
unknown outcome, missing evidence or authority-changing claim blocks before the
canonical Skill receipt is created.

## Pause and resume

`WAITING_HOST` is runtime suspension before a Skill outcome exists. It is not a
new Workflow terminal outcome and does not modify the declarative graph. The
TaskRun bridge maps it to the existing nonterminal status
`WAITING_EXTERNAL_RESULT` with phase `WORKFLOW_WAITING_HOST`.

`StarterWorkflowRuntime.resume()` accepts a Host result only when:

- the state is `WAITING_HOST`;
- the TaskRun's latest checkpoint belongs to the same Workflow;
- execution ID and `host.skill.completed` event match the durable wait handle;
- the result reference and SHA-256 are present;
- durable resume evidence is supplied.

It checkpoints `HOST_EXECUTION`, re-enters the same stage under the same stable
LangGraph thread ID, and lets the bridge validate and consume the result. It does
not create another TaskRun or controller.

## Extension model

New Skills do not require rewriting this bridge. An installed Starter adds a
normal Skill contract and canonical `SKILL.md`, then references that Skill from a
validated Workflow or Composition. The bridge derives the request from those
verified declarations.

New structured Host tools also do not require a new Workflow engine. The Host
executes them through its own supported tool surface and records the common tool
receipt. Deterministic side effects that already have Provider Adapters continue
through `WorkflowAdapterDispatcher`; they should not be duplicated as arbitrary
Host commands.

`StarterHostOrchestrator` now owns the durable interaction order above this
per-Skill transport: bounded selection, exact mutation confirmation, one TaskRun
start, repeated Host/external/human resume, and closed next-action projection.
A transport-specific ChatGPT/Codex wrapper may render or carry the same session,
request, and result objects through a different API, but it cannot fork Skill
identity, write guards, TaskRun lifecycle, Quality verdicts, merge authority, or
completion policy. See `CHATGPT_CODEX_HOST_ORCHESTRATOR_V1.md`.

## Customer Agent example

For “检查客服 Agent 总体还有哪些问题”, ChatGPT/Codex selects
`overall_audit`. The read-only route starts a TaskRun, yields at
`customer-agent-audit`, resumes from its Host result, then may yield again at the
composed `customer-agent-standards-gate`. Only after both real Skill results exist
does the Workflow call the configured Quality Provider and reach Graph END.

For “修复 finding-17，测试后提交 GitHub CI”, the Host may select
`repair_with_ci`, but the first response is only an effect preview. The user must
confirm the exact route and preview digest. Runtime then still requires the
existing write guard, durable Host repair evidence, deterministic local tests and
Quality, exact Git/PR Provider receipts, and CI wait/resume. The Workflow contains
no merge step.

## Standalone project boundary

Host requests/results, Skill receipts, Starter registration, TaskRun records and
LangGraph checkpoints are development control-plane artifacts. The generated
customer Agent imports none of them. Removing `.harness` and Harness tooling does
not remove the application's source, tests, packaging, deployment commands or
runtime dependencies; the developed project remains independently runnable and
maintainable.
