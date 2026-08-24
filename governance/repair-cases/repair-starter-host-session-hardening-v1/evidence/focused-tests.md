# Focused test evidence

The exact project dependency environment ran:

`PYTHONPATH=services/agent-service/.venv/lib/python3.12/site-packages python3 -B -m unittest -v skill-system/tests/test_starter_host_orchestrator.py`

Result: PASS, 9 tests.

The suite now directly proves:

- persisted next-action/policy tampering is rejected even when the plain state
  digest is recomputed;
- an unknown session field is rejected by the closed validator;
- one real verified Starter external wait resumes on the exact event,
  correlation and evidence into the same TaskRun at `VALIDATING`;
- one real verified Starter Human Gate resumes on an explicit decision and
  durable evidence into the same TaskRun at `VALIDATING`;
- a crash after durable start and after durable resume execution is recovered
  from the existing TaskRun and SQLite LangGraph checkpoint;
- a crash after TaskRun entered `WORKFLOW_RUNTIME_RESUMED` but before the graph
  checkpoint advanced is classified ambiguous and becomes `BLOCKED` rather
  than replaying the step.

The related Starter/Host/LangGraph/TaskRun/Dispatcher/Provider command passed 51
tests. No test is skipped or xfailed.
