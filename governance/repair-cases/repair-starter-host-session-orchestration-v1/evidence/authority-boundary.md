# Authority-boundary evidence

`StarterHostOrchestrator` owns only the legal order and correlation of a Host
interaction session.

Existing owners remain unchanged:

- ChatGPT/Codex: semantic interpretation among verified candidates;
- `resolve_starter_host_selection`: exact candidate and mutation-confirmation
  validation;
- Capability Resolver: Provider binding;
- `WorkflowAdapterDispatcher`: step dispatch;
- injected `WriteAuthorityGuard`: permission to perform mutating effects;
- `DurableHostSkillBridge`: exact per-Skill request/result and tool evidence;
- `CanonicalSkillInvocationAdapter`: canonical Skill invocation receipt;
- `StarterWorkflowRuntime` / LangGraph: Workflow execution;
- TaskRun: lifecycle and completion;
- deterministic Provider/Quality integrations: their own effect evidence;
- user/repository policy: merge authorization.

Every session and next-action policy states `authority_effect=false`,
`automatic_merge=false`, and `completion_authority=TaskRun`. Selection and
confirmation explicitly grant no write authority. Graph END is accepted only
after the existing TaskRun bridge projects `VALIDATING`.

