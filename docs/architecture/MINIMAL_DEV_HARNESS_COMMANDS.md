# Minimal Development Harness Commands

## Purpose

This repository uses a deliberately small explicit command layer for development assistance. It is not a second semantic router and it does not try to classify arbitrary natural language.

The first command token selects a fixed development flow. Everything after the command remains normal user payload and must be consumed by the selected Skill(s).

Example:

```text
/arch

ContextStore feels too broad.
Do not introduce a second context owner.
Compare conservative, evolutionary, and redesign options.
Do not modify code yet.
```

`/arch` is routing only. The remaining text is the architecture request.

## Fixed command map

| Command | Fixed request class | Required canonical Skill(s) | Mode |
|---|---|---|---|
| `/status` | `STATUS_QUERY` | `task-execution-status` | authoritative status projection |
| `/continue` | `STATUS_QUERY` | `task-execution-status` | status first, then continue only if allowed |
| `/diagnose` | `DIAGNOSIS` | `product-code-governance` | read-only diagnosis |
| `/arch` | `DESIGN` | `architecture-options` | read-only architecture comparison |
| `/agent-arch` | `DESIGN` | `architecture-options`, `customer-agent-architecture` | read-only customer-agent architecture |
| `/oracle` | `ORACLE_REVIEW` | `oracle-review` | read-only Oracle/Claim/Requirement review |
| `/repair` | `REPAIR` | `product-code-governance`, `red-baseline-repair` | governed writable repair |
| `/review` | `ADVERSARIAL_REVIEW` | `adversarial-review` | read-only adversarial review |
| `/cert` | `CERTIFICATION` | `release-certification` | read-only certification |

`change-scope` is intentionally not exposed as a normal user command. The existing writable Host Hook remains the mutation gate and must still require the active `CHANGE_SCOPE` invocation evidence before repository writes.

## Canonical dispatcher

Supported repository hosts use:

```bash
python3 -B skillctl.py dev-command \
  --command /arch \
  --payload "<free-form user text>" \
  --invocation-prefix <unique-id>
```

The dispatcher:

1. validates the exact explicit command;
2. maps it to the fixed Skill set above;
3. loads each canonical `SKILL.md`;
4. writes subject-bound Skill load receipts using the existing multi-Skill active receipt index;
5. returns the original user payload unchanged together with the loaded Skill contexts;
6. fails closed on unknown commands or missing required Skills.

It does **not** use keywords from the payload to reroute the command.

The full message can also be parsed as one value:

```bash
python3 -B skillctl.py dev-command \
  --text $'/arch\nContextStore feels too broad.\nDo not modify code.' \
  --invocation-prefix <unique-id>
```

The first non-whitespace token is the command; the rest is payload.

## Status and continuation

`/status` and `/continue` always select `task-execution-status`. Their route is incomplete until the host runs the canonical status projector against the authoritative TaskRun:

```bash
python3 -B skillctl.py task-status-project \
  --task-run <authoritative-task-run.json> \
  --invocation-id <unique-id>
```

The host must use the returned `execution-progress@1` / `rendered_text`. It may not synthesize whole-task completion from a PR, merge, latest workflow, or GitHub status alone.

`/continue` means **status first**. A healthy external run must not be duplicated; a completed TaskRun must not be restarted; a genuine Human Gate may stop and request user action.

## Payload and auxiliary context

The explicit command never replaces normal conversation. The user may provide:

- goals;
- current observations;
- constraints;
- preferred direction;
- requested output shape;
- references to a prior diagnosis, TaskRun, Change Contract, candidate, or file.

Hosts may pass durable references through repeated `--context-ref` values and may bind the route receipt with `--task-id` and/or `--change-id` when those identities are already authoritative.

The dispatcher does not reinterpret those references or infer a nearest PR/TaskRun.

## Fail-closed behavior

For an explicit command:

- do not silently select a different Skill because it appears similar;
- do not bypass the dispatcher and answer from an ad-hoc GitHub lookup when the required route failed;
- do not treat a load receipt as final status evidence; `/status` still requires `task-status-project`;
- do not let `/repair` bypass the existing Change Contract / ChangePermit / `change-scope` write guard;
- do not turn read-only commands into writes.

## Scope boundary

This command layer exists only to make the developer Harness predictable while building the customer Agent. It is not part of the customer Agent product runtime and must not become a second business, semantic, transaction, Quality, TaskRun, or completion authority.

Repository-local Codex/Claude-style hosts can consume this CLI. An external ChatGPT product conversation using a GitHub Connector is still outside repository-local Hook interception unless a product-level adapter explicitly invokes the canonical repository entrypoint; do not fabricate repository invocation evidence for that case.
