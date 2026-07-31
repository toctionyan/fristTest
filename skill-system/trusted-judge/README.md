# Trusted Judge deployment

`manifest.json` fingerprints the files that define a quality verdict. Daily local
validation may use `workspace-fallback`; protected repair/certification exports a
read-only copy outside the candidate workspace:

```bash
python3 -B skill-system/controller/trusted_judge.py \
  --workspace-root . --export /absolute/path/to/trusted-judge
```

Then pass `--trusted-judge-root` and `--require-external-judge` to
`scripts/repair_loop.py`. The external Judge refuses a candidate that changed
any trust-root file after the bundle was certified.
