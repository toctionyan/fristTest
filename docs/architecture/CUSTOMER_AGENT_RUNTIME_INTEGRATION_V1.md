# Customer Agent Starter Runtime Integration v1

## Outcome

An installed Harness Starter can now run without copying its declarations into
the repository-wide Skill or Workflow registries. Runtime integration has four
separate operations:

1. verify and register project-local Starter provenance;
2. route one exact entrypoint and preview effects;
3. activate the canonical derived `WorkflowSpec` through Capability Resolver;
4. execute through the existing Dispatcher, LangGraph runtime, durable saver,
   TaskRun bridge, and external-event resume path.

None of these operations changes completion authority. Registration and routing
have `authority_effect: false`. Capability binding does not grant write access.
Graph END is projected to `TaskRun VALIDATING`, never `COMPLETED`.

## Registration boundary

`harness-starter-runtime-registration@1` records:

- the bounded project-relative Starter directory;
- Starter ID, version, and whole-package digest;
- exact public entrypoint to Workflow identity and canonical Workflow digest;
- exact project-relative `SKILL.md` path and digest for every Skill contract;
- project command, Provider preference, and write-scope declarations;
- closed policy flags denying execution, write, completion, and merge authority.

The registration file must live inside the project workspace but outside the
Starter package. Loading it recomputes the registration fingerprint, verifies
the entire Starter inventory, recompiles base and composed Workflows through the
canonical parser, and compares every seal. Source or registration drift fails
before activation.

The registration is provenance only. It does not copy installed Skills into
`active-skills.json` or Workflows into `dev-workflows.json`, so two projects can
install different Starter versions without mutating global runtime identity.

## Exact interaction grammar

The built-in strict router recognizes exactly these forms:

```text
/harness audit all [focus]
/harness audit module <scope>
/harness architecture <focus>
/harness repair <finding> --local
/harness repair <finding> --ci
/harness full-dev <goal> --ci
```

Reserved execution flags cannot appear inside the payload. Missing or combined
write flags fail closed. Free-form natural language may be used by a Host to
propose an entrypoint, but a write Workflow must still be confirmed as one exact
entrypoint before this router is called.

The route includes the selected Workflow, activation projection, bounded write
scope, mutating capabilities, integration effects, and `automatic_merge:
false`. A read route reports `START_TASKRUN`; a mutating route reports
`REQUIRE_WRITE_AUTHORITY`.

## Execution embedding

`StarterWorkflowRuntime` receives already-created runtime dependencies:

- `ResolvedStarterEntrypoint`;
- a real `SkillHostAdapter`;
- `ProviderAdapterRegistry` with adapters for activated providers;
- a durable LangGraph checkpointer;
- the existing nonterminal `TaskRunStore`;
- the current workspace fingerprint;
- the existing Write Authority Guard for mutating routes;
- an optional Human Gate adapter.

The runtime does not create a parallel TaskRun or invent a second dispatcher.
It constructs `CanonicalSkillInvocationAdapter` with the package-bound
`SKILL.md` map. The Host must actually execute the Skill before the adapter can
write `skill-invocation-receipt@1`. Mutating Skill contracts are checked against
the same activated capability bindings and existing write Guard before Host
execution.

`start()` records `WORKFLOW_RUNTIME_STARTED`, invokes the exact graph under a
stable durable thread ID, and projects the resulting state through
`checkpoint_workflow_state()`. `resume()` accepts only a state owned by the same
TaskRun and Workflow. It requires durable event/decision evidence, records
`WORKFLOW_RUNTIME_RESUMED`, and re-enters the declared wait or Human Gate node.

## Failure-closed behavior

Execution is blocked when any of these is absent or inconsistent:

- package, Skill, Workflow, or registration digest;
- exact entrypoint identity;
- required Provider capability binding;
- executable adapter for an activated Provider;
- durable checkpointer;
- matching nonterminal TaskRun;
- real Skill Host output and evidence;
- write authority for a mutating Skill or Provider capability;
- external wait handle or resume evidence.

Provider coverage and execution coverage are separate. A project declaration
can name a known Provider so activation is reviewable, while execution still
fails closed until the corresponding real adapter is injected. No placeholder
result is treated as successful execution.

## Standalone product boundary

The developed customer Agent does not import the Harness Starter runtime. The
Starter, registration, Skill receipts, and TaskRun records are development
control-plane artifacts. Product source, tests, packaging, deployment, and
ordinary application dependencies remain independently runnable after `.harness`
and the Harness tooling are removed.
