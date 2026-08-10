from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load_module():
    path = ROOT / "skill-system" / "controller" / "working_set.py"
    spec = importlib.util.spec_from_file_location("working_set_manifest_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_duplicate_remote_resources_collapse_before_fetch() -> None:
    module = _load_module()
    manifest = module.WorkingSetManifest(goal="inspect quality loop")
    manifest.add("scripts/quality_loop.py")
    manifest.add("scripts/quality_control/state.py")
    manifest.add("scripts/quality_loop.py")

    snapshot = manifest.snapshot()

    assert snapshot["declared_count"] == 3
    assert snapshot["unique_count"] == 2
    assert snapshot["duplicate_count"] == 1
    assert snapshot["remote_plan"][0]["resources"] == [
        "scripts/quality_loop.py",
        "scripts/quality_control/state.py",
    ]


def test_required_upgrade_does_not_create_second_fetch() -> None:
    module = _load_module()
    manifest = module.WorkingSetManifest(goal="inspect runtime")
    manifest.add("scripts/quality_control/claims.py", required=False)
    manifest.add("scripts/quality_control/claims.py", required=True)

    items = manifest.deduplicated_items()

    assert len(items) == 1
    assert items[0].required is True


def test_resources_are_grouped_by_remote_source_for_batching() -> None:
    module = _load_module()
    manifest = module.WorkingSetManifest(goal="inspect branch and files")
    manifest.add("scripts/quality_loop.py", source="github.fetch_file")
    manifest.add("scripts/quality_control/state.py", source="github.fetch_file")
    manifest.add("quality-loop-resumable-checkpoint-20260810", source="github.search_branches")

    plan = manifest.remote_plan()

    assert plan == [
        {
            "source": "github.fetch_file",
            "resources": [
                "scripts/quality_loop.py",
                "scripts/quality_control/state.py",
            ],
            "required_resources": [
                "scripts/quality_loop.py",
                "scripts/quality_control/state.py",
            ],
        },
        {
            "source": "github.search_branches",
            "resources": ["quality-loop-resumable-checkpoint-20260810"],
            "required_resources": ["quality-loop-resumable-checkpoint-20260810"],
        },
    ]


def test_frozen_manifest_rejects_late_discovery() -> None:
    module = _load_module()
    manifest = module.WorkingSetManifest(goal="bounded step")
    manifest.add("scripts/quality_loop.py")
    manifest.freeze()

    with pytest.raises(module.WorkingSetError):
        manifest.add("scripts/quality_control/state.py")
