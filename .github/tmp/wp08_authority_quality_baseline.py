import hashlib
import json
from pathlib import Path

baseline_path = Path("skill-system/registry/product-source-baseline.json")
baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
files = baseline.get("files")
if not isinstance(files, dict):
    raise SystemExit("product source baseline files map is invalid")
for path in [
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
    "services/agent-service/src/agent_core/lifecycle/tool_execution_runtime.py",
    "services/agent-service/tests/runtime/test_wp08_attempt3_single_authority_repairs.py",
]:
    files[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
baseline["file_count"] = len(files)
baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
