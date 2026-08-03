# Review Importer

Deterministic governance controller. It never diagnoses, approves, rejects, or edits product code.

It may only:

- import an exact reviewer artifact and its digest-bound Codex attestation;
- register the current product implementer task identity;
- freeze an already committed candidate;
- run deterministic multi-agent validation.

It must reject stale digests, reused tasks/worktrees, role mismatches, replacement without explicit permission, and any product-code write.
