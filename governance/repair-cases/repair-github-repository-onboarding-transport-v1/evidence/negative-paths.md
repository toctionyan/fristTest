# Negative paths

- The transport exposes only `request(method="GET", ...)`; the standard-library
  client rejects every other method and disables redirects before credentials
  can be forwarded to another origin.
- Endpoint paths are constructed from the configured validated `owner/name`;
  API payloads cannot choose a repository, origin, branch-protection target,
  Environment, secret endpoint, output path, or policy.
- The token is accepted only through a stable uppercase environment-variable
  name. Its value is never a CLI argument, result field, artifact field, digest
  input, or exception string.
- Environment secret values are neither requested nor retained. Only unique
  names returned by the metadata endpoint enter the artifact.
- `collect` returns `COLLECTED`, never onboarding `PASS`. `preflight` delegates
  PASS/BLOCKED/FAIL to `scripts/repository_onboarding_preflight.py` unchanged.
- Output must remain below the selected workspace and is atomically replaced
  with mode `0600`; symlink targets are rejected.
- No workflow dispatch, repository mutation, protected-environment execution,
  write authorization, merge, release, deployment, WP-08 start, or production
  closure path exists.
- Every result and artifact keeps `authority_effect=false`,
  `deploy_allowed=false`, and `production_closed=false`.
