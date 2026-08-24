# Focused tests

PASS on 2026-08-24 UTC under the locked Agent environment:

```text
python -B -m unittest
  skill-system.tests.test_write_authority_human_gate_bootstrap
  skill-system.tests.test_concrete_host_bootstrap
Result: 12 tests PASS
```

The broader Host/runtime/dispatcher/initializer selection also passed 68 tests. The full skill unit suite passed 862 tests.

Covered positive paths include exact ChangePermit-authorized workspace write, local commit, pull-request creation, concrete factory adapter injection, durable gate creation, sealed decision creation, decision resume, and the root `skillctl.py authoring human-decision` command.
