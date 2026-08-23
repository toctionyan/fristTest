# Focused tests

The focused Host bridge, Host selection, LangGraph runtime, TaskRun bridge and
installed Starter runtime suite passed:

```text
PYTHONPATH=services/agent-service/.venv/lib/python3.12/site-packages \
python3 -B -m unittest \
  skill-system.tests.test_host_skill_bridge \
  skill-system.tests.test_starter_host_selection \
  skill-system.tests.test_langgraph_workflow_runtime \
  skill-system.tests.test_workflow_taskrun_bridge \
  skill-system.tests.test_starter_runtime

Ran 28 tests
OK
```

The wider Dispatcher, receipt, Provider, publication, Starter and runtime group
also passed 83 tests. Request creation was asserted not to create
`.quality/skill-invocations`; matching result resume created the canonical
receipt only through `CanonicalSkillInvocationAdapter`.

