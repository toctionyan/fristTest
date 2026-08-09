#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

root = Path("candidate").resolve()
baseline_path = root / "skill-system/registry/product-source-baseline.json"
baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
protected_roots = tuple(str(value) for value in baseline["protected_roots"])
raw = subprocess.check_output(["git", "ls-files", "-z", "--", *protected_roots], cwd=root)
tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
current = {relative: hashlib.sha256((root / relative).read_bytes()).hexdigest() for relative in tracked}
old = {str(key): str(value) for key, value in baseline["files"].items()}
expected = {
    "services/agent-service/.env.example",
    "services/agent-service/scripts/verify_preprod_conversation_smoke.py",
    "services/agent-service/src/agent_core/config.py",
    "services/agent-service/src/agent_core/model_calls/gateway.py",
}
changed = {path for path in set(current) | set(old) if current.get(path) != old.get(path)}
if changed != expected:
    raise SystemExit("unexpected_protected_delta:" + json.dumps({"expected": sorted(expected), "actual": sorted(changed)}, sort_keys=True))
if len(old) != len(current):
    raise SystemExit(f"unexpected_protected_file_count:old={len(old)}:current={len(current)}")
prior_release = baseline.get("source_release_sha256")
baseline["generated_from"] = "git:" + os.environ["PRODUCT_HEAD"]
baseline["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
baseline["file_count"] = len(current)
baseline["files"] = dict(sorted(current.items()))
if baseline.get("source_release_sha256") != prior_release:
    raise SystemExit("source_release_sha256_changed")
baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
report = {
    "status": "WP08_NEW_RELEASE_ATTEMPT3_BASELINE_GENERATED",
    "base_sha": os.environ["BASE_SHA"],
    "product_head": os.environ["PRODUCT_HEAD"],
    "changed_protected_paths": sorted(changed),
    "source_release_sha256_preserved": prior_release,
    "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
}
(root / "wp08-attempt3-baseline-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
