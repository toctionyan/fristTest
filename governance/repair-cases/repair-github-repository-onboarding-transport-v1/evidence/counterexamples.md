# Counterexamples

PASS evidence is exercised by
`test_github_repository_onboarding_transport.py`:

- a repository payload whose `full_name` differs from the configured exact
  target is rejected before any identity evidence is emitted;
- malformed or size-inconsistent remote base64 content cannot produce a
  workspace marker;
- HTTP 401/403 and transport ambiguity fail closed and are not interpreted as
  missing configuration;
- only an exact branch-protection 404 becomes `main: false`;
- a pagination URL that changes API origin, repository path, query shape, page
  size, or repeats a page is rejected;
- changed artifact content with an old in-memory collection seal is rejected;
- unexpected artifact fields, widened authority flags, invalid permissions,
  unsorted or duplicate names, and invalid workspace-marker shapes fail schema
  validation;
- public visibility remains `BLOCKED_BY_ENVIRONMENT` unless `--allow-public`
  is present on that exact preflight invocation;
- a secret-list payload field named `value` is discarded and cannot appear in
  the persisted artifact.
