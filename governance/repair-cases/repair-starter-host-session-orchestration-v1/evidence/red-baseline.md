# Red baseline: Starter Host session orchestration is missing

## Reproduction

Base revision: `a3aaf2acde552c4681dd5a9f2cf818ba4e7c272e` (merged PR #2095).

Repository inspection shows that the independently tested pieces exist, but no
single durable Host session owns their legal ordering:

- `build_starter_host_selection_request()` and
  `resolve_starter_host_selection()` expose and validate bounded natural-language
  selection.
- `StarterWorkflowRuntime.start()` and `.resume()` execute one already-resolved
  entrypoint through the existing TaskRun and LangGraph runtime.
- `DurableHostSkillBridge` persists and validates an individual Skill request and
  result.
- `skillctl.py invoke` stops at a route/activation description; it does not own a
  session, TaskRun, selection confirmation, or repeated resume cursor.

There is no `starter_host_orchestrator.py`, no versioned Host-session state
contract, and no controller that rejects out-of-order selection, duplicate start,
wrong TaskRun/runtime state, or an unrelated Host/external/human resume.

## Failure

A ChatGPT/Codex wrapper must currently assemble those operations itself and can
accidentally create a second TaskRun, lose the exact selection/confirmation
binding, restart a waiting Workflow, or resume an unrelated wait. The low-level
components fail closed individually, but their cross-call order and durable
session identity are not repository-owned or directly testable.

## Expected

One host-independent, immutable-identity session controller must persist the
selection request and exact resolution, start exactly one existing TaskRun,
project the current runtime wait/gate/end state into one closed `next_action`, and
resume only through the matching Host result, external event, or human decision.
It must delegate all execution and authority decisions to the existing
components, never interpret natural language, never grant write access, never
mark completion, and never merge.

