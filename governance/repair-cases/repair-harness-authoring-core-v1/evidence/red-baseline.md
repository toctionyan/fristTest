# Harness authoring core red baseline

Command executed from the repository root on 2026-08-23:

```bash
python3 -B skillctl.py authoring validate
```

Observed result: exit code `2`; `skillctl` rejected `authoring` as an unknown command.

The repository has a validated target-independent Workflow registry and canonical runtime, but it has no host-independent authoring entrypoint that can initialize, validate, explain, or compile open Project/Skill/Workflow declarations without ChatGPT or Codex.
