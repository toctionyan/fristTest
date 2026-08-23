# Counterexamples

PASS evidence covers:

- traversal, absolute, protected, duplicate, stale-digest, and symlink workspace paths;
- injected mid-transaction failure with rollback of the prior write;
- wrong Git parent, out-of-scope worktree change, and pre-staged index;
- missing GitHub token, exact-head mismatch, disabled merge, and a transport
  exception that attempts to leak the Authorization header;
- empty write scope, duplicate Provider IDs, non-Git workspace, and missing
  environment token;
- Customer Agent runtime remains non-complete while waiting for exact CI
  correlation after real local commit and exact-head PR evidence.
