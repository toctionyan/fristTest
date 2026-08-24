# Focused tests

Command:

`services/agent-service/.venv/bin/python -m pytest -q skill-system/tests/test_wp08_release_retirement.py skill-system/tests/test_wp08_release_recovery.py skill-system/tests/test_wp08_release_coordinator.py skill-system/tests/test_wp08_m3_reconciler_authority.py skill-system/tests/test_wp08_new_release_attempt6_environment_runtime.py`

Result: `44 passed in 0.20s`.

Coverage added for:

- exact repository/state/title-scoped ReleaseRun search;
- discovery of historical ledger `#696` independently of generic issue volume;
- rejection of incomplete results;
- rejection of more than 1000 scoped candidates;
- rejection of a search candidate outside the exact ReleaseRun title prefix;
- preservation of exhausted-run retirement, actor checks, exact run ID checks,
  terminal conclusion checks, closure behavior and no-dispatch behavior.

