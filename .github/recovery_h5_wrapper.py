from __future__ import annotations

from pathlib import Path

H3_PATH = Path(__file__).with_name("recovery_h3_runner.py")
ORACLE_SHA = "d419911544cb84a53067bd8411974cb9bf3b69f7"
ORACLE_PATH = "services/agent-service/tests/runtime/test_goal_coverage_runtime.py"
ORACLE_BLOB_SHA = "3e4442cfa669274861a2ff90222838eef2834438"

source = H3_PATH.read_text(encoding="utf-8")

# The historical recovery workflow predates the contract-owner oracle repair and
# therefore hard-codes exactly two Agent failures in the RED baseline.  H5 keeps
# the stronger reviewed oracle frozen in both RED and GREEN views and tightens
# the harness proof to require exactly the three known A2/B semantic RED nodes.
red_hook = '    script = extract_run(name)\n'
red_replacement = '''    script = extract_run(name)\n    if name == "Reproduce successor machine RED baseline and exact failed Claim":\n        old_red_assert = "assert suites['agent-service-pytest']['summary']['failures']==2"\n        new_red_assert = "\\n".join([\n            "assert suites['agent-service-pytest']['summary']['failures']==3",\n            "agent_stdout=suites['agent-service-pytest']['stdout']",\n            "expected_red_nodes=(",\n            "    'tests/architecture/test_v2018_single_writer_recovery_oracle.py::SemanticSingleWriterInvariantTests::test_goal_declaration_prompt_is_capability_blind_before_freeze',",\n            "    'tests/architecture/test_v2018_single_writer_recovery_oracle.py::SemanticSingleWriterInvariantTests::test_static_semantic_writer_contract_does_not_require_registry_identity_alignment',",\n            "    'tests/runtime/test_goal_coverage_runtime.py::test_invalid_goal_declaration_returns_authoritative_current_user_text',",\n            ")",\n            "for red_node in expected_red_nodes:",\n            "    assert red_node in agent_stdout, red_node",\n        ])\n        if old_red_assert not in script:\n            raise AssertionError("historical RED failure-count assertion changed; fail closed")\n        script = script.replace(old_red_assert, new_red_assert, 1)\n'''
if red_hook not in source:
    raise SystemExit("H3 RED-baseline extraction point changed; fail closed")
source = source.replace(red_hook, red_replacement, 1)

needle = '''    run_script(index, name, cwd, script)\n\nPY = ROOT / "services/agent-service/.venv/bin/python"\n'''
replacement = f'''    run_script(index, name, cwd, script)\n    if name == "Generate successor Target, Claim and Decision and initialize successor Contract":\n        oracle_target = ROOT / "{ORACLE_PATH}"\n        oracle_target.parent.mkdir(parents=True, exist_ok=True)\n        oracle_target.write_text(git_show("{ORACLE_SHA}:{ORACLE_PATH}"), encoding="utf-8")\n        actual_blob = subprocess.check_output(\n            ["git", "hash-object", str(oracle_target)], cwd=WORKSPACE, text=True\n        ).strip()\n        assert actual_blob == "{ORACLE_BLOB_SHA}", (actual_blob, "{ORACLE_BLOB_SHA}")\n        assert "rederive capability-independent domain, operation, object_type and requested_outputs from current_user_input; never copy verifier semantic answers or capability identities" in oracle_target.read_text(encoding="utf-8")\n        RECORDS.append({{"index": "4A", "name": "Freeze reviewed PR649 contract-owner oracle before RED baseline", "cwd": str(ROOT), "exit_code": 0, "duration_ms": 0}})\n        persist()\n\nPY = ROOT / "services/agent-service/.venv/bin/python"\n'''

if needle not in source:
    raise SystemExit("H3 oracle insertion point changed; fail closed")
patched = source.replace(needle, replacement, 1)

# Validation-only recovery: stop after formal product Quick.  No diff-review,
# closure evidence, contract-close, merge, release, or production activation.
exec(compile(patched, str(H3_PATH) + "[H5]", "exec"), {"__name__": "__main__", "__file__": str(H3_PATH)})
