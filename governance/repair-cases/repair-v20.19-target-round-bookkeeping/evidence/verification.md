# Target Round Bookkeeping Verification

- RED on exact `origin/main` plus the immutable test overlay: `1 failed, 2 errors, 12 passed`; round-only change was rejected and no `round_bookkeeping` evidence existed.
- GREEN owning suite: `python3 -B -m unittest skill-system/tests/test_repair_governance.py` → `15 tests, OK`.
- GREEN bounded regression: repair governance, product portability, project compatibility and write-authority bootstrap → `35 passed`.
- Static verification: `compileall` and `git diff --check` passed.
- Deterministic candidate review: `repair-diff-review --decision PASS` passed.
- Negative paths prove that changing acceptance text or using a round beyond the unchanged maximum remains rejected and visible in `out_of_scope_paths`.
- No product source, Quality Policy, Claim, prior evidence, `.quality` state, WP-08 control, push, PR or production state was modified.
