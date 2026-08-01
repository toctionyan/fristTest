from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_locked_python_preserves_virtualenv_entrypoint_symlink(tmp_path: Path) -> None:
    resolver = _load("locked_python_release_boundary", "scripts/locked_python.py")
    base = _executable(tmp_path / "base" / "python")
    locked = tmp_path / "services" / "agent-service" / ".venv" / "bin" / "python"
    locked.parent.mkdir(parents=True)
    locked.symlink_to(base)

    selected = resolver.locked_project_python(tmp_path, "agent", env={})

    assert selected == locked.absolute()
    assert selected != locked.resolve()


def test_quality_controller_interpolates_and_exports_locked_interpreters(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _load("quality_loop_release_boundary", "scripts/quality_loop.py")
    agent_python = Path(sys.executable).absolute()
    business_python = Path(sys.executable).absolute()
    monkeypatch.setenv("QUALITY_AGENT_PYTHON", str(agent_python))
    monkeypatch.setenv("QUALITY_BUSINESS_PYTHON", str(business_python))
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    assert controller._interpolate(
        "{python}", workspace=tmp_path, evidence_dir=evidence, mode="release"
    ) == str(agent_python)

    raw = controller._run_shell(
        tmp_path,
        evidence,
        "release",
        {
            "id": "locked-python-env",
            "argv": [
                str(agent_python),
                "-c",
                (
                    "import json, os; print(json.dumps(dict("
                    "quality=os.environ['QUALITY_PYTHON_EXECUTABLE'],"
                    "agent=os.environ['QUALITY_AGENT_PYTHON'],"
                    "business=os.environ['QUALITY_BUSINESS_PYTHON'])))"
                ),
            ],
            "timeout_seconds": 30,
        },
    )
    payload = json.loads(raw["stdout"].strip())
    assert raw["exit_code"] == 0
    assert payload == {
        "quality": str(agent_python),
        "agent": str(agent_python),
        "business": str(business_python),
    }


def test_production_release_owns_cumulative_integration_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = _load("run_production_release_owned_runtime", "scripts/run_production_release.py")
    agent_python = _executable(
        tmp_path / "services" / "agent-service" / ".venv" / "bin" / "python"
    )
    business_python = _executable(
        tmp_path / "services" / "business-service" / ".venv" / "bin" / "python"
    )

    class Postgres:
        url = "postgresql+psycopg://quality:secret@127.0.0.1:55432/quality"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Product:
        agent_url = "http://127.0.0.1:18000"
        business_url = "http://127.0.0.1:19000"
        business_service_token = "owned-token"
        env = {
            "OPENAI_API_KEY": "deterministic-child-key",
            "OPENAI_MODEL": "deterministic-child-model",
        }

        def __init__(self, *, persistence_url: str):
            assert persistence_url == Postgres.url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    managed = types.ModuleType("run_managed_quality_integration")
    managed.ManagedPostgres = Postgres
    harness = types.ModuleType("verify_full_lifecycle_canary")
    harness.ProductRuntimeHarness = Product
    monkeypatch.setitem(sys.modules, "run_managed_quality_integration", managed)
    monkeypatch.setitem(sys.modules, "verify_full_lifecycle_canary", harness)

    captured: dict[str, object] = {}

    def command_runner(command, *, cwd: Path, env: dict[str, str]) -> int:
        captured.update({"command": list(command), "cwd": cwd, "env": env})
        return 0

    source_env = {
        "OPENAI_API_KEY": "protected-real-key",
        "OPENAI_MODEL": "deepseek-v4-flash",
        "REAL_MODEL_CERTIFICATION_PROVIDER": "deepseek",
    }
    exit_code = runner._run_quality_in_owned_runtime(
        [str(agent_python), "-B", "scripts/quality_loop.py"],
        workspace=tmp_path,
        evidence_dir=tmp_path / "evidence",
        source_env=source_env,
        command_runner=command_runner,
    )

    assert exit_code == 0
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["OPENAI_API_KEY"] == "protected-real-key"
    assert env["AGENT_TEST_URL"] == Product.agent_url
    assert env["BUSINESS_TEST_URL"] == Product.business_url
    assert env["AGENT_TEST_POSTGRES_URL"] == Postgres.url
    assert env["BUSINESS_SERVICE_TOKEN"] == "owned-token"
    assert env["QUALITY_PYTHON_EXECUTABLE"] == str(agent_python.absolute())
    assert env["QUALITY_BUSINESS_PYTHON"] == str(business_python.absolute())


def test_production_components_do_not_spawn_children_with_base_sys_executable() -> None:
    for relative in (
        "scripts/verify_production_certification_bundle.py",
        "scripts/verify_production_postgres_bundle.py",
        "scripts/verify_production_browser_bundle.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "from locked_python import locked_project_python" in source
        assert "[sys.executable" not in source
