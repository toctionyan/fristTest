from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_core.composition import get_runtime_registry
from quality.stage3_campaign_verifier import verify_campaign_case


CAMPAIGN_DIR = Path(__file__).parents[2] / "quality" / "stage3_campaigns"
CAMPAIGN_FILES = [
    CAMPAIGN_DIR / "locked_50.json",
    CAMPAIGN_DIR / "expanded_100.json",
    CAMPAIGN_DIR / "strong_context_200.json",
    CAMPAIGN_DIR / "holdout_200.json",
]


def _cases() -> list[pytest.ParameterSet]:
    rows: list[pytest.ParameterSet] = []
    for path in CAMPAIGN_FILES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["locked"] is True
        assert payload["case_count"] == len(payload["cases"])
        rows.extend(
            pytest.param(case, id=str(case["case_id"]))
            for case in payload["cases"]
        )
    assert len(rows) == 550
    return rows


@pytest.mark.parametrize("case", _cases())
def test_locked_stage3_semantic_context_campaign(case: dict) -> None:
    errors = verify_campaign_case(
        case,
        registry=get_runtime_registry().capabilities,
    )
    assert errors == []
