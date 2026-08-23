# Regression evidence

Related integration command covered the new suite plus Host bridge, Starter
selection/runtime, TaskRun bridge, Provider adapter, and Harness invocation
suites: PASS, 45 tests.

Profile results:

- `skill-static`: PASS;
- `skill-unit`: PASS, 837 tests in the complete discovery suite;
- `skill-host-integration`: PASS;
- `skill-security`: PASS, 7 tests;
- `project-compatibility-smoke`: PASS, 671 protected files and no drift.

The final contract verification reruns all five required profiles against the
closed candidate fingerprint before the contract can close.

