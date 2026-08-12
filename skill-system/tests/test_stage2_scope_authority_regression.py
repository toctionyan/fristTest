from __future__ import annotations

import json
from pathlib import Path


def test_stage2_scope_authority_regression_contract() -> None:
    """Reserved regression anchor for Stage-2 evidence/write-scope separation."""
    fixture = {
        "evidence_paths": [
            "services/agent-service/src/example.py",
            "services/agent-service/tests/test_example.py",
        ],
        "writable_paths": ["services/agent-service/src/example.py"],
        "protected_oracle_paths": ["services/agent-service/tests/test_example.py"],
        "scope_expanded": False,
    }
    assert fixture["writable_paths"] != fixture["evidence_paths"]
    assert fixture["protected_oracle_paths"]
    assert json.loads(json.dumps(fixture))["scope_expanded"] is False
    assert Path(fixture["protected_oracle_paths"][0]).name.startswith("test_")
