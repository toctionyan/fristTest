# Red baseline

Baseline: merge commit `c51e55c594bbae25606fa860490069bcd7e3a587`.

Command:

```text
python3 -B skillctl.py host </dev/null
```

Observed result: exit code `2`; argparse rejects `host` as an invalid root
command. The repository has a durable `StarterHostOrchestrator`, but there is no
versioned Host command envelope, no exact dispatcher, and no stable stdio/file
CLI for ChatGPT or Codex wrappers.

This is a transport gap. It does not justify another runtime, semantic router,
write guard, completion authority, or merge path.
