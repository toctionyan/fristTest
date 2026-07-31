# Scope Planner Review — B10b Persistence/Profile Boundary

Decision: **PASS**

## Reviewed scope

The migration is limited to the six paths frozen in the active contract:

1. `services/agent-service/src/agent_core/kernel/profile.py`
2. `services/agent-service/src/agent_core/runtime/profile.py`
3. `services/agent-service/src/agent_core/persistence/database_settings.py`
4. `services/agent-service/src/agent_core/persistence/thread_store.py`
5. `services/agent-service/tests/architecture/test_persistence_profile_boundary_scc.py`
6. `docs/architecture/V20_17_B10_PERSISTENCE_PROFILE_BOUNDARY.md`

## Ownership result

- `agent_core.kernel.profile` owns the only implementation of `RuntimeProfile`, diagnostics, `APP_PROFILE` parsing, strict/fail-closed resolution, and verifier-mode resolution.
- `agent_core.runtime.profile` is an explicit compatibility export and does not define a second profile authority.
- Persistence imports the Kernel contract directly and no longer depends on Runtime.
- Database defaults, schema-creation policy, tenant ownership and protected-profile behavior remain under their previous owners.

## Architecture result

The official architecture convergence gate reports `PASS_WITH_DEBT / REDUCED` and the main SCC is reduced from five packages to four:

`lifecycle / presentation / runtime / transaction`

`persistence` and every package removed in B1–B9 remain outside the SCC. The debt baseline was not edited.

## Verification

- Central Quick: 18/18 required gates PASS.
- Agent tests: 645 passed, 6 deselected.
- Business tests: 28 passed, 2 deselected.
- Frontend: 28 tests PASS; production build PASS.
- HTTP lifecycle and real Chromium product journey PASS.
- P1 claim `PERSISTENCE-PROFILE-BOUNDARY-SCC-001`: VERIFIED.

No out-of-scope product modification or parallel implementation was identified.
