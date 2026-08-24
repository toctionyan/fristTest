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
  host/bootstrap.json                  concrete-host-bootstrap@1
  runtime/langgraph-checkpoints.sqlite3  created on first Host command
  runtime/host-sessions/               durable interaction sessions
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
- the built-in factory injects no `WriteAuthorityGuard`, so mutating Skill/Provider dispatch remains blocked until a separately governed existing Guard is integrated;
- the factory adds no Human Gate decision, completion decision, release authority, or merge adapter;
- LangGraph `END` remains `TaskRun VALIDATING`;
- TaskRun remains the only completion authority;
- CI green and Quality green remain evidence, not overall completion.

This phase makes read-only audits and the concrete Host/runtime wiring operational. Fully automated repair remains intentionally dependent on a real governed write-authority implementation rather than an initializer-generated allow-all substitute.
