# Focused tests

Environment: locked `services/agent-service` Python 3.12 environment.

Command:

```text
python -B -m unittest skill-system/tests/test_starter_host_transport.py
```

Result: PASS, 9 tests.

Coverage includes all nine fixed operations, closed fields and payloads, canonical
types/case, factory isolation, Host/session identity, bounded error responses, a
real Orchestrator OPEN/READ/SELECT sequence, and the root `skillctl.py host`
process boundary.

Related command:

```text
python -B -m unittest \
  skill-system/tests/test_starter_host_transport.py \
  skill-system/tests/test_starter_host_orchestrator.py \
  skill-system/tests/test_starter_runtime.py \
  skill-system/tests/test_starter_host_selection.py \
  skill-system/tests/test_host_skill_bridge.py \
  skill-system/tests/test_starter_provider_bootstrap.py \
  skill-system/tests/test_task_run.py \
  skill-system/tests/test_workflow_dispatcher.py
```

Result: PASS, 63 tests.
