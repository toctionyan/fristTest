# Engineering Authorized Merge Policy

## Purpose

A task-level `EngineeringMergeGrant` may remove repetitive final PR clicks without weakening the existing repair, review, G6, deployment, or production boundaries.

`EngineeringMergeGrant` is a separate authority from `AutonomyGrant`. `AutonomyGrant` remains merge-forbidden and continues to authorize only bounded engineering continuation. A merge grant never creates source/test write authority, never changes the user goal or acceptance oracle, and never expands the TaskRun write scope.

## Issuance

The trusted `engineering-autonomy-authorize` owner-dispatch may optionally use `merge_policy=bounded-auto-merge`. The default is `disabled`.

An issued merge grant is bound to the exact TaskRun immutable binding fingerprint, repository, original task branch/base, target fingerprint, and allowed paths. It is single-use and only permits `mark_ready` plus merge-commit landing after the existing G6 authority has independently completed.

## Final merge gate

Automatic landing is allowed only when all of the following are re-read from GitHub and remain true at the final decision:

- the PR is open, unmerged, mergeable, same-repository, and targets `main`;
- the PR belongs to the same governed TaskRun/G6 lineage;
- governance is closed, protected baseline accepted, and exact-head certification is PASS;
- exact `pull_request` runs for `quality` and `skill-self-validation` are terminal success;
- when same-head `push` runs exist for those workflows, the latest same-head push run is also terminal success;
- changed paths remain inside the TaskRun scope, except the exact system-generated protected-baseline registry path;
- there is no active `CHANGES_REQUESTED`, unresolved review thread, or explicit Human Gate;
- the grant and final decision fingerprints remain intact.

A green PR check does not hide a red same-head push check. Stale older push failures are ignored only when a newer same-head run for the same workflow is successful.

## Exact-head CAS

After a passing gate, the PR may be changed from Draft to Ready. The workflow then re-reads the PR and compiles one merge request containing the exact expected head SHA and merge method `merge`. Any head or base drift after the gate blocks the request instead of merging a different candidate.

## Human Gates that remain

Automatic merge does not bypass:

- changes to the user goal, acceptance criteria, protected tests, or oracle;
- evidence-backed scope expansion that crosses the original grant boundary;
- new secrets/privileges, destructive data operations, deployment, release, or production;
- a repository policy requiring genuinely independent human review (for example a protected Environment with `prevent_self_review`).

## Production boundary

This policy grants no deploy, release, or production authority. All generated contracts and receipts must keep `deploy_allowed=false` and `production_closed=false`.
