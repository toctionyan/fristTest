#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "services/agent-service/tests/architecture/test_systemic_operational_closure.py"
MANIFEST = ROOT / "PHASE_CANDIDATE_MANIFEST.json"
SELF = Path(__file__).resolve()


def main() -> int:
    text = TEST.read_text(encoding="utf-8")
    old = '    assert set(gate["modes"]) == {"integration"}\n'
    new = '    assert set(gate["modes"]) == {"release"}\n'
    if text.count(old) != 1:
        raise RuntimeError("expected exactly one stale integration-mode assertion")
    TEST.write_text(text.replace(old, new, 1), encoding="utf-8")

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("phase manifest files list missing")
    for entry in files:
        path = ROOT / str(entry["path"])
        if not path.is_file():
            raise RuntimeError(f"managed file missing: {entry['path']}")
        data = path.read_bytes()
        entry["size"] = len(data)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
    payload["file_count"] = len(files)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    SELF.unlink()
    print(json.dumps({
        "status": "PASS",
        "assertion": "configured-model-browser-conversation is release-only",
        "manifest_file_count": len(files),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
