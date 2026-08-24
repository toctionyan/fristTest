# Regression

Current-source verification results:

- `skill-static`: PASS.
- `skill-unit`: PASS; 850 tests.
- `skill-host-integration`: PASS.
- `skill-security`: PASS; 7 tests.
- `project-compatibility-smoke`: PASS; 671 protected files, no drift.
- related Host/Starter/Runtime/TaskRun/Dispatcher suite: PASS; 63 tests.
- `git diff --check`: PASS.

The unit count increased from 841 at #2096 closure to 850 after adding the nine
Host transport tests.
