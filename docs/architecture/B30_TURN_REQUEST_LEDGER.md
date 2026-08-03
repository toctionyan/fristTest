# B30 WP-02 TurnRequestLedger Contract

## Why this authority is required

The current conversation lease and TurnFence serialize active writers, but they do not identify a network request durably. A client can lose a response and retry the same message after the first worker has already appended the user message or completed the graph. Without a durable request identity, “one HTTP request equals one Turn” cannot be proved.

`TurnRequestLedger` is therefore the sole authority for chat request identity and replay. It is not the existing action `IdempotencyStore`: action idempotency protects business commands, while this ledger owns the creation and completion of a conversational Turn before any model or graph execution.

## Durable key and digest

The unique key is:

```text
(tenant_id, user_id, thread_id, client_request_id)
```

The request digest binds normalized message, authenticated actor context and response profile. Transport is deliberately excluded, so an HTTP retry and an SSE retry for the same logical submission use one Turn. Reusing the same key with a different digest is `PAYLOAD_CONFLICT`.

`ChatRequest.client_request_id` is required. The public API must not silently generate it, because a newly generated identifier on every retry destroys idempotency. The frontend generates it once per user submission and reuses it across retries.

## Required order

```text
authenticate
  -> require client_request_id
  -> acquire conversation lease
  -> atomic ledger claim
  -> branch on claim decision
  -> append one user message with ledger message_id
  -> invoke graph with ledger turn_id
  -> persist public response
  -> complete ledger using owner/fencing CAS
  -> return or emit terminal response
```

Message append and graph invocation before the ledger claim are forbidden.

## Retry and crash behavior

- `SUCCEEDED` with the same digest replays the stored canonical response. It does not append a message, invoke the graph or call the model.
- An active `CLAIMED/RUNNING` record returns `REQUEST_IN_PROGRESS` and never starts a second worker.
- An expired `RUNNING` record is not automatically re-executed. It becomes `RECOVERY_REQUIRED`; durable message, checkpoint and response evidence must be reconciled first.
- `SUBMISSION_UNKNOWN` enters a dedicated reconciliation transition. This prevents a crash boundary from creating two Turns or two transaction drafts.
- Completion requires the current owner and fencing token, so a stale worker cannot overwrite a recovered request.

## Storage ownership

`TurnRequestRepository` is a first-class StoreProvider dependency with equivalent SQLite and SQLAlchemy/PostgreSQL behavior. A process-local dictionary, generic retry helper or LangGraph checkpoint cannot replace it.

The stored terminal result is the canonical public response envelope plus a digest. HTTP and SSE may encode that envelope differently, but they must replay the same logical result.

## Required counterexamples

WP-02 cannot close until tests prove:

1. two simultaneous claims produce one winner;
2. same ID plus different payload is rejected;
3. HTTP followed by SSE with the same ID creates one Turn;
4. an SSE disconnect replays the result without a second graph invocation;
5. a crash after claim requires recovery instead of re-execution;
6. an expired RUNNING record is not automatically reclaimed;
7. one request appends one user message;
8. a stale fencing token cannot complete the request;
9. success is not returned until response and ledger completion are durable;
10. missing `client_request_id` is rejected at the API boundary.

The machine-readable contract is `governance/architecture/b30-turn-request-ledger.json`.
