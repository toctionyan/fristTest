# Business Service Rules

- The Business Service is final authority for business facts, authorization, state transitions, idempotency, versions, and write receipts.
- Agent code may propose commands but must not duplicate or bypass business validation.
- Preserve actor identity, command digest, idempotency key, optimistic version, state-machine legality, and receipt reconciliation.
- Tests must cover replay, stale version, unauthorized actor, invalid transition, unknown submission, and recovery.
- Product implementers may not weaken business invariants to satisfy Agent tests.
