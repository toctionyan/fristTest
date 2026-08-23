# Customer Agent Starter v1

## Outcome

`customer-agent` is a verified Harness authoring package for developing a
standalone customer-service Agent. It packages project defaults, Skill
contracts, provider-neutral Workflows, and composition overlays for the six
development interactions that recur most often:

| Intent | Starter entrypoint | Write effect | Remote effect |
| --- | --- | --- | --- |
| Find all known classes of problems | `overall_audit` | none | none |
| Inspect one module or feature | `module_audit` | none | none |
| Review system architecture | `architecture_review` | none | none |
| Repair and prove locally | `repair_and_prove` | guarded workspace write | none |
| Repair, open a PR, and wait for CI | `repair_with_ci` | guarded workspace write and commit | PR creation and CI wait |
| Audit, decide, repair, test, and publish a PR | `full_dev` | guarded workspace write and commit | PR creation and CI wait |

No Starter Workflow contains `code_review.pull_request.merge`. CI green ends at
`TaskRun VALIDATING`; an explicit, separately governed merge action remains
required. Installing or verifying the Starter executes no Workflow, activates
no Provider, and grants no write authority.

## Initialize without hand-writing YAML

List the built-in packages:

```bash
python3 -B skillctl.py authoring starter-list
```

Copy the verified package into a new project-local development directory:

```bash
python3 -B skillctl.py authoring starter-init \
  --starter customer-agent \
  --output .harness/customer-agent
```

The output directory must not exist. Initialization validates the built-in
source, copies it, validates the installed bytes again, and removes a partial
installation if any check fails. Validate it later with no ChatGPT, Codex,
model, or network dependency:

```bash
python3 -B skillctl.py authoring starter-verify \
  --directory .harness/customer-agent
```

Customize `harness-project.json` after initialization. In particular, replace
the example `start`, `test`, and `quality` commands and narrow `write_scope` to
the real repository. Provider bindings can change without changing Workflow
topology. For example, `ci.run.wait` may bind to a future registered GitLab CI
adapter instead of `github.actions`.

## ChatGPT or Codex interaction contract

Natural language and slash commands are Host conveniences. The Host must map
them to one exact Starter entrypoint, show the selected Workflow and effects,
then use the canonical activation/runtime path. It must never fuzzy-select a
write Workflow.

| User interaction | Exact selection | Required input |
| --- | --- | --- |
| “检查客服 Agent 总体还有哪些问题” | `overall_audit` | project/revision scope |
| `/harness audit --module conversation-context` | `module_audit` | exact module or feature scope |
| “分析整体架构，重点检查会话状态和工具边界” | `architecture_review` | quality attributes or focus |
| `/harness repair F-142 --local` | `repair_and_prove` | finding/evidence identifier |
| `/harness repair F-142 --ci github` | `repair_with_ci` | finding, repository, base branch |
| `/harness full-dev "add complaint escalation" --ci github` | `full_dev` | bounded goal, acceptance criteria, repository |

Before a mutating run, the interaction should present a compact effect preview:

```text
Workflow: customer-agent-repair-with-ci
Writes: src/**, tests/**, docs/**, .github/workflows/**, pyproject.toml
Runs: focused tests, adversarial review, quality
Remote: create PR, wait for GitHub Actions
Merge: never automatic
```

The existing Write Authority Guard still decides whether the requested paths
and operation are authorized. Provider binding says how a capability can be
performed; it does not authorize performing it.

## What each Workflow does

### Overall and module audit

The base overall audit always runs the general `customer-agent-audit` Skill and
Quality evaluation. The bundled composition inserts
`customer-agent-standards-gate` immediately before Quality. The standards check
is additive: it cannot replace or suppress the general inspection node.

The module audit always runs `customer-agent-module-audit` and targeted tests.
Its composition inserts `customer-agent-context-audit` only on the `findings`
edge. This enriches context-related findings while leaving unrelated discovery
and the clean path unchanged.

### Repair and prove

The local repair loop is:

1. apply a bounded repair through `customer-agent-repair`;
2. run focused tests;
3. run `customer-agent-adversarial-review` against the patch;
4. loop to repair on test, review, or Quality findings;
5. end at `TaskRun VALIDATING` only after Quality is green.

### Repair with CI and full development

The CI Workflow extends the proven local loop with commit, PR creation, and an
event-driven `ci.run.wait` node. Pending CI yields `WAITING_EXTERNAL`; a wake-up
continues the same durable run. Red CI returns to repair. Green CI ends the
Graph at validation, not completion and not merge.

`full_dev` begins with general audit and architecture review. If no repair is
required it produces a validated report. If repair is required it enters the
same test/review/Quality/PR/CI loop. This keeps diagnosis and mutation evidence
in one reviewable Workflow while retaining separate authorities.

## Extend without rewriting a Skill

To add a security standard check later:

1. add one `harness-skill-contract@1` file, such as
   `skills/customer-agent-security-gate.json`;
2. declare its `extension_type` and artifact inputs/outputs;
3. add it to `starter.json.skill_contracts`;
4. add one binding to a composition overlay at an existing compatible hook;
5. run `starter-verify` and review the compiled provenance.

For example, bind it to the audit Skill's `before-validation` hook with a
`before_step` anchor on `quality`. Multiple extensions on the same anchor are
ordered by numeric `order`, then binding ID. The base Skill and base Workflow
remain unchanged. A new check therefore does not require rewriting the original
Skill, and it cannot silently change topology merely by naming a hook.

If the desired insertion point does not exist, evolve the host Skill contract
once by adding a named extension point, then attach present and future
extensions through overlays. Extension contracts prove compatibility;
execution still requires a real Host invocation receipt.

## Standalone application boundary

The `.harness` directory is development control-plane input. The customer Agent
application must keep its runtime source, tests, package manifest, deployment
configuration, and ordinary dependencies outside the Harness runtime. After
development, the application can be copied, built, deployed, and run without
ChatGPT, Codex, the Starter, or Harness services.

Keeping `.harness` in version control is optional but useful for later
maintenance. If ChatGPT is unavailable, local developers and CI can still edit
the open JSON/YAML declarations and run the deterministic validation/compiler
CLI. Removing `.harness` removes development automation, not application
functionality.

## Current boundary

Starter v1 supplies verified authoring material and a safe initializer. It does
not register the installed declarations into the repository-wide runtime
registries or start a TaskRun. That remains a separate activation step so
installation cannot acquire operational authority. A later integration may
offer a natural-language Composer or `/harness` command router, but its output
must resolve to these exact declarations and pass the same deterministic
verification before activation.
