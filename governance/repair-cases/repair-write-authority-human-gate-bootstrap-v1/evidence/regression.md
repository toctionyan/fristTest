# Regression

`skill-control-plane` PASS under the locked environment after the final implementation diff:

- `skill-static`: PASS
- `skill-unit`: PASS, 862 tests
- `skill-host-integration`: PASS
- `skill-security`: PASS
- `project-compatibility-smoke`: PASS, 671 protected files, zero drift

The focused Host/runtime regression selection passed 68 tests. JSON schema parsing, Python compilation, `git diff --check`, concrete Host initialization, root Host OPEN, and explicit human-decision authoring also passed.
