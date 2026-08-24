# Authority boundary

- `scripts/wp08_release_github.py` remains an acquisition and persistence
  adapter. The candidate changes only its ledger candidate query.
- `parse_issue_state` remains the semantic authority for accepting a
  `wp08-release-run@1` ledger body.
- `find_release_issue` remains the single zero/one/multiple active-ledger
  decision point consumed by authorization, reconciliation and recovery.
- `scripts/wp08_release_recovery.py` still owns comment-command eligibility,
  actor authorization, exact run binding and retirement.
- `scripts/wp08_release_coordinator.py` still owns new ReleaseRun authorization
  and workflow dispatch.
- GitHub remains the authority for current issue content and workflow run
  identity.
- This repair cannot mutate issue state, dispatch WP-08, close a work package,
  deploy, or set `production_closed=true`.

