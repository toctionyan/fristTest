# Runtime trace

The candidate adapter was executed read-only against the live public GitHub
search endpoint for `toctionyan/fristTest`. It used its own scoped query builder
and parser and returned:

```json
{
  "status": "PASS",
  "issue_number": 696,
  "release_run_id": "wp08-release-31657843214",
  "release_status": "FAILED_NEEDS_CLASSIFICATION",
  "attempt": 8,
  "max_attempts": 8,
  "current_wp08_run_id": 31716787445,
  "production_closed": false
}
```

This trace made no GitHub mutation and transmitted no usable credential. It
proves the repaired acquisition path can now see the exact ledger that the live
old coordinator run `32700479383` silently missed.

