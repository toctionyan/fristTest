# Counterexamples

- A concurrent conflicting publisher receives `FileExistsError` at the atomic
  pathname claim and then fails semantic equality validation.
- It cannot call `os.replace`, delete the winner or publish partial bytes.
- An identical retry remains idempotent after re-reading the winner.
- The existing sequential conflicting-resubmission and stale Skill tests remain
  green.

