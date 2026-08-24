# Authority boundary review

PASS.

- `GitHubRepositoryOnboardingTransport` owns only current read-only metadata
  acquisition, sanitization, repository binding, artifact sealing, and exact
  reload verification.
- `scripts/repository_onboarding_preflight.py` remains the sole repository
  readiness decision owner and is imported and invoked without modification.
- GitHub remains authoritative for repository identity, caller permissions,
  protected main, Environment names, and names-only secret metadata.
- The protected-environment preflight remains authoritative for real secret
  values, provider endpoints, and production execution dependencies.
- The WP-08 release coordinator remains the sole owner of ReleaseRun
  authorization and certification dispatch.
- TaskRun, Quality, write, completion, release, deployment, merge, and
  production-closure authorities are unchanged.
- Customer Agent service, web, contracts, product dependencies, workflows, and
  existing onboarding policy are outside the ChangePermit and unchanged.
