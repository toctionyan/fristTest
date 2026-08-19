# Host–Skill Invocation Integrity

## Problem

A Skill file being present, statically valid and covered by CI does not prove a host actually discovered, selected, loaded, invoked or consumed it. This distinction matters for host-mediated behavior such as long-task status answers, automatic continuation, Human Gate interpretation and stop/completion claims.

The required lifecycle is:

```text
User request
→ Skill discovery
→ Skill selection
→ canonical Skill load
→ Skill invocation / entrypoint execution
→ output binding into host context
→ governed action or deterministic response
→ durable skill-invocation-receipt@1
```

Static host conformance remains useful, but it is only evidence that the adapters and guards *can* run. It is not runtime invocation evidence.

## Runtime receipt

`skill-system/controller/skill_invocation.py` defines `skill-invocation-receipt@1`.

A PASS receipt binds:

- request class;
- required and selected Skill identity;
- canonical Skill path and current SHA256;
- concrete entrypoint;
- discovery / selection / load / execution / output-binding phases;
- TaskRun and/or Change Contract identity when applicable;
- output schema, output digest and evidence reference;
- whether the entrypoint produced the deterministic response payload;
- `authority_effect=false`.

A stale canonical Skill digest, wrong Skill, wrong TaskRun/change id, missing phase, malformed output identity or missing host-context binding fails closed.

### Multiple Skills in one lifecycle

A real governed lifecycle can require more than one Skill. Therefore there is deliberately **no global single `current.json` receipt**. A later Skill load must not evict `change-scope` or any other still-required invocation.

`.quality/skill-invocations/active.json` is a read-only-evidence index keyed by:

```text
request_class | selected_skill | change_id-or-- | task_id-or--
```

Each key points to one immutable per-invocation receipt and its fingerprint. Reloading the same Skill for the same subject advances only that key; loading another Skill creates another active key. The guard resolves the exact expected request class + Skill + subject, then revalidates the receipt against the current canonical Skill digest. Index tampering, missing entries and stale receipts fail closed.

## Supported write-host guard

For repository-local Codex/Claude style hosts, every writable Change Contract already requires `change-scope` by policy. The PreToolUse, PostToolUse and Stop guards now require an active PASS invocation receipt for:

```text
request_class = CHANGE_SCOPE
required_skill = selected_skill = change-scope
subject.change_id = active Change Contract change_id
loaded_sha256 = current canonical change-scope Skill digest
```

This closes the gap where `.agents/skills/change-scope/SKILL.md` could exist while the actual writable session never loaded it. Loading an additional review/domain Skill no longer removes the `change-scope` evidence.

It does **not** yet prove that every optional/domain Skill was selected correctly. That remains a separate routing-coverage problem and must not be overclaimed.

## Long-task status

`task-execution-status` is a dedicated discoverable Skill for progress/status/resume questions. Supported hosts must call:

```text
python3 -B skillctl.py task-status-project ...
```

That entrypoint runs the canonical `scripts/render_task_progress.py`, produces `execution-progress@1`, and emits a receipt bound to the TaskRun id and deterministic rendered text. GitHub jobs/runs remain evidence; TaskRun remains lifecycle authority.

## Coverage audit

| Capability | Current enforcement | Invocation drift risk after this change |
|---|---|---|
| GitHub post-merge trigger / exact-head Quality / project-convergence | Workflow code + immutable run evidence | Low; not dependent on conversational Skill discovery |
| Business transaction Grant / Attempt / Receipt | Runtime transaction authority | Low for business side-effect truth |
| Writable Change Contract scope and ChangePermit | Repository Hook + deterministic governance | Lowered: supported local hosts must also carry `change-scope` invocation receipt |
| Multi-Skill host lifecycle | Keyed active receipt index | Lowered: one Skill load cannot evict another subject-bound receipt |
| Long-task status projection | Dedicated Skill + deterministic renderer + invocation receipt | Lowered for hosts that can execute repository CLI |
| Recovery/Human Gate semantics | Code + Skill policy | Medium until every routing path is bound to explicit invocation evidence |
| Domain-specific Skill selection | Static adapters + descriptions | Still unproven at runtime unless a receipt is required for that request class |
| External ChatGPT product conversation using GitHub Connector | Outside repository-local Hook interception | **Host integration unverified**; repository code must not claim it intercepted or invoked a Skill |

## External ChatGPT boundary

Repository-local hooks cannot intercept the ChatGPT product harness or its GitHub Connector merely because `.agents`, `.claude`, or `skill-system/hooks` files exist. Therefore:

- no repository receipt may be fabricated for an external ChatGPT turn;
- an external host that cannot execute the repository CLI must report invocation as unverified;
- GitHub evidence may still be read directly, but it must not be presented as proof that the TaskRun status Skill executed;
- future product-level integration needs an explicit adapter capable of invoking the canonical repository entrypoint and returning its receipt.

## Next expansion

The receipt schema is intentionally generic. Additional request classes can be made fail-closed one at a time only after a deterministic routing contract exists. Do not replace the current gap with a hard-coded natural-language keyword router that would become a second semantic authority.
