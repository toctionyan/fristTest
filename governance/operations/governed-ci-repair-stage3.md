# Governed CI Repair Stage 3

## Purpose

Stage 3 is the independent validation and Draft-publication boundary for a repair candidate produced by Stage 2. It does not call the model fixer and does not trust a candidate merely because syntax validation passed.

## Authoritative flow

1. Consume the immutable `governed-ci-repair-stage2-*` Artifact.
2. Verify the Stage-2 result, patch digest, TaskRun binding, exact source SHA, repair branch and base branch.
3. Apply the patch with `git apply --check` to the exact failed commit.
4. Reject any changed path outside the Stage-2 immutable product-source set.
5. Create a local, unpushed repair commit.
6. Run fixed component regression suites selected by trusted path-to-component rules.
7. Run a complete Quick Quality Loop using the read-only `main` control plane.
8. Require `decision=PASS`, `loop_status=CI_VERIFIED`, `completion_eligible=true`, and every required gate to pass.
9. Refuse publication when the original base branch moved or a governed repair branch already points to different evidence.
10. Push the immutable repair commit and create a Draft PR only.
11. Dispatch the normal repository Quality workflow on that branch.
12. Complete the TaskRun only after Draft publication evidence is recorded.

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
- Base branch advanced after the source failure.
- Existing repair branch points to another commit.
- Existing repair PR is not the expected Draft repair PR.

A failure on a `governed-repair/` branch is ingested for diagnosis but is not recursively sent through Stage 2.

## Authority exclusions

Stage 3 never:

- Reads model or embedding Secrets.
- Changes Environment configuration.
- Force-pushes a repair branch.
- Marks a PR Ready.
- Merges a PR or writes directly to protected `main`.
- Runs WP-08 production certification.
- Closes WP-08 or WP-09.
- Sets `production_closed=true`.
