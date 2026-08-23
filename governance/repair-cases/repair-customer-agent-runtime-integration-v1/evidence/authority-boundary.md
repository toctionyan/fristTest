# Authority-boundary evidence

Single-owner boundaries remain intact:

- canonical declaration and graph parsing: existing `WorkflowSpec` and graph contract;
- capability and Provider binding: existing Capability Resolver;
- step execution: existing `WorkflowAdapterDispatcher`;
- Skill proof: existing receipt format after a real Host call;
- write permission: injected existing `WriteAuthorityGuard` before mutating Skill
  or Provider execution;
- routing and resume: existing LangGraph runtime with a durable saver;
- lifecycle and completion: existing TaskRun and TaskRun bridge.

The new registration is a revalidated provenance pointer. It is not a semantic
cache, registry writer, execution owner, write grant, completion verdict, or
merge authority.
