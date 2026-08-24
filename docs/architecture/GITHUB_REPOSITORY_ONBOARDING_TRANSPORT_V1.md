# GitHub Repository Onboarding Transport v1

## Purpose

`skillctl repository-onboarding` closes the transport gap in the existing
STAGE-5 / WP-08 repository admission process. It collects current GitHub
metadata, writes a digest-sealed names-only artifact, and hands that metadata to
`scripts/repository_onboarding_preflight.py` without changing that evaluator.

The transport is evidence acquisition, not readiness, release, completion, or
merge authority. A successful preflight does not start WP-08 and does not prove
production credentials, live model calls, PostgreSQL, browser execution, or a
production release.

## Read-only endpoint contract

For one configured `owner/name`, the transport permits only HTTPS `GET` calls
under the configured GitHub API origin:

| Endpoint | Retained evidence |
| --- | --- |
| `/repos/{owner}/{name}` | exact full name, default branch, visibility, size-derived empty state, caller permission booleans |
| `/repos/{owner}/{name}/branches/main/protection` | whether GitHub returned the protected-main resource |
| `/repos/{owner}/{name}/environments` | unique Environment names |
| `/repos/{owner}/{name}/environments/production-certification/secrets` | unique secret names only |
| `/repos/{owner}/{name}/contents/PHASE_CANDIDATE_MANIFEST.json?ref={branch}` | exact decoded candidate-manifest bytes and SHA-256 |
| `/repos/{owner}/{name}/contents/release/MANIFEST.json?ref={branch}` | exact decoded workspace, version, skill version, and phase identity |

Pagination is limited to the same API origin and exact repository endpoint,
with only positive `page` and fixed `per_page=100` parameters. Cross-origin,
cross-repository, cyclic, malformed, or excessive pagination fails closed.

The implementation exposes no GitHub mutation method. It cannot configure
branch protection, create an Environment, write a secret, dispatch a workflow,
open or merge a pull request, deploy, release, or set `production_closed`.

## Secret boundary

The caller supplies an uppercase environment-variable name, `GITHUB_TOKEN` by
default. Its value is used only in the in-memory authorization header and is
never accepted as a CLI argument, printed, persisted, hashed, or included in an
exception. The GitHub Environment secret-list endpoint exposes metadata; the
collector retains only each `name`. Secret values are never requested or
validated. Real value and endpoint validation remains inside the protected
`production-certification` Job.

## Artifact contract

The default artifact is stored below:

```text
.harness/runtime/repository-onboarding/{owner}--{name}.json
```

An explicit `--output` must remain inside `--workspace-root`. The artifact has a
closed top-level and metadata schema, immutable false authority flags, and a
canonical JSON SHA-256 seal. The CLI reloads and validates the artifact against
the in-memory seal from the exact collection after the atomic write and before
using it. A mismatched repository/API identity,
unexpected field, changed authority flag, malformed digest, or content change
invalidates the artifact.

The three authority flags are always:

```json
{
  "authority_effect": false,
  "deploy_allowed": false,
  "production_closed": false
}
```

## Operator commands

Collect current metadata without making a readiness claim:

```bash
GITHUB_TOKEN=... python3 -B skillctl.py repository-onboarding collect \
  --repository owner/name
```

Collect, seal, reload, and pass the metadata to the existing evaluator:

```bash
GITHUB_TOKEN=... python3 -B skillctl.py repository-onboarding preflight \
  --repository owner/name
```

A public repository remains blocked unless the operator adds the existing
explicit decision:

```bash
GITHUB_TOKEN=... python3 -B skillctl.py repository-onboarding preflight \
  --repository owner/name \
  --allow-public
```

`PASS` means repository admission metadata satisfies the existing preflight at
that moment. `BLOCKED_BY_ENVIRONMENT` means explicit configuration is still
missing. Transport authorization, network, identity, content, or pagination
ambiguity returns `BLOCKED` and cannot be converted into a negative claim such
as “protection is absent.” Only an exact `404` on the branch-protection resource
is represented as `main: false`.

## Authority handoff

The existing evaluator remains the sole owner of repository readiness. The
protected-environment preflight remains the owner of real credential and
endpoint checks. The WP-08 release coordinator remains the owner of execution
authorization. Quality, TaskRun, write, completion, release, deployment, merge,
and production-closure authorities are unchanged.
