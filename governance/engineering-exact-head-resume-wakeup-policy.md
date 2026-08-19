# Automatic Exact-Head Resume After Real Workflow Approval

An `EXACT_HEAD_CI_AWAITING_APPROVAL` state is a real external Human Gate when GitHub requires workflow approval. Automation must not approve that workflow or reinterpret `action_required` as success.

When the human has actually approved the exact workflows and GitHub reports both exact-head pull-request `quality` and `skill-self-validation` runs as terminal `success`, a separate wakeup may remove the redundant **second** resume click for a TaskRun that already has an owner-issued `EngineeringMergeGrant`.

## Preconditions

The wakeup requires all of the following:

- the triggering run is a successful `pull_request` workflow run on the exact PR head;
- the latest exact-head PR runs for both mandatory workflows are completed success;
- a prior successful G6 artifact for the same PR/head is still `EXACT_HEAD_CI_AWAITING_APPROVAL` with `resume_required=true`;
- that exact G6 has not already produced a resume artifact;
- an owner-dispatched `EngineeringMergeGrant` revalidates against the same immutable TaskRun binding.

## Dispatch and replay protection

The wakeup serializes by repository + exact head and reserves one resume dispatch with a commit-status context bound to exact G6 run ID/attempt and TaskRun fingerprint. `pending` is treated as an uncertain crash window; `success` means already dispatched; `failure/error` requires explicit recovery rather than replay.

## Resume workflow

`governed-ci-repair-exact-head-resume` keeps its manual owner-token path. A preauthorized path may be used only with the exact owner authorization run carrying the same TaskRun MergeGrant. The resume workflow re-reads the prior G6 artifact and the now-green exact-head workflows before finalizing G6. It does not repeat governance or protected-baseline acceptance.

## Authority boundary

This automation never approves a GitHub workflow, never bypasses protected Environment reviewers, and never creates merge, deployment, release, or production authority. Final merge remains a separate bounded MergeGrant decision after G6 exact-head certification.
