# Pluggable Skill and Workflow Runtime

## Goal

The development Harness exposes three explicit execution modes instead of forcing users to memorize internal stage commands:

1. **OPEN** — no Skill is selected. The host may analyze freely and must not fabricate Skill invocation evidence.
2. **SKILL_BOUND** — the caller names one exact active Skill. The Harness resolves that exact registry entry, loads the canonical `SKILL.md`, records invocation evidence, and requires response binding.
3. **WORKFLOW_BOUND** — the caller names one exact registered Workflow. The Workflow owns only step order/branch/loop/interrupt semantics; each Skill/Executor/Gate keeps its own contract and authority.

No natural-language keyword router is introduced. If the caller does not explicitly name a Skill or Workflow, the route is OPEN. Unknown Skill/Workflow names fail closed instead of falling back to a similar plugin.

## Separation of responsibilities

```text
Target     = what the work is about
Skill      = how one bounded reasoning/review task is performed
Workflow   = how multiple steps are ordered
LangGraph  = orchestration runtime
Executor   = deterministic command/tool/GitHub action
Gate/Judge = deterministic acceptance decision
TaskRun    = whole-task lifecycle authority
Evidence   = durable proof
```

A Skill must not call the next Skill directly. The Workflow owns composition.

A Workflow must not become a second TaskRun, Quality, Change, merge, deployment, or business authority. Successful graph exhaustion is `FLOW_COMPLETE`, never `COMPLETED`; whole-task completion still requires the existing TaskRun Completion Contract and durable evidence.

## Canonical host gateway

Repository-local hosts use:

```bash
python3 -B skillctl.py plugin-route --mode OPEN --target customer-agent --payload "分析当前客服 Agent 的问题"
```

or:

```bash
python3 -B skillctl.py plugin-route --mode SKILL_BOUND --skill architecture-options --target customer-agent --payload "检查当前架构" --invocation-id <unique-id>
```

or:

```bash
python3 -B skillctl.py plugin-route --mode WORKFLOW_BOUND --workflow repair-and-verify --target customer-agent --payload "根据已确认问题执行修复与验证"
```

`SKILL_BOUND` creates the ordinary subject-bound Skill load receipt and still requires `skill-response-bind` before the invocation is complete.

`WORKFLOW_BOUND` does not create one fake top-level Skill receipt. Each actual Skill step must produce its own exact invocation/output evidence when the host executes that step.

## Declarative Workflow contract

Registered Workflows live under `skill-system/workflows/<name>.json` and are listed in `skill-system/registry/active-workflows.json`.

First version intentionally supports only a small control vocabulary: ordered transitions, conditional outcomes, bounded loops through `max_visits`, `human_gate`, deterministic `executor`, and deterministic `gate` steps.

Every Workflow must preserve these invariants:

```json
{
  "taskrun_is_lifecycle_authority": true,
  "workflow_runtime_authority_effect": false,
  "max_visits_are_not_success": true
}
```

Every referenced Skill is resolved exactly through `active-skills.json`. Every destination must exist or be `END`. Unreachable steps and Workflows with no reachable `END` fail validation.

## LangGraph boundary

`workflow_runtime.py` compiles a validated Workflow into `langgraph.graph.StateGraph`.

The LangGraph state may carry execution-local data such as target, artifacts, per-step outputs, visit counts, and execution history. It does **not** replace the durable TaskRun or Task Ledger.

Handlers are injected by the host/runtime adapter using exact keys such as `skill:red-baseline-repair`, `executor:quality-verify`, and `skill:adversarial-review`. A missing handler, undefined transition, or exhausted visit budget blocks the flow fail-closed.

## Pilot Workflow

`repair-and-verify` is deliberately small:

```text
red-baseline-repair
        ↓ PASS
quality-verify
   RED ↙     ↘ GREEN
repair       adversarial-review
                RED ↙   ↘ GREEN
                  repair    FLOW_COMPLETE
```

Loop budgets are safety ceilings only. Exhausting the budget is `BLOCKED`, never success.

This pilot proves the composition boundary before migrating the larger existing engineering/GitHub lifecycle.

## Migration policy

The existing `/diagnose`, `/arch`, `/agent-arch`, `/oracle`, `/repair`, `/review`, `/cert`, `/status`, and `/continue` dispatcher remains temporarily as a compatibility adapter. New integrations should prefer OPEN / explicit Skill / explicit Workflow routing.

Do not create a second implementation chain behind the legacy commands. A later migration may convert them into thin aliases to the canonical plugin/workflow gateway and then remove them after host conformance proves the new path.

Existing authorities remain unchanged: TaskRun remains whole-task lifecycle owner; Change Contract / `change-scope` remain write authority; Repair Governance remains repair authority; deterministic Quality/Judge remains verification authority; existing GitHub PR/merge/post-merge controls remain publication authority; Customer Agent runtime semantic/business authorities remain outside this development Workflow runtime.

## Next expansion

After the pilot is proven, add domain-specific Skills such as `customer-agent-audit` or `harness-audit` as independent plugins, and add larger Workflows by composition. Do not hard-code their ordering inside the Skills.

A future `customer-agent-full-dev` Workflow may compose audit, solution design, repair, deterministic verification, adversarial review, GitHub publication, exact-merge Quality, convergence, and final evidence while preserving the same authority boundaries.
