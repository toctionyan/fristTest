# Engineering Human Gate Policy

## Principle

Routine GitHub mechanics are not human authority. Once a TaskRun has an explicit bounded owner authorization, the system should continue through safe mechanical transitions without asking the owner to click again.

## One bounded owner authorization

For autonomous final landing, the repository owner explicitly authorizes the exact TaskRun through `engineering-autonomy-authorize` with `merge_policy=bounded-auto-merge`. That authorization is bound to the immutable TaskRun fingerprint, target, repository, and write scope. It is not reusable as generic repository merge authority.

## Actions that should continue automatically inside that authorization

- inspect GitHub CI, jobs, steps, logs, and artifacts;
- classify product vs transport/environment failures;
- bounded repair/retry already allowed by the TaskRun;
- push/update the governed candidate and wait for exact-head CI;
- re-read PR mergeability and wait out transient GitHub computation;
- wait for same-head push checks to finish and reject a real red result;
- perform the redundant solo-owner G6 transition when no independent-review policy exists;
- after a real workflow approval, resume exact-head certification without a second owner click;
- mark the exact governed PR Ready when the final gate allows it;
- consume the single-use MergeGrant and perform one exact-head merge-commit landing;
- proceed to the next already-authorized milestone.

## True Human Gates that remain

A human is still required when any of these boundaries are crossed:

1. **Goal / acceptance authority** — changing the requested goal, acceptance criteria, protected tests, oracle, or what counts as success.
2. **Scope / privilege expansion** — writing outside the authorized TaskRun scope, requesting new secrets/permissions, or changing security authority.
3. **Destructive or production boundary** — destructive data migration, release, deployment, production traffic, or other production authority.
4. **Independent review** — a protected GitHub Environment/ruleset genuinely requires another reviewer, especially `prevent_self_review=true`.
5. **GitHub workflow approval** — when GitHub reports an exact workflow as `action_required`, the system may wait and observe but must not approve it. After the human approval makes the exact workflow genuinely green, continuation may resume automatically.
6. **Uncertain authority state** — durable reservation is `pending/UNCERTAIN`, exact identity cannot be re-read, policy inspection is unavailable, or evidence conflicts. Fail closed instead of guessing.

## Explicit non-authorities

`AutonomyGrant` remains merge-forbidden. `EngineeringMergeGrant` is final-merge-only and cannot modify source/tests, weaken acceptance, expand scope, deploy, release, or reach production. G6 receipts themselves continue to report `merge_allowed=false`; the independent MergeGrant gate is the only layer that may authorize the final merge.
