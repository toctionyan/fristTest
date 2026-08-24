# Counterexamples

PASS evidence is in `test_concrete_host_bootstrap.py`:

- unknown bootstrap fields are rejected;
- changed content with a stale fingerprint is rejected;
- `..`, immutable Starter output paths, and unsafe symlink destinations are rejected;
- an existing installation cannot be overwritten;
- a missing declared project command rolls back all partial installation output;
- configured GitHub without the named token is blocked;
- the configured token is removed from the local project command environment;
- command text containing shell separators is not executed through a shell;
- no generated setting can grant write, completion, or merge authority.
