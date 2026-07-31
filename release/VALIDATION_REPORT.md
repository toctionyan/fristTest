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

## GitHub Quick red baseline and round 2

Run `30608910835` proved the Profile-boundary repair: `skill-self-validation` and `quality-static` passed. Quick Job `91087188820` then failed only three stale adversarial Harness tests while 134 tests in that gate passed. Two bridges called an undefined `_load_test_module`; the third expected Workflow-owned service startup that B17d had deliberately retired in favor of the production certification bundle.

Round 2 replaces the missing-loader calls with direct imports of the authoritative B17e counterexamples. The stale architecture assertion now proves that `release.yml` delegates to `run_production_release.py` / `verify_production_certification_bundle.py`, while `verify_full_lifecycle_canary.py` owns the protected preprod service contract. Three targeted deterministic contracts and Python compilation pass locally. A locked Agent pytest runtime is absent locally, so the GitHub Quick retry remains the authoritative closure test.

## GitHub standard Python red baseline and round 3

Run `30609735023` verified the round-2 repair with `137 passed` in `adversarial-runtime-counterexamples`. The standard Agent suite then reported only 3 failures out of 857 tests; Business reported 28 passed. One failure was stale current-phase Changelog metadata. The other two were false scenario contamination: tests intended to prove behavior outside CI inherited GitHub Actions and Workflow-level release variables from the parent Runner.

Round 3 updates metadata and makes the no-CI subprocess tests construct an isolated environment. Production Preflight and Admission code remains unchanged, preserving real fail-closed ordering.
