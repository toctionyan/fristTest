# Focused tests

Command:

```text
PYTHONPATH=services/agent-service/.venv/lib/python3.12/site-packages python -m pytest -q skill-system/tests/test_starter_runtime.py skill-system/tests/test_customer_agent_starter.py skill-system/tests/test_workflow_dispatcher.py skill-system/tests/test_skill_invocation_integrity.py skill-system/tests/test_workflow_activation_authority.py skill-system/tests/test_langgraph_workflow_runtime.py skill-system/tests/test_workflow_taskrun_bridge.py skill-system/tests/test_harness_invocation.py skill-system/tests/test_harness_authoring.py skill-system/tests/test_harness_composition.py skill-system/tests/test_full_development_child_runtime.py
```

Result: PASS, 86 tests and 20 subtests. The suite exercises package verification,
registration seals, strict routing, activation, real Host receipts, injected
Provider dispatch, write Guard enforcement, durable start/wait/resume, and
TaskRun END-to-VALIDATING projection.

All seven bundled `SKILL.md` directories also passed the Skill Creator
`quick_validate.py` validator.
