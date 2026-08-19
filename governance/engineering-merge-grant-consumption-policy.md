# Durable MergeGrant Consumption

`EngineeringMergeGrant.single_use=true` is enforced by durable repository evidence, not by an in-memory assumption.

## Stable identity

The consumption anchor is the grant's immutable TaskRun `initial_base_sha`. The GitHub commit-status context is `engineering-merge-consume/<full grant_sha256>`, so a newly issued grant for the same TaskRun has a different consumption identity.

## Serialization

`engineering-authorized-merge` uses repository-wide GitHub Actions concurrency with `cancel-in-progress:false`. Autonomous final landings therefore execute one at a time for a repository. This serialization covers the read→reserve interval and also prevents two autonomous merges from racing movement of `main`.

## State machine

Before the merge network call, after the final gate and second PR/CAS read:

- no matching status → `RESERVABLE`;
- `pending` → `UNCERTAIN`, fail closed;
- `success` → `CONSUMED`, fail closed to replay;
- `failure` or `error` → `FAILED`, new authority required;
- any unknown state → blocked.

A new reservation writes `pending`. Successful merge writes `success`. A merge failure writes `failure`. If execution stops after the pending reservation but before finalization, later consumers observe `UNCERTAIN` rather than replaying a possibly consumed grant.

## Authority boundary

Consumption state never creates merge authority. It is evaluated only after the existing MergeGrant, G6, exact-head, scope, workflow, review, and CAS checks have passed. It never creates source/test write authority and never grants deployment, release, or production access.
