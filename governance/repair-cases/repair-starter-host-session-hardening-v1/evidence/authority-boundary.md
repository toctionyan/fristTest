# Authority-boundary evidence

The repair does not add a new live authority.

- `StarterHostOrchestrator` remains the sole owner of Host-session order, CAS,
  pending-transition correlation, and Host action projection.
- `StarterWorkflowRuntime.recover()` only reads its existing durable LangGraph
  thread and projects a non-running snapshot through the existing TaskRun
  bridge; it does not execute the graph or decide completion.
- LangGraph remains Workflow sequence/resume authority.
- TaskRun remains lifecycle and completion authority.
- `DurableHostSkillBridge` remains immutable Host result/receipt authority.
- Provider adapters, external integrations, Human Gate adapters, and the
  injected `WriteAuthorityGuard` keep their existing responsibilities.
- Session and action policies remain `authority_effect=false`,
  `automatic_merge=false`, and `completion_authority=TaskRun`.
- A transport-specific ChatGPT/Codex CLI remains future work and is not hidden
  inside this repair.
