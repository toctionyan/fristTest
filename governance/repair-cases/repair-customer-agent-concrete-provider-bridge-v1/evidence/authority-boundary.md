# Authority boundary

- Capability Registry/Resolver still select Provider identity.
- WorkflowAdapterDispatcher still owns step dispatch.
- Existing WriteAuthorityGuard runs before mutating Skill or Provider effects.
- Structured workspace scope is trusted embedding policy, not request authority.
- Local Git and GitHub credentials enable effects but grant no ChangePermit,
  merge, Quality, or completion authority.
- RequestDataflowProviderAdapter only materializes declared prior-step values.
- GitHub exact-head read proves remote identity but cannot merge.
- Customer Agent Workflows still contain no merge capability.
- TaskRun remains lifecycle/completion authority; Graph END remains
  `VALIDATING`.
