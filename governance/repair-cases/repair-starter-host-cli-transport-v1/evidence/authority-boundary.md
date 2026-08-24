# Authority boundary

The new authority is limited to closed wire validation and fixed method dispatch.
It has no direct session writer: every transition calls the existing
`StarterHostOrchestrator`.

Unchanged owners:

- ChatGPT/Codex: semantic interpretation among verified candidates;
- Starter selection resolver: exact candidate and confirmation validation;
- StarterHostOrchestrator: durable session order and compare-and-swap revision;
- StarterWorkflowRuntime/LangGraph: Workflow execution;
- DurableHostSkillBridge: real Skill request/result and invocation receipt;
- Provider adapters: deterministic/local/external effects;
- existing WriteAuthorityGuard: mutating dispatch permission;
- TaskRun and completion policy: lifecycle and completion;
- explicit operator: trusted factory/bootstrap selection and PR merge approval.

The response policy fixes `transport_is_authority=false`,
`semantic_routing=false`, `write_authority_granted=false`,
`completion_authority=TaskRun`, `automatic_merge=false`, and
`authority_effect=false`. The customer application imports none of the new
control-plane modules.
