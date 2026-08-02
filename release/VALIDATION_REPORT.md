# B17j Validation Report

- Product/workspace version: `20.6.1`
- Architecture Skill version: `6.3.1`
- Governance phase: `V20.17 B17j`
- Status: `PHASE_CANDIDATE_ENVIRONMENT_EXECUTION_PENDING`
- `production_closed`: `false`

## Semantic authority correction

Skill `6.3.1` makes semantic representation independence an executable hard boundary. Open-language meaning remains owned by the model semantic role; deterministic code validates structure, evidence, data lineage, permissions, authorization and transaction invariants. Semantic certification no longer treats one exact Goal count, Goal ID mapping, `depends_on` graph or Tool order as the only valid interpretation. Internal-shape disagreements must enter read-only Oracle Review before any writable repair.

## Semantic Oracle migration evidence

The protected semantic smoke now validates required user-visible effect evidence and delegates semantic completeness to the independent production alignment judge; it does not compare one exact Goal count or `depends_on` graph. Legacy tests that still called the retired exact-AST matcher were migrated to the effect-coverage contract. The regressions preserve strict failures for duplicate Goal IDs, missing effects and incorrect literal evidence while accepting equivalent dependency graphs and composite Goal representations.

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

## GitHub lifecycle red baseline and round 4

Run `30610419110` passed Skill self-validation, static, adversarial counterexamples, all 857 Agent tests, all 28 Business tests, frontend tests/build and coverage. The only remaining Quick failure was `full-lifecycle-canary`: the Harness resolved `.venv/bin/python` to the base system interpreter and then could not import `uvicorn`.

Round 4 preserves the selected virtual-environment entrypoint with absolute path normalization only. A direct symlink regression proves the venv path is not dereferenced. No dependency or product runtime behavior is changed.

## Round 4 pre-closure GitHub PASS

GitHub run `30611637518` on commit `26f3d058e77d5e025585e8c311883b07d32db325` passed Skill self-validation, Static and Quick. Quick produced `CI_VERIFIED`, decision `PASS`, `completion_eligible=true`, no missing prerequisites and all 18 required gates PASS. Standard suites reported 858 Agent tests and 28 Business tests with zero skips. Coverage passed at Python 0.7288 and frontend 0.5432. The authenticated HTTP lifecycle and Chromium product journey both passed.

Downloaded evidence artifacts were CRC/read verified and matched GitHub digests: Static `51a6f9e745732a6afd1cf0ccc33019e8913e0ef0da4bb66aacd392df6195dab8`; Quick `87bf278e5b91dab62750f6417530367508d1f07d1ba435989c366ec9f01b0746`.

This report update changes the source tree. To avoid a self-referential endless metadata loop, the exact final metadata-normalized commit must pass once more; that final run identity is recorded in the external delivery evidence rather than mutating source after the final run.
