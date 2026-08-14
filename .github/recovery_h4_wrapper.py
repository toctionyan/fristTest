from __future__ import annotations

from pathlib import Path

H3_PATH = Path(__file__).with_name("recovery_h3_runner.py")
ORACLE_SHA = "d419911544cb84a53067bd8411974cb9bf3b69f7"
ORACLE_PATH = "services/agent-service/tests/runtime/test_goal_coverage_runtime.py"
ORACLE_BLOB_SHA = "3e4442cfa669274861a2ff90222838eef2834438"

source = H3_PATH.read_text(encoding="utf-8")

needle = '''    run_script(index, name, cwd, script)\n\nPY = ROOT / "services/agent-service/.venv/bin/python"\n'''
replacement = f'''    run_script(index, name, cwd, script)\n    if name == "Generate successor Target, Claim and Decision and initialize successor Contract":\n        oracle_target = ROOT / "{ORACLE_PATH}"\n        oracle_target.parent.mkdir(parents=True, exist_ok=True)\n        oracle_target.write_text(git_show("{ORACLE_SHA}:{ORACLE_PATH}"), encoding="utf-8")\n        actual_blob = subprocess.check_output(\n            ["git", "hash-object", str(oracle_target)], cwd=WORKSPACE, text=True\n        ).strip()\n        assert actual_blob == "{ORACLE_BLOB_SHA}", (actual_blob, "{ORACLE_BLOB_SHA}")\n        assert "rederive capability-independent domain, operation, object_type and requested_outputs from current_user_input; never copy verifier semantic answers or capability identities" in oracle_target.read_text(encoding="utf-8")\n        RECORDS.append({{"index": "4A", "name": "Freeze reviewed PR649 contract-owner oracle before RED baseline", "cwd": str(ROOT), "exit_code": 0, "duration_ms": 0}})\n        persist()\n\nPY = ROOT / "services/agent-service/.venv/bin/python"\n'''

if needle not in source:
    raise SystemExit("H3 insertion point changed; fail closed")
patched = source.replace(needle, replacement, 1)

# H4 is still a validation-only recovery run and must stop exactly where H3 stops:
# after formal product Quick, before diff-review/closure/contract-close.
exec(compile(patched, str(H3_PATH) + "[H4]", "exec"), {"__name__": "__main__", "__file__": str(H3_PATH)})
