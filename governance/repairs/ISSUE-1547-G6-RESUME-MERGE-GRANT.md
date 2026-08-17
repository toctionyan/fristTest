# ISSUE-1547 — G6 resume and exact-head MergeGrant

This repair is rooted in the failure mode proven by PR #1348.

## Invariant

A governed candidate may accept its protected product baseline exactly once. If GitHub marks the newly generated exact-head `pull_request` workflows as `action_required`, the lifecycle must enter a resumable approval-wait state rather than repeating governance or baseline acceptance.

Merge authorization is a separate single-use authority. A comment is evidence only; it is not itself merge authority.

## State model

`GOVERNANCE_CLOSED -> BASELINE_ACCEPTED -> EXACT_HEAD_CI_AWAITING_APPROVAL -> EXACT_HEAD_CI_RUNNING -> READY_FOR_REVIEW -> MERGE_GRANT_ISSUED -> MERGED / GRANT_CONSUMED`

## Fail-closed constraints

- resume binds immutable baseline/governance evidence, PR number, branch, exact head SHA, and required workflow identities;
- no resume path can re-run baseline acceptance or mutate product source;
- `action_required` is resumable only for the exact required PR workflows on the accepted head;
- any PR head/base drift fails;
- MergeGrant requires repository-owner acknowledgement, exact PR/head/base binding, and is single-use;
- replay, stale head/base, non-owner, or malformed grant fails;
- merge grant never authorizes deployment, Protected Release, production certification, or `production_closed=true`.

`production_closed=false`.
