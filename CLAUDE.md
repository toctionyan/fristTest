# Portable Governance · Claude Code

Read `AGENTS.md`, `skill-system/core/constitution.md`, and the relevant canonical Skill under `skill-system/skills/`.

Claude Code uses the same lifecycle as the current environment and Codex:

```bash
python3 -B skillctl.py product-init ...
python3 -B skillctl.py product-baseline
python3 -B skillctl.py product-verify --mode <contract-mode>
python3 -B skillctl.py contract-verify --result CONVERGED
python3 -B skillctl.py contract-close --result CONVERGED
```

Use `.claude/agents/product-implementer.md` as the only writable product role. Scope planner, Oracle reviewer, adversarial reviewer, and release Judge are read-only. Do not use a subagent to bypass the active contract.

For product repair, migration, or revert:

- require an explicit Quality Target and Claim;
- reject root-level product write globs;
- establish the red baseline before edits;
- never edit Target, Claim, Policy, baseline, Judge, evidence, or Skill control-plane files;
- run the profile selected by the contract through the original product Quality Loop;
- do not claim completion until the Stop Hook accepts the current verification identity.

Host-specific instructions cannot weaken the canonical contract.
