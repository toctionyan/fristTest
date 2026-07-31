# V20.17 B17f CI Run Identity and Replay Authority

## Problem

B17e made the release toolchain reproducible, but a reproducible toolchain alone does not prove that every accepted artifact belongs to the current protected GitHub Actions execution. The signed Quality Loop summary and the release toolchain evidence did not bind the repository, protected ref, workflow file, commit, run ID and run attempt into one canonical authority. Evidence from an earlier attempt of the same commit could therefore be copied into a later attempt, or evidence from a different checkout could be combined with the current release inputs while retaining an otherwise valid source and toolchain fingerprint.

The workflow also relied on platform configuration for branch protection and checkout integrity. It did not make the following conditions executable release contracts: the event is a manual protected release, the ref is the protected `main` branch, `HEAD` equals `GITHUB_SHA`, the worktree is clean, the origin repository matches the GitHub repository, and checkout credentials are not persisted.

## Authority boundary

B17f introduces `release-run-identity@1`. It owns the immutable identity of one protected certification attempt:

- GitHub repository name, numeric repository ID and server/API authorities;
- workflow name, workflow file reference and workflow commit;
- protected branch ref and ref type;
- current commit SHA and checked-out `HEAD`;
- workflow run ID, run number and run attempt;
- protected release job and triggering event;
- clean worktree, matching origin and absence of persisted checkout credentials.

The canonical payload produces `run_identity_fingerprint_sha256`. The payload is embedded inside `release-toolchain-provenance@1`, so the toolchain fingerprint is now a function of both the installed execution stack and the exact protected CI attempt.

## End-to-end binding

The protected workflow checks out the exact `github.sha` with a clean, shallow checkout and `persist-credentials: false`. Job admission requires `workflow_dispatch`, `refs/heads/main` and `github.ref_protected == true`.

After the locked toolchain is installed, the provenance capture validates the live Git checkout and emits the run identity fingerprint. The workflow injects this fingerprint into the production release controller. The same value is required in:

1. the validated toolchain evidence;
2. every production certification session derived from that toolchain;
3. the Quality Loop release summary;
4. the clean-release provenance manifest;
5. the final production closure ledger and artifact name.

The controller independently compares the evidence repository, workflow reference, commit, protected ref, run ID and run attempt with the current process environment. A prior run, prior attempt, different commit, different repository, dirty checkout, mismatched origin or persisted credential header fails closed. Artifact names include both run ID and run attempt so a rerun cannot silently overwrite or impersonate an earlier attempt.

## Failure semantics

A missing GitHub protected-run context, unavailable locked runtime, unavailable Docker/model secret, or other external prerequisite remains `BLOCKED_BY_ENVIRONMENT`. A malformed or inconsistent run identity is a control-contract failure. Neither case can create quality evidence, a protected artifact, or `production_closed`.

## Non-goals

B17f does not change customer-service semantics, prompts, capabilities, transaction behavior, business rules, database schema, model routing or RAG behavior. It does not claim that the protected workflow has executed in the current local environment.
