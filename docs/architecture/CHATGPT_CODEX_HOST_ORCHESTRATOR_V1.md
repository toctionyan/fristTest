# ChatGPT / Codex Host Orchestrator v1

## Outcome

`StarterHostOrchestrator` is the host-independent interaction owner above the
verified Starter runtime and the durable Host Skill bridge. A ChatGPT or Codex
wrapper no longer has to remember the legal order of separate selection,
confirmation, TaskRun, graph, Host-result, CI-event, and Human-Gate APIs.

One durable `starter-host-session@1` now binds:

- one ChatGPT/Codex Host;
- one verified Starter registration and user request;
- one exact selected entrypoint and, when required, exact effect confirmation;
- one stable TaskRun and resolved Workflow;
- the latest canonical LangGraph runtime state;
- one closed `starter-host-next-action@1` projection;
- one sealed `starter-host-pending-transition@1` only while a start/resume call
  is claimed;
- a monotonic revision used for compare-and-swap transitions.

The session is an ordering and correlation controller. It does not become a
semantic router, Skill executor, Provider, write guard, Quality judge, completion
policy, or merge authority.

The executable process boundary above this controller is the closed
`starter-host-command@1` transport exposed by `python3 -B skillctl.py host` and
documented in `CHATGPT_CODEX_HOST_CLI_TRANSPORT_V1.md`. That transport performs
only exact validation and method dispatch; it does not add another state owner.

## Interaction sequence

### 1. Open

The Host calls `open(session_id=..., user_request=...)`. The orchestrator verifies
the installed Starter and persists `AWAITING_SELECTION`. The next action is
`SELECT_EXACT_ENTRYPOINT` and contains only the candidates produced by
`build_starter_host_selection_request()`.

ChatGPT/Codex interprets the language. Repository code does not add a keyword or
fuzzy fallback.

### 2. Select and confirm

The Host returns `starter-host-selection@1` with one exact candidate and the
current session revision. `select()` delegates validation to
`resolve_starter_host_selection()`.

- A read-only entrypoint becomes `READY_TO_START`.
- A mutating entrypoint becomes `AWAITING_CONFIRMATION` and exposes
  `CONFIRM_EXACT_EFFECT_PREVIEW`.
- `confirm()` accepts only the exact request fingerprint, entrypoint, preview
  digest, `confirmed=true`, and `authority_effect=false`.

Confirmation approves the selected interaction route. It does not create a
ChangePermit or grant write access. Mutating dispatch still requires the existing
injected `WriteAuthorityGuard`.

### 3. Start once

`start()` compare-and-swap claims the current revision, derives a stable TaskRun
ID from the immutable session/Workflow identity, and persists `STARTING` before
calling `StarterWorkflowRuntime.start()`.

The TaskRun binding includes the session fingerprint, Starter registration,
entrypoint, and Workflow. The same task file is reopened on every resume. A
second start, stale revision, different binding, or changed workspace fingerprint
fails closed; no second TaskRun or graph is created.

The caller injects the existing Provider registry, durable LangGraph
checkpointer, optional Human-Gate adapter, and optional WriteAuthorityGuard.
Missing target-specific implementations or authority still block in their
existing owners.

### 4. Follow `next_action`

The orchestrator derives the next action only from the canonical runtime state:

| Runtime state | Session phase | Host next action |
|---|---|---|
| `WAITING_HOST` | `WAITING_HOST` | `EXECUTE_HOST_SKILL` |
| `WAITING_EXTERNAL` | `WAITING_EXTERNAL` | `WAIT_EXTERNAL_EVENT` |
| `HUMAN_GATE` | `HUMAN_GATE` | `REQUEST_HUMAN_DECISION` |
| `BLOCKED_UNRECOVERABLE` | `BLOCKED` | `INSPECT_BLOCKER` |
| `WORKFLOW_END` + TaskRun `VALIDATING` | `VALIDATING` | `EVALUATE_COMPLETION_POLICY` |

No other runtime status may escape as a Host action. Every action explicitly
states that selection is not execution, selection grants no write authority,
Graph END does not complete TaskRun, automatic merge is disabled, and TaskRun is
completion authority.

Persisted `next_action` is never trusted merely because it is valid JSON. Every
open, read, and compare-and-swap update verifies the exact closed session fields,
the whole-session state digest, immutable bindings, phase/runtime relationship,
fixed authority policies, and equality with a newly derived canonical action.
Changing `next_action`, policy, phase, pending input, runtime identity, or TaskRun
binding therefore fails before an action is returned to the Host. The SHA-256
seal is corruption evidence, not a secret or a replacement for filesystem access
control and the existing write-authority guard.

### 5. Resume the matching boundary

Each resume first seals the exact non-authorizing input/evidence/correlation,
claims the current session revision, and enters one non-reentrant `RESUMING_*`
phase.

- `submit_host_result()` is legal only for the active `WAITING_HOST` execution
  ID. It delegates immutable result/tool-receipt validation to
  `DurableHostSkillBridge`, then resumes the same Skill step.
- `resume_external()` is legal only in `WAITING_EXTERNAL`, requires durable
  evidence, and delegates exact event/correlation validation to the Starter
  runtime and Provider adapter.
- `resume_human()` is legal only in `HUMAN_GATE` and requires durable decision
  evidence.

Concurrent callers with the same old revision have one winner. The loser sees a
revision or phase conflict before it can advance the session.

### 6. Reconcile an interrupted claim

A process crash after a claim leaves `STARTING` or `RESUMING_*` plus its exact
sealed pending transition. The next Host process calls `reconcile()` with the
current revision. Reconciliation takes another CAS claim and follows only these
proof rules:

- a start may run when the stable TaskRun is still exactly `CREATED/CREATED`;
- a resume may replay its sealed input when TaskRun still contains the exact
  original Host/external/Human wait handle;
- a newer non-running durable LangGraph snapshot may be adopted and projected
  through the existing TaskRun bridge without invoking the graph again;
- a missing snapshot, `RUNNING` snapshot, or unchanged pre-resume snapshot after
  TaskRun entered `WORKFLOW_RUNTIME_RESUMED` is ambiguous and becomes `BLOCKED`.

This favors at-most-once safety over automatic liveness. Reconciliation does not
invent evidence, infer that an in-flight side effect failed, or blindly execute
an ambiguous step a second time.

## Customer Agent examples

### Overall audit

“检查客服 Agent 总体还有哪些问题” opens a session. ChatGPT/Codex selects
`overall_audit`; the orchestrator starts one TaskRun and returns the exact audit
Skill request. After the Host submits `findings`, the same session returns the
composed standards-gate Skill request. After its `continue` result, deterministic
Quality runs. Graph END is projected as `EVALUATE_COMPLETION_POLICY` with the
TaskRun still `VALIDATING`.

### Repair with CI

“修复 finding-17，测试后提交 GitHub CI” may select `repair_with_ci`, but the
session first stops at the exact effect preview. After confirmation, the repair
Skill still cannot mutate unless the injected write guard accepts the existing
scope/permit. Tests and Quality execute through configured Providers; commit and
PR creation use their exact-scope adapters; CI yields an event-driven external
wait. Green CI reaches validation, never automatic merge.

## Extension model

Adding another Skill or Workflow does not change the orchestrator. A verified
Starter exposes the new candidate, existing selection validation binds it, and
the runtime produces the same closed wait/end states. A new deterministic tool
or external integration is added through a Provider adapter. A new Host transport
may render or carry the session/request/result JSON differently, but it must call
the same compare-and-swap transitions and cannot reinterpret their authority.

The v1 CLI is the canonical reference transport. ChatGPT Skills, Codex commands,
IDE integrations, schedulers, or webhooks may wrap its command schema, but they
must not introduce additional operations, defaults, or lifecycle writers.

## Standalone project boundary

Session files, TaskRuns, Host requests/results, Skill receipts, and LangGraph
checkpoints live under development control-plane directories such as `.harness`
and `.quality`. The customer application imports none of the orchestrator code.
Its source, tests, package manifest, deployment configuration, and runtime
dependencies remain usable when ChatGPT, Codex, the Starter, and Harness artifacts
are removed.
