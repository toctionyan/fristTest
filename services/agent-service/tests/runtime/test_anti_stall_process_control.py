from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from snapshot_cache import SnapshotCache
from task_harness import AntiStallTaskHarness
from working_set import WorkingSetManifest


def _manifest() -> WorkingSetManifest:
    manifest = WorkingSetManifest(goal="process-control boundary")
    manifest.add("a.py")
    return manifest.freeze()


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(17)])
def test_process_control_exceptions_are_not_reclassified_as_remote_failures(
    tmp_path: Path, exc: BaseException
) -> None:
    def reader(_ref: str, _path: str):
        raise exc

    harness = AntiStallTaskHarness(
        cache=SnapshotCache(tmp_path / "cache"),
        readers={"github.fetch_file": reader},
        ref_by_source={"github.fetch_file": "sha-1"},
    )

    with pytest.raises(type(exc)):
        harness.execute(_manifest())
