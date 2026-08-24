# Focused tests

PASS.

Command:

```text
services/agent-service/.venv/bin/python -m pytest -q \
  skill-system/tests/test_repository_onboarding_preflight.py \
  skill-system/tests/test_github_repository_onboarding_transport.py
```

Result: `19 passed in 0.13s`.

The focused suite proves current B28 identity without the retired phase
manifest, exact local/remote release-manifest digest equality, normal `.git`
checkout handling, sealed names-only metadata, explicit public approval, and
unchanged fail-closed behavior.
