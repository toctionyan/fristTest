# B30 Runtime Entrypoint Inventory

This inventory maps every known customer-facing mutation/read path and the internal gateways that can advance lifecycle state. The machine-readable source is `governance/architecture/b30-runtime-entrypoints.json`.

## External paths

| Entrypoint | Current path | Result |
|---|---|---|
| Chat HTTP | API → ConversationTurnService → LifecycleGraph → RuntimeOutcome → ResponseProjector | Gap: no durable TurnRequestLedger identity |
| Chat SSE | Same graph, final `get_state`, same ResponseProjector | Gap: same request-idempotency issue |
| Transaction start | Structured target → capability preparation → BusinessService preview → Draft → gateway → RuntimeOutcome | Conforms; zero LLM and cannot commit |
| Transaction input | Exact form/version/revision → transaction state machine → RuntimeOutcome | Conforms |
| Transaction authority | Exact authority → formal commit boundary → BusinessService/TransactionRepository → RuntimeOutcome | Conforms |
| Transaction reconcile | Existing attempt + original idempotency key → formal reconciliation | Gap: raw public dictionary bypasses RuntimeOutcome projection |
| Business resource reads | Typed API → BusinessService → DTO | Valid read-only projection; outside open-language planning |
| Transaction reads | Typed API → TransactionRepository → DTO | Valid read-only projection |
| Pending interaction read | checkpoint projection + TransactionRepository → read-only interaction DTO | Valid only while repository remains lifecycle authority |

## Internal authority gateways

- `build_lifecycle_graph` owns routing, not semantics or business facts.
- `LifecycleCommandRunner` is the only privileged external structured-transition bridge and always resumes formal routing.
- `CapabilityGate` creates MatchProof and ExecutionPermit; model tool choice is not permission.
- `frozen_plan_definition + plan_run` are the plan authorities. `grounded_execution_plan` is a digest-bound cache/bootstrap view, not a durable owner.
- `ResponseProjector` projects RuntimeOutcome into HTTP/SSE/UI and cannot perform business writes.

## WP-01 findings

### FINDING-01 — missing durable request identity

`ChatTurnPayload` contains thread and message but no external request identifier bound to a durable TurnRequestLedger. Conversation locking prevents concurrent writers, but it does not prove that a network retry creates at most one Turn. WP-02 must add the ledger before message persistence and graph invocation.

### FINDING-02 — reconciliation projection bypass

The reconciliation endpoint safely replays only existing attempts, but it returns a raw dictionary. WP-06 must represent reconciliation results through RuntimeOutcome and a canonical projection.

### FINDING-03 — plan cache acceptance proof

The plan projection contract validates and re-derives its cache from `frozen_plan_definition + plan_run`. WP-04 must add a counterexample proving post-materialization dispatch cannot proceed from a cache alone.

## WP-01 exit status

All known external mutation/read entrypoints and internal lifecycle gateways are mapped. There are no unknown customer-facing write routes in the inspected API surface. WP-01 closes as `MAPPED_WITH_GAPS`; the three explicit findings become mandatory downstream work and cannot be relabeled PASS without code and counterexample evidence.

## Machine entrypoint identifiers

- `chat_turn_http`
- `chat_stream_sse`
- `transaction_start_http`
- `transaction_input_http`
- `transaction_authority_http`
- `transaction_reconcile_http`
- `business_resource_query_http`
- `transaction_query_http`
- `pending_interaction_query_http`
