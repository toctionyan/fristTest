# Focused tests

Command (locked project environment):

`UV_CACHE_DIR=/tmp/fristtest-uv-cache uv run --project services/agent-service --frozen python -B -m unittest skill-system/tests/test_provider_webhook_external_event_transport.py skill-system/tests/test_concrete_host_bootstrap.py`

Result: PASS, 17 tests.

Coverage includes valid signed `workflow_run` delivery, exact raw-body evidence,
delivery replay and concurrent duplicate serialization, persisted-evidence
tamper detection, closed GitHub header/payload validation, configured repository
binding, size limits, missing-secret blocking, Scheduler rejection propagation,
the WSGI route, root CLI delegation, bootstrap schema/fingerprint enforcement,
secret scrubbing, local-only assembly, rollback, and real Host construction.

A related Host/Runtime/Adapter/Scheduler test selection also completed PASS with
64 tests.
