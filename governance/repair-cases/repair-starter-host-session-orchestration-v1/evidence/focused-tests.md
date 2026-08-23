# Focused test evidence

Command:

`PYTHONPATH=services/agent-service/.venv/lib/python3.12/site-packages python3 -B -m unittest -v skill-system/tests/test_starter_host_orchestrator.py`

Result: PASS, 5 tests.

The suite proves:

- one read-only natural-language selection starts exactly one TaskRun;
- two real Host Skill request/result cycles resume the same TaskRun and session;
- canonical Skill receipts do not exist before Host results and total two after
  the two validated resumes;
- Graph END projects TaskRun `VALIDATING` and
  `EVALUATE_COMPLETION_POLICY`, never `COMPLETED`;
- a mutating route stays `AWAITING_CONFIRMATION` until the exact preview digest
  is confirmed, while the session continues to state that it grants no write
  authority;
- one of two concurrent selection writers wins the revision CAS.

