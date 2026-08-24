# WP-08 exact ledger discovery red baseline

Observed against public repository `toctionyan/fristTest` on 2026-08-24:

- Release ledger issue `#696` is open with contract `wp08-release-run@1`, status
  `FAILED_NEEDS_CLASSIFICATION`, attempt `8`, maximum attempts `8`, and current
  WP-08 run ID `31716787445`.
- Repository owner `toctionyan` posted the exact supported command
  `/wp08 retire attempt_budget_exhausted run=31716787445` as issue comment
  `5391890867`.
- The comment triggered `wp08-release-coordinator` run `32700479383` (run
  number `5490`) on protected `main@7f7d637496bc03518189e58958c4b677dcc8834e`.
- The coordinator completed successfully without updating or closing issue
  `#696`; its machine state remained unchanged.
- `GitHubAPI.list_issues()` enumerates at most pages 1 through 10 with 100 rows
  per page. The tenth page of the live repository ended at issue number `958`.
  GitHub rejected page 11 with HTTP 422, so issue `#696` cannot be discovered by
  this unfiltered enumeration.

Expected: the bounded ReleaseRun discovery query finds every open machine ledger
matching the exact WP-08 title/body contract, fails closed on incomplete or
over-limit results, and lets an authorized issue-comment event act on its exact
current ledger.

Actual: the unfiltered first-1000 issue scan silently returns no active ledger,
so the valid retirement command is ignored and a later authorization could
incorrectly create a second active ReleaseRun.

