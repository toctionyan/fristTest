#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
BASELINE = ROOT / "skill-system/registry/product-source-baseline.json"
EXPECTED_PATH = "services/agent-service/tests/architecture/test_quality_loop_governance.py"
EXPECTED_PRIOR = "git:b8d0f1a4ab3bdea5493a37b76f452a1bc1e19568"
CANDIDATE_HEAD = os.environ["CANDIDATE_HEAD"]

baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
if baseline.get("generated_from") != EXPECTED_PRIOR:
    raise SystemExit(
        f"unexpected_prior_baseline:{baseline.get('generated_from')!r}"
    )
if int(baseline.get("file_count") or 0) != 558:
    raise SystemExit(f"unexpected_prior_file_count:{baseline.get('file_count')}")

protected_roots = tuple(str(value) for value in baseline.get("protected_roots") or ())
raw = subprocess.check_output(
    ["git", "ls-files", "-z", "--", *protected_roots], cwd=ROOT
)
tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
current = {
    relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    for relative in tracked
}
old = {str(key): str(value) for key, value in (baseline.get("files") or {}).items()}
changed = {
    path for path in set(current) | set(old)
    if current.get(path) != old.get(path)
}
if changed != {EXPECTED_PATH}:
    raise SystemExit(
        "unexpected_protected_delta:"
        + json.dumps(
            {"expected": [EXPECTED_PATH], "actual": sorted(changed)},
            sort_keys=True,
        )
    )
if len(current) != 558:
    raise SystemExit(f"unexpected_current_file_count:{len(current)}")

prior_release = baseline.get("source_release_sha256")
baseline["generated_from"] = "git:" + CANDIDATE_HEAD
baseline["generated_at"] = (
    datetime.now(timezone.utc)
    .replace(microsecond=0)
    .isoformat()
    .replace("+00:00", "Z")
)
baseline["file_count"] = len(current)
baseline["files"] = dict(sorted(current.items()))
if baseline.get("source_release_sha256") != prior_release:
    raise SystemExit("source_release_sha256_changed")
BASELINE.write_text(
    json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

report = {
    "status": "WP08_ATTEMPT6_SEMANTIC_ORACLE_BASELINE_REFRESH_GENERATED",
    "candidate_head": CANDIDATE_HEAD,
    "authority": "git-tracked-protected-source",
    "approved_path": EXPECTED_PATH,
    "old_sha256": old.get(EXPECTED_PATH),
    "new_sha256": current.get(EXPECTED_PATH),
    "file_count": len(current),
    "source_release_sha256_preserved": prior_release,
    "baseline_sha256": hashlib.sha256(BASELINE.read_bytes()).hexdigest(),
}
(ROOT / "wp08-attempt6-semantic-oracle-baseline-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False, indent=2))
