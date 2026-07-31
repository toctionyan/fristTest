from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.runtime.profile import get_runtime_profile
from agent_core.transaction.active_draft import get_active_draft_id
from tests.support.paths import agent_root


def test_retired_profile_selectors_do_not_activate_runtime(monkeypatch):
    monkeypatch.delenv("APP_PROFILE", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOCAL_DEV", "true")
    assert get_runtime_profile(strict=False) is None
    with pytest.raises(RuntimeError, match="APP_PROFILE is required"):
        get_runtime_profile(strict=True)


def test_only_canonical_draft_pointer_is_read():
    assert get_active_draft_id({"pending_offer_handle": "retired"}) is None
    assert get_active_draft_id({"active_draft_id": "draft:1"}) == "draft:1"


def test_serving_source_has_no_retired_checkpoint_adapter():
    root = agent_root(__file__)
    assert not (root / "src" / "agent_core" / "transaction" / "checkpoint_migration.py").exists()
    scanned = [root / "src", root / "app"]
    forbidden = ("pending_offer_handle", "PROFILE_MIGRATION_COMPAT")
    offenders: list[str] = []
    for folder in scanned:
        for path in folder.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if any(marker in text for marker in forbidden):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
