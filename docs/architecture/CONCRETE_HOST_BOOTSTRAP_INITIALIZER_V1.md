# Concrete Host Bootstrap / Project Initializer v1

## Outcome

One command installs a verified Starter into an existing project, seals its exact runtime registration, and generates the closed `.harness/host/bootstrap.json` consumed by the built-in trusted factory:

```bash
python3 -B skillctl.py authoring host-init \
  --project-workspace /path/to/customer-agent
```

The generated application remains independently runnable. `.harness/**` is development-control-plane state; the initializer does not change application source, its entrypoint, packaging, or production dependencies.

## Generated files

```text
<project>/.harness/
  host-init.lock                         concurrent initializer serialization
  starters/customer-agent/             verified immutable Starter copy
  runtime/starter-registration.json    sealed Starter/workflow/skill identity
  host/bootstrap.json                  concrete-host-bootstrap@2
  runtime/langgraph-checkpoints.sqlite3  created on first Host command
  runtime/host-sessions/               durable interaction sessions
  runtime/authority-checks/            exact ChangePermit checks
  runtime/human-gates/                 durable gate contracts
  runtime/human-decisions/             explicit sealed decisions
  taskruns/                             canonical TaskRun state
```

Existing targets are never overwritten. A failed initialization removes only artifacts created by that attempt.

## Running from ChatGPT or Codex

The initializer returns the two operator settings. They are outside the untrusted command envelope:

```bash
export HARNESS_HOST_FACTORY=concrete_host_bootstrap:build_orchestrator
export HARNESS_HOST_BOOTSTRAP=/path/to/customer-agent/.harness/host/bootstrap.json
python3 -B skillctl.py host --request open-command.json --pretty
```

`OPEN` carries natural language. ChatGPT/Codex chooses one exact verified candidate, then sends `SELECT`, and mutating candidates also require `CONFIRM`. Subsequent commands follow the durable `next_action`; callers never infer or skip a revision.

## Project commands and extension

`test.run` and `quality.evaluate` select only command names sealed in the Starter's `harness-project@1` declaration. The concrete runner parses the fixed command with `shlex` and invokes it without a shell. Workflow or model data cannot submit an arbitrary command.

Defaults use the Starter command names `test` and `quality`. A Starter with other command names can be initialized explicitly:

```bash
python3 -B skillctl.py authoring host-init \
  --project-workspace /path/to/project \
  --starter my-verified-starter \
  --test-profile unit \
  --quality-profile release
```

New Skills and Workflows are added to a verified Starter package and its declarations. They are not inserted by changing the bootstrap parser. New Provider implementations extend the existing Provider registry assembly; Provider selection remains in Capability activation, not in natural-language routing.

## GitHub integration

Local-only audit and test assembly requires no GitHub token. Configure GitHub only when the project uses pull-request/CI capabilities:

```bash
python3 -B skillctl.py authoring host-init \
  --project-workspace /path/to/project \
  --github-repository owner/repository \
  --github-token-environment-variable GITHUB_TOKEN
```

Only the environment-variable name is written. The token value is loaded at factory runtime and is never emitted. If integration is configured and the variable is missing, bootstrap blocks; it does not silently downgrade or choose another provider.

## Authority boundaries

The generated bootstrap has a fixed non-authorizing policy:

- initialization and configuration do not grant write authority;
- the built-in factory injects a `ChangePermitWriteAuthorityGuard`, which reloads the project's existing active ChangePermit for every mutating dispatch and permits only exact requested paths;
- `workspace.write`, `vcs.commit.create`, and `code_review.pull_request.create` must expose their exact paths before the effect; missing or out-of-permit paths block;
- the generic guard categorically cannot authorize `code_review.pull_request.merge`;
- a durable Human Gate adapter persists the exact gate and accepts only a sealed decision artifact for the same TaskRun, Workflow, step, routes, and gate digest;
- Human Gate decisions add no write, completion, release, merge, or production authority;
- LangGraph `END` remains `TaskRun VALIDATING`;
- TaskRun remains the only completion authority;
- CI green and Quality green remain evidence, not overall completion.

Normal governed repair is automatic after the target project already has an implementing active Change Contract and valid ChangePermit and the `START` target carries their exact `change_id` and `permit_digest`. Initialization, natural-language routing, `SELECT`, and `CONFIRM` cannot manufacture those facts. True policy choices remain explicit Human Gates. See [Write Authority / Human Gate Bootstrap v1](WRITE_AUTHORITY_HUMAN_GATE_BOOTSTRAP_V1.md).
