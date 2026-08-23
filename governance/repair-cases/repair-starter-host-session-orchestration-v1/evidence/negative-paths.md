# Negative-path evidence

- The orchestrator calls the existing Host selection validator; it contains no
  keyword or fuzzy natural-language router.
- Session selection and exact confirmation set `write_authority_granted=false`;
  the existing `WriteAuthorityGuard` is still independently injected into
  `StarterWorkflowRuntime`.
- No arbitrary Host command or tool claim is executed by the session controller.
- A Host request alone remains `WAITING_HOST` and creates no canonical Skill
  invocation receipt.
- External waits remain event-driven; the controller contains no polling or
  sleep loop.
- Unknown runtime states cannot be projected into a next action.
- Graph END is accepted only with TaskRun `VALIDATING`; automatic merge remains
  false and no merge adapter exists in this change.
- Customer application source, services, web code, contracts, packaging, and
  runtime dependencies are unchanged.

