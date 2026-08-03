from __future__ import annotations

import importlib.util
import json
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts" / "run_wp08_certification.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_wp08_certification", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "release").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "deployment" / "ci").mkdir(parents=True)
    (root / "VERSION").write_text("test\n", encoding="utf-8")
    (root / "release" / "MANIFEST.json").write_text("{}\n", encoding="utf-8")
    (root / "PHASE_CANDIDATE_MANIFEST.json").write_text("{}\n", encoding="utf-8")
    (root / "scripts" / "run_wp08_certification.py").write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def _config(root: Path, batches: list[dict]) -> Path:
    path = root / "deployment" / "ci" / "wp08-certification-batches.json"
    path.write_text(json.dumps({"contract": "wp08-certification-batches@1", "batches": batches}), encoding="utf-8")
    return path


def _run(tmp_path: Path, root: Path, config: Path, *, resume: bool = False):
    return MODULE.run_certification(
        workspace=root,
        config_path=config,
        evidence_dir=tmp_path / "evidence",
        state_file=tmp_path / "state" / "wp08.json",
        resume=resume,
        environment={},
    )


def test_blocked_batch_does_not_hide_later_batch(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    marker = tmp_path / "later-ran"
    config = _config(root, [
        {
            "id": "blocked",
            "timeout_seconds": 10,
            "command": ["{python}", "-c", "import json,sys; print(json.dumps({'status':'BLOCKED_BY_ENVIRONMENT'})); sys.exit(78)"],
        },
        {
            "id": "later",
            "timeout_seconds": 10,
            "command": ["{python}", "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('yes'); print('{{\"status\":\"PASS\"}}')"],
        },
    ])
    state, code = _run(tmp_path, root, config)
    assert code == 78
    assert state["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert state["batches"]["blocked"]["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert state["batches"]["later"]["status"] == "PASS"
    assert marker.read_text() == "yes"


def test_resume_skips_only_already_passed_batch(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    counter = tmp_path / "counter"
    script = (
        "from pathlib import Path; "
        f"p=Path({str(counter)!r}); n=int(p.read_text())+1 if p.exists() else 1; "
        "p.write_text(str(n)); print('{\"status\":\"PASS\"}')"
    )
    config = _config(root, [{"id": "pass", "timeout_seconds": 10, "command": ["{python}", "-c", script]}])
    state, code = _run(tmp_path, root, config)
    assert code == 0 and state["status"] == "PASS"
    state, code = _run(tmp_path, root, config, resume=True)
    assert code == 0
    assert counter.read_text() == "1"
    assert state["batches"]["pass"]["resume_action"] == "SKIPPED_ALREADY_PASS"


def test_timeout_is_killed_and_next_batch_runs(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    marker = tmp_path / "after-timeout"
    config = _config(root, [
        {"id": "slow", "timeout_seconds": 1, "command": ["{python}", "-c", "import time; time.sleep(30)"]},
        {"id": "after", "timeout_seconds": 10, "command": ["{python}", "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran'); print('{{\"status\":\"PASS\"}}')"]},
    ])
    state, code = _run(tmp_path, root, config)
    assert code == 1
    assert state["batches"]["slow"]["status"] == "TIMEOUT"
    assert state["batches"]["after"]["status"] == "PASS"
    assert marker.read_text() == "ran"


def test_resume_rejects_changed_source_identity(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    config = _config(root, [{"id": "pass", "timeout_seconds": 10, "command": ["{python}", "-c", "print('{\"status\":\"PASS\"}')"]}])
    state, code = _run(tmp_path, root, config)
    assert code == 0 and state["status"] == "PASS"
    (root / "VERSION").write_text("changed\n", encoding="utf-8")
    try:
        _run(tmp_path, root, config, resume=True)
    except MODULE.CertificationInputError as exc:
        assert "another source identity" in str(exc)
    else:
        raise AssertionError("resume must reject a changed source identity")



def test_missing_executable_is_blocked_and_later_batch_runs(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    marker = tmp_path / "after-missing"
    config = _config(root, [
        {"id": "missing", "timeout_seconds": 10, "command": [str(tmp_path / "does-not-exist")]},
        {"id": "after", "timeout_seconds": 10, "command": ["{python}", "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran'); print('{{\"status\":\"PASS\"}}')"]},
    ])
    state, code = _run(tmp_path, root, config)
    assert code == 78
    assert state["batches"]["missing"]["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert state["batches"]["after"]["status"] == "PASS"
    assert marker.read_text() == "ran"

def test_default_batch_contract_is_valid() -> None:
    batches = MODULE.load_batches(
        WORKSPACE / "deployment" / "ci" / "wp08-certification-batches.json",
        workspace=WORKSPACE,
        evidence_dir=WORKSPACE / ".quality" / "wp08-test-evidence",
        state_dir=WORKSPACE / ".quality" / "wp08-test-state",
    )
    assert [row["id"] for row in batches] == [
        "protected-environment-preflight",
        "postgres-pgvector-recovery",
        "real-model-rag",
        "browser-full-stack",
    ]
    assert all(row["timeout_seconds"] > 0 for row in batches)
