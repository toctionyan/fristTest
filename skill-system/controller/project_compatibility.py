
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTECTED = (ROOT/"services", ROOT/"web", ROOT/"contracts")
BASELINE_FILE = ROOT/"skill-system"/"registry"/"product-source-baseline.json"


def snapshot() -> dict[str, str]:
    rows: dict[str, str] = {}
    for root in PROTECTED:
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            if any(part in {".venv","node_modules","__pycache__"} for part in path.parts):
                continue
            rows[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return rows


def main() -> int:
    current=snapshot()
    if not BASELINE_FILE.is_file():
        print(json.dumps({"status":"FAIL","errors":["missing_product_source_baseline"]},ensure_ascii=False,indent=2)); return 1
    baseline=json.loads(BASELINE_FILE.read_text(encoding="utf-8")).get("files") or {}
    changed=sorted(path for path in set(current)|set(baseline) if current.get(path)!=baseline.get(path))
    required=["scripts/quality_loop.py","scripts/repair_loop.py","architecture-skill/scripts/verify_skill_package.py"]
    missing=[path for path in required if not (ROOT/path).is_file()]
    errors=[]
    if changed: errors.append("product_source_changed:"+",".join(changed[:20]))
    if missing: errors.append("missing_legacy_entrypoints:"+",".join(missing))
    print(json.dumps({"status":"PASS" if not errors else "FAIL","errors":errors,"protected_file_count":len(current)},ensure_ascii=False,indent=2))
    return 0 if not errors else 1

if __name__=="__main__": raise SystemExit(main())
