# Regression

The final candidate passed `skill-control-plane` in the locked project
environment:

- `skill-static`: PASS
- `skill-unit`: PASS; 880 tests
- `skill-host-integration`: PASS
- `skill-security`: PASS; 7 tests
- `project-compatibility-smoke`: PASS; 671 protected product files, zero drift

The same run also verified the governed-repair architecture, mutation proof,
environment contract, path-policy projection, task ledger, local-first
governance, registry, package, and Host conformance checks.

Durable machine evidence is recorded by the profile runner under
`.quality/profile-runs/skill-control-plane.json` with `status=PASS`.
