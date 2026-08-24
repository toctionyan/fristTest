# Red baseline

Base candidate: local tree `174cd36dabd381ec1e5049cc8b5954cc73e675d1`,
published as PR #2096 head `e33fc6f3655b2e9b9050ecfb1da8b48aa00c2cfc`.

The existing focused suite passes five tests, but a direct persisted-state
counterexample reproduces the missing boundary:

```json
{
  "tamper_rejected": false,
  "returned_kind": "AUTOMATIC_MERGE",
  "automatic_merge": true
}
```

The diagnostic opened a valid session, changed only the persisted
`next_action.kind` and `next_action.policy.automatic_merge`, then called
`StarterHostOrchestrator.read()`. The altered action was returned instead of
being rejected or reconstructed from canonical state.

Static inspection also proves two coverage/operation gaps:

- `test_starter_host_orchestrator.py` calls `resume_external()` only from the
  wrong `WAITING_HOST` phase and never calls `resume_human()`;
- the controller persists `STARTING` and `RESUMING_*` claims but exports no
  reconciliation operation for a process interruption after the claim.

This is a real control-plane red baseline. Existing write guards mean the
fabricated action is not itself write authority, but accepting it violates the
closed Host transport contract and can misdirect a future Host wrapper.
