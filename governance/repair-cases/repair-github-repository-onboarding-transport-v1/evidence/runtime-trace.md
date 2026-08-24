# Runtime trace

The focused end-to-end preflight test exercised this sequence:

1. Resolve the selected `owner/name` and an uppercase token environment-variable
   reference at the root CLI boundary.
2. Perform repository-bound HTTPS GETs for repository metadata, protected main,
   paginated Environment names, and paginated names-only Environment secrets.
3. For a nonempty repository, request the two fixed manifest paths at the exact
   returned default branch, decode GitHub multiline base64, parse closed JSON
   objects, and hash the exact candidate-manifest bytes.
4. Discard every non-name secret field and build the evaluator metadata shape.
5. Add immutable false authority fields, compute a canonical JSON SHA-256 seal,
   and atomically persist the artifact with mode `0600`.
6. Reload the closed artifact and compare its digest to the in-memory seal from
   that exact collection.
7. Pass only the validated metadata object to the existing deterministic
   evaluator with the invocation-local public-repository decision.
8. Return the evaluator status plus artifact path and seal while preserving
   `authority_effect=false`, `deploy_allowed=false`, and
   `production_closed=false`.

No GitHub mutation, secret-value access, workflow dispatch, WP-08 transition,
release, deployment, merge, or production closure occurs in the trace.
