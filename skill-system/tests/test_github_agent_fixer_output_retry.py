from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_agent_fixer.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_agent_fixer_output_retry", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load()
CONFIG = MODULE.ModelConfig("openai", "model", "https://api.openai.com/v1", "not-used")
MESSAGES = [
    {"role": "system", "content": "trusted repair contract"},
    {"role": "user", "content": "untrusted source payload"},
]


def _envelope(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


def _valid_payload() -> str:
    return json.dumps(
        {
            "summary": "small repair",
            "changes": [
                {
                    "path": "services/a.py",
                    "content": "x = 1\n",
                    "reason": "repair syntax",
                }
            ],
        }
    )


def test_call_model_retries_malformed_json_within_same_contract(monkeypatch) -> None:
    responses = [_envelope("not-json"), _envelope(_valid_payload())]
    calls: list[tuple[list[dict[str, str]], bool]] = []

    def fake_request(_config, messages, *, response_format):
        calls.append(([dict(message) for message in messages], response_format))
        return responses.pop(0)

    monkeypatch.setattr(MODULE, "_request", fake_request)

    payload = MODULE.call_model(CONFIG, MESSAGES)

    assert payload["summary"] == "small repair"
    assert len(calls) == 2
    assert calls[0][1] is True
    assert calls[1][1] is True
    assert "FORMAT RETRY 2/3" in calls[1][0][0]["content"]
    assert calls[1][0][1] == MESSAGES[1]


def test_call_model_retries_schema_violation_but_never_expands_authority(monkeypatch) -> None:
    responses = [
        _envelope(json.dumps({"summary": "missing changes"})),
        _envelope(_valid_payload()),
    ]
    seen_system_prompts: list[str] = []

    def fake_request(_config, messages, *, response_format):
        assert response_format is True
        seen_system_prompts.append(messages[0]["content"])
        return responses.pop(0)

    monkeypatch.setattr(MODULE, "_request", fake_request)

    MODULE.call_model(CONFIG, MESSAGES)

    assert len(seen_system_prompts) == 2
    retry_prompt = seen_system_prompts[1]
    assert "does not authorize any new file path" in retry_prompt
    assert "tests, governance, workflows, dependencies, secrets, or quality judges" in retry_prompt


def test_call_model_exhausts_bounded_format_attempts(monkeypatch) -> None:
    calls = 0

    def fake_request(_config, _messages, *, response_format):
        nonlocal calls
        calls += 1
        assert response_format is True
        return _envelope("still-not-json")

    monkeypatch.setattr(MODULE, "_request", fake_request)

    with pytest.raises(MODULE.FixerError, match="failed after 3 bounded attempts"):
        MODULE.call_model(CONFIG, MESSAGES)

    assert calls == MODULE.MAX_MODEL_FORMAT_ATTEMPTS == 3


def test_strict_parser_still_rejects_prose_wrapped_json() -> None:
    content = "Here is the repair:\n" + _valid_payload()
    with pytest.raises(MODULE.ModelOutputError, match="not valid JSON"):
        MODULE.parse_change_payload(content)


def test_path_violation_is_not_a_format_retry(tmp_path: Path, monkeypatch) -> None:
    allowed = tmp_path / "services" / "a.py"
    forbidden = tmp_path / "services" / "b.py"
    allowed.parent.mkdir()
    allowed.write_text("x = 1\n", encoding="utf-8")
    forbidden.write_text("y = 1\n", encoding="utf-8")

    calls = 0

    def fake_request(_config, _messages, *, response_format):
        nonlocal calls
        calls += 1
        assert response_format is True
        return _envelope(
            json.dumps(
                {
                    "summary": "attempt scope expansion",
                    "changes": [
                        {
                            "path": "services/b.py",
                            "content": "y = 2\n",
                            "reason": "not authorized",
                        }
                    ],
                }
            )
        )

    monkeypatch.setattr(MODULE, "_request", fake_request)
    payload = MODULE.call_model(CONFIG, MESSAGES)

    with pytest.raises(MODULE.FixerError, match="undeclared path"):
        MODULE.validate_changes(tmp_path, ["services/a.py"], payload)

    assert calls == 1
