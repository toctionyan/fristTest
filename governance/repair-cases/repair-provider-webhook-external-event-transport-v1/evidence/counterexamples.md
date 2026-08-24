# Counterexamples

PASS evidence is exercised by
`test_provider_webhook_external_event_transport.py`:

- absent, malformed, or incorrect `X-Hub-Signature-256` is rejected;
- ambiguous or incorrect event, content-type, and delivery headers are rejected;
- malformed JSON, wrong action/status, invalid run metadata, and a repository
  other than the configured exact repository are rejected;
- an empty or oversized request body is rejected before delivery;
- a missing named webhook secret blocks without persisting request evidence;
- replaying one delivery id with different bytes is rejected;
- concurrent duplicate delivery invokes the Scheduler exactly once;
- replacing the authenticated HMAC with a recomputed plain SHA-256 cannot make
  tampered persisted evidence authoritative;
- a terminal Scheduler rejection is preserved as transport rejection and is not
  converted to success;
- an incorrect HTTP route or method cannot reach the transport.
