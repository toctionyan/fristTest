# B30 WP-02 TurnRequestLedger Implementation Plan

## Bound inputs

This plan is valid only for B30 baseline `e7e902c3363d867b8bea18ab2c76c0739252daad` and the TurnRequestLedger contract blob `bcc9f2847de265fe207a6635647da2c492620eaa`. Moving either identity invalidates review and any ChangePermit.

## Proven implementation seams

The current code establishes the failure mechanism directly:

- `ChatRequest` has no `client_request_id`, although structured transaction requests already require one.
- HTTP and SSE both append the user message inside `ConversationTurnService` before any durable request claim.
- `MessageRepository.add_message` and SQLite/SQLAlchemy message tables have no stable public `message_id` contract.
- `StoreProvider` has no `TurnRequestRepository`.
- `ChatPanel` already generates an optimistic user-row ID, but sends only `thread_id` and `message` to `/chat/turn`.

This means the repair is not a generic retry wrapper. It requires a first-class persistence authority before message append and graph invocation.

## Required scope amendment

The initial contract listed the repository, provider, schema, conversation use case and frontend tree, but omitted two concrete files that are necessary to satisfy its own invariants:

- `app/services/agent_service.py` must receive the repository from the composed StoreProvider; it must not construct a private second authority.
- `persistence/message_store.py` must accept the ledger-owned stable message identity and enforce idempotent user-message persistence.

The Repair Plan Reviewer must explicitly approve this amendment. The implementer cannot infer approval from this document alone.

## Eight implementation phases

### P1 — Repository types and digest

Add immutable scope/record/claim types, claim decisions and deterministic digest construction. The repository layer remains independent of FastAPI, LangGraph and model clients.

### P2 — SQLite authority

Create the composite unique scope key, atomic claim, monotonically increasing fencing token and owner-bound compare-and-set transitions. An expired RUNNING record returns recovery-required; it is not silently reclaimed.

### P3 — SQLAlchemy/PostgreSQL parity

Add the same table and repository semantics. Production schema-present mode must report a missing ledger schema rather than create it implicitly.

### P4 — Stable message identity

Extend message persistence with an optional stable `message_id`, preserving historical rows. Repeated insertion of the same identity and same payload is idempotent; the same identity with different content is a conflict.

### P5 — API, composition and frontend

Require `ChatRequest.client_request_id`. Wire the StoreProvider repository into AgentService. `ChatPanel` generates one ID and uses that same value for its optimistic row and request payload. No server fallback is permitted.

### P6 — HTTP/SSE integration

Claim under authenticated scope and conversation lease before message persistence or graph access. Only NEW_CLAIM may append or run. SUCCEEDED replays the stored response; conflict, in-progress and recovery-required states fail closed. HTTP and SSE share the same transport-neutral identity.

### P7 — Crash recovery and durability

Persist the canonical public response before ledger success and before returning success. Test crashes after claim, message append, graph completion and response persistence. Unknown completion enters reconciliation and never auto-runs the graph.

### P8 — Independent closure

Run focused, counterexample, concurrency, crash, backend parity, HTTP/SSE and complete regression suites. An independent Diff Reviewer verifies that no message/graph path bypasses the ledger. Product-source baseline refresh is performed by a separate control-plane actor only after approval.

## Prohibited shortcuts

- Reusing action `IdempotencyStore` as Turn authority.
- Generating a request ID on the server.
- Treating ConversationLease, TurnFence, checkpoint or process memory as request identity.
- Adding a compatibility route that bypasses the ledger.
- Automatically retrying an expired RUNNING or SUBMISSION_UNKNOWN record.
- Returning success before response persistence and fenced ledger completion.
- Allowing the product implementer to refresh its own protected-source baseline.

## Exit rule

WP-02 implementation closes only when the 13 acceptance tests in the frozen contract, all eight mandatory test dimensions, independent diff review, protected-source baseline refresh and complete Quick regression all pass. A partial backend, optional API field or HTTP-only implementation is not closure.
