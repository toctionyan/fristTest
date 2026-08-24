# Counterexamples

PASS.

- Missing or malformed `release/MANIFEST.json` returns `FAIL`.
- A mismatched nonempty-repository marker returns
  `nonempty_repository_unrelated`.
- Malformed remote release content, wrong repository identity, ambiguous HTTP
  permission failures, cross-origin pagination, and seal tampering remain
  rejected.
- `.venv`, `node_modules`, `.pytest_cache`, `__pycache__`, symlinks outside
  `.git`, and real `.env` files remain forbidden.
- Public repository metadata remains `BLOCKED_BY_ENVIRONMENT` unless the
  operator supplies `--allow-public`.
