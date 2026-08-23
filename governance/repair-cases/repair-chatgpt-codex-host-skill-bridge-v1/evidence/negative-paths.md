# Negative paths

- Host data is parsed as closed JSON contracts; it cannot provide an executable,
  shell command or arbitrary Workflow route.
- The model Host chooses only among verified installed Starter entrypoints.
  Repository code does not add a keyword/fuzzy semantic authority.
- Request creation transitions to `WAITING_HOST` and produces no
  `skill-invocation-receipt@1`.
- A mutating Skill is stopped by the existing `WriteAuthorityGuard` before the
  Host request is exposed when write authority is absent.
- Host selection, request/result artifacts, tool receipts and credentials have
  `authority_effect=false` and grant no ChangePermit or write authority.
- `WAITING_HOST` is runtime suspension, not a new declared Skill outcome and not
  a second Workflow graph.
- TaskRun uses the existing `WAITING_EXTERNAL_RESULT` status with the distinct
  `WORKFLOW_WAITING_HOST` phase; resume returns the same TaskRun to `RUNNING`.
- Graph END still becomes TaskRun `VALIDATING`, never `COMPLETED`.
- Customer Agent Workflows still contain no merge capability and automatic merge
  remains false.

