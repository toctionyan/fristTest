# Authority boundary

| Decision | Sole authority after this change |
| --- | --- |
| Installed Skill identity | verified Starter registration and canonical `SKILL.md` digest |
| Skill step dispatch | existing `WorkflowAdapterDispatcher` |
| Canonical Skill execution receipt | existing `CanonicalSkillInvocationAdapter` |
| Capability/Provider binding | existing Capability Resolver |
| Product mutation permission | existing ChangePermit and `WriteAuthorityGuard` |
| Host request/result integrity | `DurableHostSkillBridge` evidence boundary |
| Workflow routing and suspension | existing LangGraph runtime |
| Lifecycle and completion | existing TaskRun and completion policy |
| Quality acceptance | existing Quality/Judge boundary |
| Merge | independent explicit MergeAuthority; absent from Customer Agent Starter |

`WAITING_HOST` records that an outcome does not yet exist. It cannot authorize a
write, declare Quality green, select a Provider, merge a PR or complete a TaskRun.
Natural-language selection is a Host proposal reduced to one existing entrypoint;
it cannot expand the registered execution graph.

