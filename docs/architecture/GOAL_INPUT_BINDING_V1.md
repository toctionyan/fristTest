# Goal Input Binding v1

## 新增项

`goal-input-binding@1` is the semantic declaration contract for new turns. The model declares Goals, canonical requested outputs, literal evidence, conditions, and typed input sources. It cannot declare executable dependency edges.

The deterministic Typed Goal Graph compiler is the sole current-turn dependency writer:

- `current_goal_output` produces one semantic dataflow edge from consumer to producer.
- a Condition AST `goal_output` operand produces one `result_condition` edge.
- `current_text` and `visible_result_ref` are verified symbolic inputs but produce no current-turn edge.
- discourse order, shared topic, and shared subject never create an edge by themselves.

`GoalOutputRef` is extended with producer Goal, canonical output identity, resource type, cardinality, target identity, scope, semantic contract digest, and proof digest so a consumer can reference a not-yet-produced current-turn result without weakening identity checks.

## 唯一职责

`FrozenSemanticContract` validates and signs bindings before compilation. The compiler performs no language interpretation, capability selection, target guessing, or execution authorization. Pre-tool planning, workflow validation, and execution policy project dependencies from the sealed graph for typed contracts. Model alignment output is read-only audit evidence.

`GoalOutputRef` reuse is bound to producer Goal, canonical output identity, resource type, cardinality, target identity, scope, semantic contract digest, and proof digest. A mismatch fails closed. In particular, output for order `10002` cannot authorize work on order `10003`, and a collection cannot silently become a single object.

The input-binding contract owns only symbolic input provenance. The Typed Goal Graph compiler alone owns current-turn dependency edges. Capability matching, business facts, transaction authorization, and completion remain with their existing owners.

## 替换或删除项

- Replace provider-authored `depends_on` with verified `input_bindings` for every new semantic declaration.
- Replace frozen raw dependency claims for new contracts with edges compiled deterministically from `current_goal_output` bindings and Condition AST `goal_output` operands.
- Replace pre-tool and workflow reliance on model or shadow-plan dependency fields with projections from the sealed Typed Goal Graph.
- Delete the Goal patch path that could restore or modify raw dependency edges.
- Keep no model verifier, nearest-match rule, or legacy selector as a second live dependency writer.

## 删除证据

- The provider schema does not expose `depends_on` and requires `input_bindings`.
- Freezing a new declaration strips or rejects raw dependency authority; a typed contract cannot obtain an edge without a verified binding or Condition AST operand.
- The compiled graph produces exactly one edge per `current_goal_output` binding, while shared text subjects and `visible_result_ref` inputs produce no current-turn edge.
- Pre-tool execution and workflow validation re-project dependencies from the sealed graph, so tampering with a shadow plan cannot alter scheduling.
- Goal state patches cannot write `depends_on`.
- Counterexamples reject a collection-to-single projection and reject using order `10002` output to authorize order `10003`.

## 历史兼容边界

Historical frozen contracts without `input_bindings` may retain `depends_on` as an explicitly version-gated, read-only compatibility claim. That branch cannot accept new provider writes, be restored through Goal patches, override a typed graph verdict, or grant new execution authority.

Compatibility may be deleted after all persisted historical checkpoints have been migrated or expired and replay evidence shows no supported reader requires the legacy contract. New schemas, fixtures, smoke checks, schedulers, and execution paths must never use the legacy field as a writer.

## 回滚

Rollback is atomic across provider schema, semantic freeze, graph compilation, workflow/pre-tool projection, execution policy, and executable fixtures. A partial rollback that restores `depends_on` beside `input_bindings` is forbidden because it recreates dual authority.

This migration does not authorize a WP-08 run, repository push, pull request, production activation, or `production_closed` transition.

## 验证

- The immutable RED oracle must fail on exact `origin/main` because the provider schema lacks `input_bindings`, then pass on the candidate without changing the oracle.
- Focused tests cover schema/freeze, graph compilation, condition edges, historical read-only compatibility, graph tamper rejection, execution blocking, exact Goal output identity, cardinality, and target identity.
- Full Agent runtime, context, architecture, presentation, support, and transaction suites must remain green.
- `compileall`, `git diff --check`, deterministic diff review, workspace doctor, dependency lock validation, frontend tests/build, and Product Quick must pass before contract closure.
- Local offline verification must clear ambient `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` variants so provider-construction tests exercise the locked client stack instead of a host-injected proxy transport.
- WP-08 remains outside this migration and requires separate authorization after the local governed repair is closed.
