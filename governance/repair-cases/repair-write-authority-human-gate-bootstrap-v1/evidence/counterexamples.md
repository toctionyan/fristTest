# Counterexamples

PASS. Focused tests prove all of the following fail closed:

- a path forbidden by or absent from the active ChangePermit;
- a stale or mismatched `permit_digest`;
- a mutating request without exact pre-effect paths;
- generic pull-request merge authorization;
- an unsupported mutating capability;
- a Human Gate answer with no persisted decision file;
- a tampered decision fingerprint;
- a decision bound to another TaskRun/gate identity;
- a decision outcome absent from the verified Workflow routes;
- unsafe or drifting bootstrap/gate paths and identities.

No test weakens an existing Gate or converts a missing authority into success.
