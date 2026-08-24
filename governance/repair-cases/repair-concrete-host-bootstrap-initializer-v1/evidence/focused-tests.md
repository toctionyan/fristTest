# Focused tests

Command:

`UV_CACHE_DIR=/tmp/uv-cache-concrete-host uv run --project services/agent-service --frozen python -B -m unittest skill-system/tests/test_concrete_host_bootstrap.py skill-system/tests/test_starter_provider_bootstrap.py skill-system/tests/test_harness_authoring.py skill-system/tests/test_starter_host_transport.py`

Result: PASS, 29 tests.

Coverage includes closed/fingerprinted bootstrap validation, immutable/symlink-bounded paths, no-shell project commands, credential scrubbing, local-only Provider assembly, rollback, missing-token blocking, real SQLite/Orchestrator construction, and root CLI `host-init -> host OPEN`.
