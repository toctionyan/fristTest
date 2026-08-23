# Harness Authoring Core v1

## Purpose

Harness Authoring Core is the portable authoring boundary for project-specific
development assistance. It turns small, reviewable JSON or YAML declarations
into the existing Harness runtime contract. ChatGPT and Codex may compose these
files from natural language, but neither product is required to validate,
compile, explain, version, or maintain them.

The boundary deliberately does not execute a Workflow. Compilation ends at
`workflow_registry.parse_workflow_spec`, which also validates the existing
declarative graph. Activation, capability resolution, LangGraph execution,
provider adapters, durable evidence, and TaskRun completion remain downstream
owners.

## Authority flow

1. `harness-project@1` describes project commands, bounded write scope,
   provider preferences, and default Workflow identifiers.
2. `harness-skill-contract@1` describes one Skill's inputs, outputs, execution
   mode, required capabilities, and supported extension points.
3. `harness-workflow@1` describes provider-neutral orchestration.
4. Authoring Core safely loads and strictly validates the declaration.
5. Workflow compilation calls the existing `WorkflowSpec` parser and graph
   validator. It does not interpret a second graph language.
6. `Graph END` means `TaskRun VALIDATING`. Only TaskRun and its completion
   policy may declare overall completion.

The public JSON Schemas live in `skill-system/schemas/`. Runtime semantic
validation is intentionally stricter where JSON Schema alone is insufficient,
including graph reachability, provider neutrality, capability binding, bounded
write scope, and completion authority.

## Minimal project setup

Generate the file instead of hand-writing it:

```bash
python3 -B skillctl.py authoring project-init \
  --output .harness/project.yaml \
  --project-id customer-agent \
  --project-type agent \
  --command 'test=python -m unittest' \
  --command 'lint=ruff check .' \
  --write-scope 'src/**' \
  --write-scope 'tests/**' \
  --provider 'quality.evaluate=local.process' \
  --default 'audit_workflow=audit-customer-agent'
```

The generated declaration belongs to the project and can be versioned with it.
It records how the Harness may work on the project; it is not imported by the
project's production runtime. The resulting customer-agent application can be
built, deployed, and run after the Harness is removed.

Validate one or more files without a model or network:

```bash
python3 -B skillctl.py authoring validate \
  .harness/project.yaml \
  .harness/skills/customer-agent-rules.yaml \
  .harness/workflows/audit.yaml
```

JSON is always supported. YAML uses `yaml.safe_load`; environments without
PyYAML receive an explicit error and can use `.json` declarations instead.
Unknown keys and unsupported schema versions fail closed.

## Skill extension contract

A project Skill can be small and composable:

```yaml
schema: harness-skill-contract@1
skill: customer-agent-policy-audit
version: 1.0.0
mode: read_only
inputs: [repository-snapshot@1]
capabilities: [vcs.diff.read]
outputs: [finding-set@1]
extension_type: audit-lens
extension_points:
  before-analysis: [context-provider]
  finding-enrichment: [finding-enricher]
  before-validation: [gate]
```

An extension point is a compatibility declaration, not permission to execute.
A later Workflow revision may insert another compatible Skill at that point
without rewriting the original Skill. The Workflow still records the exact
order, and each invoked Skill must produce real Host evidence. A mutating Skill
must declare `workspace.write`; runtime write guards still decide whether the
operation is authorized.

This separation supports the three common customer-agent activities:

| Activity | Typical reusable Workflow | Project-specific extension |
|---|---|---|
| Find system or module problems | audit Workflow | architecture rules, domain invariants, module audit lens |
| Repair and test findings | repair-and-prove Workflow | repair procedure, project test commands, validation gate |
| Review overall architecture | architecture review Workflow | bounded-context rules, dependency policies, quality attributes |

A standards Skill adds another evidence-producing lens; it must not suppress
unrelated findings. The audit Workflow should fan findings into a common
`finding-set@1`, deduplicate them, and run coverage/completeness gates after all
lenses. Extensions should enrich or gate evidence, not replace the general
inspection step unless the Workflow source says so explicitly.

## Workflow example

```yaml
schema: harness-workflow@1
id: audit-customer-agent
version: 1.0.0
request_class: DIAGNOSIS
skills: [architecture-options, customer-agent-policy-audit]
mode: READ_ONLY
status_first: false
deterministic_response: false
write_governed: false
requirements:
  capabilities:
    required: [quality.evaluate]
    optional: [vcs.diff.read]
graph:
  start: architecture
  steps:
    architecture:
      type: skill
      use: architecture-options
      routes: {issues: policy, clean: policy}
    policy:
      type: skill
      use: customer-agent-policy-audit
      routes: {checked: quality}
    quality:
      type: gate
      use: quality.evaluate
      routes: {pass: END, fail: BLOCKED_UNRECOVERABLE}
completion:
  transition_to: VALIDATING
  policy: audit-report-produced@1
  authority: TaskRun
```

Compile or inspect it without executing providers:

```bash
python3 -B skillctl.py authoring compile \
  --workflow .harness/workflows/audit.yaml \
  --output .harness/compiled/audit.json

python3 -B skillctl.py authoring explain \
  --workflow .harness/workflows/audit.yaml
```

The compiled artifact is `compiled-workflow-plan@1`. Its source digest makes
review and caching deterministic. Provider selection is intentionally absent:
the same Workflow can bind local process, GitHub Actions, GitLab CI, or another
registered adapter during activation without changing graph semantics.

## Host independence and maintenance

The stable maintenance surface is the open declaration plus CLI, not a ChatGPT
conversation. A different editor, CI job, local script, or future Host can read
and update the same files. Host adapters may offer natural-language actions such
as "initialize this repository", "add the security audit lens before quality",
or "explain why this Workflow can write"; their output must pass the same local
compiler before activation.

Removing ChatGPT, Codex, or the entire Harness does not add a runtime dependency
to the developed project. Only intentionally generated application source,
tests, build files, and ordinary project dependencies remain.
