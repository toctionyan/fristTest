# Runtime trace

The real-boundary focused test performs this trace:

1. copy and verify the shipped Customer Agent Starter into a temporary project;
2. register the exact Starter runtime package;
3. construct the existing `StarterHostOrchestrator` for Host `codex`;
4. send closed `OPEN` through `StarterHostCommandTransport` with the Chinese
   overall-audit request;
5. observe canonical `AWAITING_SELECTION / SELECT_EXACT_ENTRYPOINT`;
6. submit an exact `starter-host-selection@1` for `overall_audit` using revision
   `0`;
7. observe canonical `READY_TO_START / START_TASKRUN`;
8. send `READ` and prove it returns the identical durable session.

The process-boundary test invokes `python -B skillctl.py host --factory ...`,
passes one JSON request on stdin, and parses exactly one PASS response on stdout.
Lower-level Orchestrator tests continue to prove start, repeated real Host result,
external event, Human decision, reconciliation, and END-to-VALIDATING behavior.
