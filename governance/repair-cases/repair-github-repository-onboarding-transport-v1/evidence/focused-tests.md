# Focused tests

Command (locked project environment):

`UV_CACHE_DIR=/tmp/fristtest-uv-cache uv run --project services/agent-service --frozen pytest -q skill-system/tests/test_github_repository_onboarding_transport.py`

Result: PASS, 6 tests.

Coverage includes a valid private nonempty repository, exact remote workspace
identity, GitHub-style multiline base64 content, canonical seal persistence and
reload, delegation to the unchanged onboarding evaluator, Environment and
secret pagination, names-only secret retention, token non-persistence, GET-only
requests, public-repository explicit approval, missing protection, permission
ambiguity, repository mismatch, malformed content, seal tampering, and
cross-origin pagination rejection.

Root command routing was also exercised with:

`python3 -B skillctl.py repository-onboarding --help`

Result: PASS; the root control plane exposes only `collect` and `preflight`.
