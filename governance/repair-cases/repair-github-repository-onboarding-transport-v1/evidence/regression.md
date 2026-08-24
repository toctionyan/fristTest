# Regression

The final candidate passed `skill-control-plane` in the locked project
environment:

- `skill-static`: PASS
- `skill-unit`: PASS; 880 tests
- `skill-host-integration`: PASS
- `skill-security`: PASS; 7 tests
- `project-compatibility-smoke`: PASS; 671 protected product files, zero drift

The same run verified the governed-repair architecture, mutation proof,
environment contract, path-policy projection, task ledger, local-first
governance, registry, package, and strict Host conformance checks. Durable
machine evidence is recorded under
`.quality/profile-runs/skill-control-plane.json` with `status=PASS`.

The existing evaluator is imported unchanged and its ready/private and
public-approval behaviors pass in the new focused integration test. The older
standalone pytest fixture in `test_repository_onboarding_preflight.py` still
expects `PHASE_CANDIDATE_MANIFEST.json`, which B28 deliberately removed in the
same historical commit that added that fixture. This pre-existing noncanonical
fixture defect is not changed or hidden by this repair; the canonical
`skill-control-plane` remains green and the new test builds an explicit
synthetic candidate workspace for evaluator compatibility.
