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

## Consumer inventory gate

The complete known consumer inventory for this lifecycle is:

1. `scripts/github_repair_baseline_acceptance.py` — sole baseline-acceptance mutation owner; creates the accepted-baseline child commit and leaves G6 pending.
2. `scripts/github_repair_exact_head_state.py` — sole external exact-head CI state classifier; no mutation authority.
3. `scripts/github_repair_exact_head.py` — sole G6 exact-head finalizer from accepted-baseline + successful exact-PR CI evidence.
4. `.github/workflows/governed-ci-repair-governance.yml` — protected multi-review governance orchestration consumer.
5. `.github/workflows/governed-ci-existing-candidate-solo-governance.yml` — solo-owner existing-candidate orchestration consumer.
6. `.github/workflows/governed-ci-repair-exact-head-resume.yml` — resume-only consumer; may finalize G6 but cannot repeat governance/baseline acceptance.
7. `scripts/github_repair_merge_grant.py` — sole machine authority for converting `READY_FOR_REVIEW` evidence into exact PR/head/base merge-only authority.
8. `.github/workflows/governed-ci-repair-merge.yml` — sole intended grant consumer; rechecks binding, merges exact head, verifies merge parents, and records grant consumption.
9. GitHub Ruleset / branch protection for `main` — platform enforcement remains separately blocked by Issue #1475 and is not claimed closed by this code repair.

No product/runtime or deployment consumer is permitted to interpret these receipts as production authority.

## Root-cause closure proof

Acceptance requires:

- authority count = 1 for baseline mutation, exact-head state classification, G6 finalization, and MergeGrant issuance;
- both G6 orchestration consumers delegate `action_required` classification to the same classifier;
- the resume workflow contains no baseline-acceptance invocation or candidate-source write;
- positive/negative transition matrix covers success, pending, approval wait, terminal failure, stale head, wrong event/PR, non-owner grant, Draft PR, and tampered receipt;
- replay of the PR #1348 `action_required` incident terminates in `EXACT_HEAD_CI_AWAITING_APPROVAL`, then resumes from the same accepted-baseline receipt;
- MergeGrant is issued and consumed in one run, and head/base are rechecked immediately before merge with merge-commit parent verification afterward;
- full Skill/Quality exact-head CI passes.

## Platform dependency

Issue #1475 remains open until GitHub itself enforces required checks / PR-only main mutation and a negative bypass test is recorded. This repair must not be used to close that platform blocker.

`production_closed=false`.
