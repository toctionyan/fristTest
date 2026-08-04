# B33 provider projection repair trigger v2

This marker triggers the corrected repair workflow from the latest carrier baseline.

- Patch path normalization: `git apply -p4`.
- Canonical Runtime Schema remains strict.
- Provider projection must remain below 12,000 bytes.
- Target branch: `agent/b33-full-source-20260804`.
- Production closed: `false`.
