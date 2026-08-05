from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_agent_fixer.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_agent_fixer", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def test_json_fence_is_accepted_but_schema_is_still_required() -> None:
    payload = MODULE.parse_change_payload(
        '```json\n{"summary":"fix","changes":[{"path":"services/a.py","content":"x=1\\n","reason":"r"}]}\n```'
    )
    assert payload["summary"] == "fix"
    with pytest.raises(MODULE.FixerError):
        MODULE.parse_change_payload('{"summary":"missing changes"}')


def test_allowed_paths_are_existing_product_source_files(tmp_path: Path) -> None:
    source = tmp_path / "services" / "a.py"
    source.parent.mkdir()
    source.write_text("x = 1\n", encoding="utf-8")
    assert MODULE.validate_allowed_paths(tmp_path, ["services/a.py"]) == ("services/a.py",)

    protected = tmp_path / "governance" / "policy.json"
    protected.parent.mkdir()
    protected.write_text("{}\n", encoding="utf-8")
    with pytest.raises(MODULE.FixerError):
        MODULE.validate_allowed_paths(tmp_path, ["governance/policy.json"])

    test_file = tmp_path / "services" / "tests" / "test_a.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_a(): pass\n", encoding="utf-8")
    with pytest.raises(MODULE.FixerError):
        MODULE.validate_allowed_paths(tmp_path, ["services/tests/test_a.py"])

    manifest = tmp_path / "services" / "package.json"
    manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(MODULE.FixerError):
        MODULE.validate_allowed_paths(tmp_path, ["services/package.json"])


def test_path_normalization_does_not_hide_traversal(tmp_path: Path) -> None:
    source = tmp_path / "services" / "a.py"
    source.parent.mkdir()
    source.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(MODULE.FixerError):
        MODULE.validate_allowed_paths(tmp_path, ["services/../services/a.py"])
    with pytest.raises(MODULE.FixerError):
        MODULE.validate_allowed_paths(tmp_path, ["/services/a.py"])


def test_model_cannot_expand_immutable_repair_scope(tmp_path: Path) -> None:
    source = tmp_path / "services" / "a.py"
    other = tmp_path / "services" / "b.py"
    source.parent.mkdir()
    source.write_text("x = 1\n", encoding="utf-8")
    other.write_text("y = 1\n", encoding="utf-8")
    payload = {"changes": [{"path": "services/b.py", "content": "y = 2\n", "reason": "not allowed"}]}
    with pytest.raises(MODULE.FixerError):
        MODULE.validate_changes(tmp_path, ["services/a.py"], payload)


def test_repair_round_applies_complete_file_and_checks_syntax(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "services" / "a.py"
    source.parent.mkdir()
    source.write_text("x =\n", encoding="utf-8")
    monkeypatch.setattr(
        MODULE,
        "call_model",
        lambda _config, _messages: {
            "summary": "repair syntax",
            "changes": [{"path": "services/a.py", "content": "x = 1\n", "reason": "valid Python"}],
        },
    )
    config = MODULE.ModelConfig("openai", "model", "https://api.openai.com/v1", "not-used")
    result = MODULE.repair_round(
        workspace=tmp_path,
        failure_case={"failure_summary": "SyntaxError"},
        allowed_paths=["services/a.py"],
        diagnostics="",
        cycle=1,
        config=config,
    )
    assert result["verification_passed"] is True
    assert result["changed_paths"] == ["services/a.py"]
    assert source.read_text(encoding="utf-8") == "x = 1\n"


def test_invalid_python_never_becomes_stage2_candidate(tmp_path: Path) -> None:
    source = tmp_path / "services" / "a.py"
    source.parent.mkdir()
    source.write_text("x =\n", encoding="utf-8")
    passed, rows = MODULE.verify_changed_files(tmp_path, ["services/a.py"])
    assert passed is False
    assert rows[0]["passed"] is False
