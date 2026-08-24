# Counterexamples

The candidate tests prove these negative cases fail closed:

- `incomplete_results=true` raises `GitHubCoordinatorError` and cannot become
  an empty active-ledger set;
- `total_count=1001` raises the bounded-result-limit error instead of
  truncating at GitHub's search ceiling;
- a search result titled `[WP08 Repair] ...` is rejected as an out-of-scope
  candidate even if its body contains a valid ReleaseRun state block;
- unauthorized commenters, non-exhausted budgets, mismatched run IDs,
  nonterminal controller states and successful WP-08 conclusions still reject
  retirement;
- multiple active bodies are still rejected by `find_release_issue`;
- title matches without a valid `wp08-release-run@1` body remain non-authority.

No skip, xfail, weakened assertion, placeholder credential or manual issue
mutation was introduced.

