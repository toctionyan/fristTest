# Red baseline

The first Host bridge implementation used this publication sequence for a
result:

```text
if path.exists(): compare
else: _atomic_write(...)
_atomic_write -> os.replace(temporary, path)
```

Two submitters can both observe a missing result and both call `os.replace`.
The later writer overwrites the earlier immutable result instead of receiving a
conflict. This violates the result's single-assignment contract even though the
ordinary sequential conflicting-resubmission test passes.

