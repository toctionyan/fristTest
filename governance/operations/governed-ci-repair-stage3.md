# Governed CI Repair Stage 3

## Purpose

Stage 3 is the independent validation and Draft-publication boundary for a repair candidate produced by Stage 2. It does not call the model fixer and does not trust a candidate merely because syntax validation passed.

## Privilege separation

Stage 3 is split into three authority zones:

1. `inspect` is read-only and verifies the immutable Stage-2 handoff.
2. `validate` is read-only. It may install and execute candidate tests, but it has no repository, PR or Actions write permission.
3. `publish` has the minimal write permissions needed to push a governed repair branch, create a Draft PR and dispatch normal Quality. It does not install dependencies or execute candidate code.

The publisher receives only validation evidence and the bound patch. It reapplies the patch to the exact failed source commit and requires the resulting Git tree to equal the tree produced by the read-only validation job.

## Authoritative flow

1. Consume the immutable `governed-ci-repair-stage2-*` Artifact.
2. Verify the Stage-2 result, patch digest, TaskRun binding, exact source SHA, repair branch and base branch.
3. Apply the patch with `git apply --check` to the exact failed commit in the read-only validation job.
4. Reject any changed path outside the Stage-2 immutable product-source set.
5. Create a local, unpushed validation commit and bind its parent and Git tree.
6. Run fixed component regression suites selected by trusted path-to-component rules.
7. Run a complete Quick Quality Loop using the read-only `main` control plane.
8. Require `decision=PASS`, `loop_status=CI_VERIFIED`, `completion_eligible=true`, and every required gate to pass.
9. Upload the validation result, bound tree, patch and TaskRun as a same-run Artifact.
10. In the separate publisher job, reapply the patch without running candidate code and require the recreated tree to match the validated tree.
11. Refuse publication when the original base branch moved, a repair branch points to different evidence, or an existing PR is not the expected Draft PR.
12. Push the immutable repair commit and create or reuse a verified Draft PR only.
13. Dispatch the normal repository Quality workflow on that branch.
14. Complete the TaskRun only after Draft publication evidence is recorded.

## Automatic repair boundary

Stage 3 does not expand the Stage-2 path set. Automatic source repair remains restricted to non-test product source under:

- `services/`
- `web/`
- `contracts/`

Governance, workflows, tests, E2E assets, manifests, lockfiles, `.env` files, symlinks, traversal paths and control-plane code remain prohibited.

## Fail-closed rules

Stage 3 blocks instead of publishing when any of these conditions occurs:

- Artifact schema or patch digest mismatch.
- TaskRun binding or source SHA drift.
- Patch cannot apply cleanly.
- Applied paths differ from the immutable Stage-2 set.
- Deterministic parsing fails.
- Targeted regression fails or times out.
- Complete Quick validation is not current and completion eligible.
- Validation and publication Git trees differ.
- Base branch advanced after the source failure.
- Existing repair branch points to another commit.
- Existing repair PR is not the expected Draft PR.

A failure on a `governed-repair/` branch is ingested for diagnosis but is not recursively sent through Stage 2.

## Authority exclusions

Stage 3 never:

- Reads model or embedding Secrets.
- Gives a candidate-code execution job write permissions.
- Changes Environment configuration.
- Force-pushes a repair branch.
- Marks a PR Ready.
- Merges a PR or writes directly to protected `main`.
- Runs WP-08 production certification.
- Closes WP-08 or WP-09.
- Sets `production_closed=true`.
