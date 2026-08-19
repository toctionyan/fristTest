# Durable Solo G6 Preauthorization

A repository owner may remove a redundant later solo-governance click by selecting `bounded-auto-merge` during the trusted `engineering-autonomy-authorize` dispatch for a specific TaskRun.

This does **not** turn an `AutonomyGrant` into merge authority. The independent `EngineeringMergeGrant` is re-bound to the Stage-3 TaskRun before it may serve as evidence that the owner already authorized the solo-owner governance transition for that same task.

## Automatic handoff

After a successful Stage-3 publication, `engineering-solo-governance-wakeup`:

1. reads the exact Stage-3 run and publication artifact;
2. computes the immutable TaskRun binding fingerprint;
3. finds the exact `engineering-merge-grant-<task-fingerprint>` artifact;
4. proves its producer was a successful `engineering-autonomy-authorize` `workflow_dispatch` by the repository owner;
5. revalidates the MergeGrant against the exact Stage-3 TaskRun;
6. reserves the Stage3→solo-G6 dispatch using a replay-stable commit status;
7. dispatches the existing `governed-ci-repair-solo-governance` workflow once.

The solo G6 workflow redoes the same task/run/grant verification before governance is closed. Network handoff evidence therefore cannot itself mint authority.

## Independent review remains human

Before choosing the solo path, the wakeup checks the protected `governed-repair-governance` Environment. If required reviewers exist with `prevent_self_review=true`, it stops and does not dispatch solo governance. The multi-user governance workflow is unchanged and remains the authority for genuinely independent review.

## Backward compatibility

The existing manual solo-owner token `CLOSE_SOLO_GOVERNANCE_AND_ACCEPT_BASELINE` remains valid. Manual token mode and task-preauthorization mode are mutually exclusive in one run.

## Authority limits

Task preauthorization may satisfy only the redundant solo-owner acknowledgement. It does not bypass Stage 3, baseline acceptance, exact-head pull-request CI, review state, the M8 final merge gate, deployment, release, or production controls. `merge_allowed=false`, `deploy_allowed=false`, and `production_closed=false` remain true throughout G6 itself.
