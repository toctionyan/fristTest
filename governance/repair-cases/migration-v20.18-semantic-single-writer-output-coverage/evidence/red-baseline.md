# V20.18 Semantic Single-Writer / Exact Output Coverage RED Baseline

- Change ID: `migration-v20.18-semantic-single-writer-output-coverage`
- Architecture issue: `#550`
- Base candidate: `ffd29dde28c87e86e3f59f2bca88a0134a86273d`
- Source ReleaseRun: `#527`, Attempt 8 run `31508829048`
- Temporary RED carrier: `#551`
- Carrier final head: `cd260a4af23dc0b061aff8386faee6bb1bff7727`
- Carrier disposition: `closed`, `merged=false`, `DO NOT MERGE`
- `skill-self-validation` run: `31515143618`
- Job: `93858308174`
- Result: 299 tests executed, exactly 2 expected architecture-invariant failures.
- `quality` run: `31515143626`; failed at the Skill control-plane gate consistently with the intentional RED invariant.

## Reproduced architecture failures

1. During the goal-declaration / pre-freeze phase, `dialogue_runtime.py` exposes `capability_effect_index(capability_registry)` to the Semantic Writer. The index contains current deployment effect identities plus module-owned semantic guidance/examples.
2. The goal-declaration static prompt and `DECLARE_TURN_GOALS_SCHEMA` instruct the writer to align `requested_effect` to a currently deployed registered business-effect identity.
3. Read-only audit found validator-authority drift: alignment/granularity validators may internally judge a candidate, but current repair feedback can prescribe replacement dependency/decomposition values instead of returning violation-only evidence.

## Positive controls

- Planning provider surface remains narrowed to `declare_turn_goals`.
- `FrozenSemanticContract` remains the sole immutable formal semantic contract.
- This baseline does not authorize a new WP-08 ReleaseRun.
- No product source was changed by the retired RED carrier.
