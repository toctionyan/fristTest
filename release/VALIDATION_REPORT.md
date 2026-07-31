# B17j Validation Report

- Product/workspace version: `20.6.1`
- Architecture Skill version: `6.3.0`
- Governance phase: `V20.17 B17j`
- Status: `PHASE_CANDIDATE_ENVIRONMENT_EXECUTION_PENDING`
- `production_closed`: `false`

## Real GitHub red baseline

A zero-file-difference draft PR ran the restored `quality` Workflow. Skill package/static, 46 Skill unit tests, host integration and 7 security tests all passed. The only failure was `project-compatibility-smoke`, which compared the current approved product candidate against a historical Skill-only product baseline and prevented all project Quality jobs from starting.

## Repair

The project `quality` Workflow now runs the four Skill self-validation profiles directly. `project-compatibility-smoke` remains in `skill-control-plane`, and `skill-release` still includes `skill-control-plane`; therefore Skill-only releases retain the no-product-change guard. Product static/quick/integration/release gates are unchanged.

## Current boundary

Local deterministic validation passes: 2 CI-boundary tests, 48 Skill unit tests, 7 security tests, Skill static/host checks, version consistency, architecture and Evidence contract. The GitHub retry is still required before the CI boundary can close. Protected production certification has not executed and no `production_closed` artifact exists.
