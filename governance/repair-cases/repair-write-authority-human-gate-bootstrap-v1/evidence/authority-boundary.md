# Authority boundary

PASS.

- `repair_governance` remains the only validator/interpreter for ChangePermit chains and path decisions.
- `ChangePermitWriteAuthorityGuard` is a protocol adapter: it issues no permit and evaluates every mutation afresh.
- `WorkflowAdapterDispatcher` remains the single pre-effect guard call site.
- Provider adapters remain the effect implementations and keep their own exact request/precondition checks.
- Generic write authority explicitly has no merge authority.
- `DurableHumanGateAdapter` owns only gate/decision representation; Host ordering, LangGraph resume, and TaskRun lifecycle remain with their existing owners.
- Human decision records carry no authority effect.
- TaskRun remains the only completion authority; no code can turn CI/Quality/Graph END into overall completion.
- No automatic merge, release, deploy, or production-close adapter was introduced.
