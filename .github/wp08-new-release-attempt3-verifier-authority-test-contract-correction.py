#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:160]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


semantic_identity_test = ROOT / "services/agent-service/tests/runtime/test_b15b1_real_model_semantic_identity_boundary.py"
replace_once(
    semantic_identity_test,
    '''def _patch_successful_semantic_runtime(monkeypatch, script, tmp_path: Path, *, invoke):
    catalog = tmp_path / "catalog.json"
''',
    '''def _patch_successful_semantic_runtime(monkeypatch, script, tmp_path: Path, *, invoke):
    # A successful semantic-certification fixture must exercise the same protected
    # independent-verifier authority now required by the live bundle.  This keeps
    # the provider-attestation test focused without reintroducing candidate-only mode.
    monkeypatch.setenv("APP_PROFILE", "preprod")
    monkeypatch.setenv("GOAL_ALIGNMENT_VERIFIER_MODE", "model")
    monkeypatch.setenv("GOAL_GRANULARITY_VERIFIER_MODE", "model")
    catalog = tmp_path / "catalog.json"
''',
)

browser_authority_test = ROOT / "services/agent-service/tests/runtime/test_b17d_protected_browser_runtime_authority.py"
replace_once(
    browser_authority_test,
    '''            "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
            "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
            "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
''',
    '''            "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
            "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
            "GOAL_GRANULARITY_VERIFIER_MODE": "model",
            "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
''',
)

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(semantic_identity_test.relative_to(ROOT)),
        str(browser_authority_test.relative_to(ROOT)),
    ],
    "reason": "align existing tests with the newly explicit protected granularity-verifier authority",
}, ensure_ascii=False, indent=2))
