# Authority boundary review

PASS.

- GitHub ingress owns only provider authentication, closed payload validation,
  exact evidence persistence, and delivery replay.
- `DurableExternalEventScheduler` remains the sole owner of normalized external
  event correlation, admission, and wake delivery.
- `StarterHostOrchestrator` remains the sole Host session coordinator.
- LangGraph remains the Workflow graph executor.
- TaskRun remains the completion authority; Graph END still reaches
  `VALIDATING` rather than completing a task.
- Existing adapters remain the only effect boundary.
- The transport has no write-authority guard, completion controller, release
  writer, merge adapter, or merge operation.
- Provider evidence and receipts explicitly seal `authority_effect=false`,
  `completion_authority_changed=false`, and `merge_authority_changed=false`.
- Customer Agent product source, web code, service dependencies, and contracts
  are outside the permit and unchanged.
