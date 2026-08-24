# Authority boundary

PASS.

- `GitHubRepositoryOnboardingTransport` owns read-only metadata acquisition and
  sealed evidence only.
- `repository_onboarding_preflight.evaluate` remains the sole admission verdict
  owner.
- `release/MANIFEST.json` supplies versioned workspace identity; it does not
  grant execution authority. Exact candidate SHA binding remains owned by main
  Quality and the WP-08 ReleaseRun coordinator.
- `.git` is ignored only as checkout metadata. No other safety exclusion was
  broadened.
- The protected Environment preflight owns credential value and endpoint
  validation.
- The WP-08 coordinator alone may create and dispatch a ReleaseRun after human
  authorization; this repair neither dispatches it nor changes ledger state.
- Production release and `production_closed=true` remain outside this repair.
