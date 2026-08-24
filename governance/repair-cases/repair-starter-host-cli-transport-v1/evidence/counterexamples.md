# Counterexamples

The focused suite proves that no Orchestrator method is called when a request
contains an unexpected top-level field (including `factory`), an unsupported
operation such as `AUTOMATIC_MERGE`, `authority_effect=true`, a forbidden or
missing revision, a missing operation payload, non-canonical Host/operation
case, a non-string identifier/evidence reference, duplicate/missing evidence,
or a mismatched factory Host.

The CLI validates the request before loading the trusted factory. A factory that
returns something other than `StarterHostOrchestrator` is a bounded
configuration failure. A returned session for another Host/session is rejected.

Factory or Orchestrator exceptions containing a simulated secret are not present
in stdout. No traceback, arbitrary exception body, or factory identity is
returned in the wire response.
