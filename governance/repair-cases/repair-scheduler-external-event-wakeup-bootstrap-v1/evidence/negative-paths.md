# Negative-path evidence

- Provider authentication and payload normalization remain the responsibility
  of the trusted integration listener; the generic Scheduler accepts only its
  closed normalized request contract.
- Ingestion reads the canonical Host Session and exact latest TaskRun external
  wait checkpoint before writing an immutable, deterministically identified
  event.
- Wake-up resolves only safe relative `file:` references beneath configured
  `.harness` roots and revalidates the event seal before use.
- A per-Session lock and immutable reservation prevent concurrent delivery from
  replaying a one-shot wait.
- Recovery delegates execution state to the existing Host `RECONCILE` command
  and otherwise requires exact durable TaskRun evidence; it never guesses.
- The Scheduler performs no provider polling, background sleep loop, Workflow
  selection, graph execution, Human Gate approval, write authorization,
  completion decision, GitHub merge, or deployment.
- No customer application source or dependency is modified.
