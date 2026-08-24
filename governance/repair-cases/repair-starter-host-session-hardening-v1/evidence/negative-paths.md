# Negative-path evidence

- Session SHA-256 sealing is corruption evidence, not a secret, write permit,
  completion verdict, or merge grant.
- Every loaded session is checked against exact fields, immutable identity,
  canonical selection/TaskRun/runtime binding, fixed authority policies, and a
  newly derived next action.
- A pending transition is legal only in `STARTING` or `RESUMING_*`, is sealed
  before runtime dispatch, has `authority_effect=false`, and is cleared after
  successful canonical publication or an observed runtime failure.
- Reconciliation invokes a new start only for exact `CREATED/CREATED`, replays
  a resume only while the TaskRun still proves the same original wait/gate, and
  otherwise only adopts a newer non-running durable graph snapshot.
- Missing/unchanged/running snapshots block. No evidence, event, Host result,
  Human decision, Provider result, completion, or merge is fabricated.
- Graph END still projects TaskRun `VALIDATING`; automatic merge stays false.
- No customer application source, dependency, service, web, or contract file
  changes.
