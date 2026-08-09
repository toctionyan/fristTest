from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

from tests.support.paths import workspace_root


def _release_artifact_module(root: Path):
    path = root / "scripts" / "release_artifact.py"
    spec = importlib.util.spec_from_file_location("release_artifact_lock_contract", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_specifier(raw: str) -> str:
    from packaging.specifiers import SpecifierSet

    return str(SpecifierSet(raw))


def _canonical_requirement(raw: str) -> tuple[str, tuple[str, ...], str]:
    from packaging.requirements import Requirement

    requirement = Requirement(raw)
    return (
        requirement.name.lower().replace("_", "-"),
        tuple(sorted(requirement.extras)),
        _canonical_specifier(str(requirement.specifier)),
    )


def _locked_project_requirements(project: dict, project_name: str) -> tuple[set[tuple[str, tuple[str, ...], str]], set[tuple[str, tuple[str, ...], str]]]:
    packages = project.get("package", [])
    current = next(
        package
        for package in packages
        if package.get("name") == project_name and package.get("source") == {"virtual": "."}
    )
    metadata = current.get("metadata", {})

    def convert(entries: list[dict]) -> set[tuple[str, tuple[str, ...], str]]:
        return {
            (
                str(entry["name"]).lower().replace("_", "-"),
                tuple(sorted(entry.get("extras", []))),
                _canonical_specifier(str(entry.get("specifier", ""))),
            )
            for entry in entries
        }

    return (
        convert(metadata.get("requires-dist", [])),
        convert(metadata.get("requires-dev", {}).get("dev", [])),
    )


def test_python_lockfiles_are_current_and_release_visible() -> None:
    import tomllib

    root = workspace_root(__file__)
    uv = shutil.which("uv")
    assert uv, "uv is required to verify locked Python projects"
    projects = {
        "services/agent-service": "ecommerce-agent-service",
        "services/business-service": "ecommerce-business-service",
    }
    expected = {f"{relative}/uv.lock" for relative in projects}

    for relative, project_name in projects.items():
        project_dir = root / relative
        lock = project_dir / "uv.lock"
        pyproject = project_dir / "pyproject.toml"
        assert lock.is_file(), f"missing lock file: {lock.relative_to(root)}"

        project_data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        lock_data = tomllib.loads(lock.read_text(encoding="utf-8"))
        assert lock_data.get("requires-python") == project_data["project"]["requires-python"].replace(",", ", ")

        expected_runtime = {
            _canonical_requirement(requirement)
            for requirement in project_data["project"].get("dependencies", [])
        }
        expected_dev = {
            _canonical_requirement(requirement)
            for requirement in project_data.get("dependency-groups", {}).get("dev", [])
        }
        locked_runtime, locked_dev = _locked_project_requirements(lock_data, project_name)
        assert locked_runtime == expected_runtime, (
            f"runtime dependency metadata in {lock.relative_to(root)} does not match pyproject.toml\n"
            f"missing={sorted(expected_runtime - locked_runtime)}\n"
            f"unexpected={sorted(locked_runtime - expected_runtime)}"
        )
        assert locked_dev == expected_dev, (
            f"dev dependency metadata in {lock.relative_to(root)} does not match pyproject.toml\n"
            f"missing={sorted(expected_dev - locked_dev)}\n"
            f"unexpected={sorted(locked_dev - expected_dev)}"
        )

        env = dict(__import__("os").environ)
        # uv must compare against the same public registry identity recorded in
        # uv.lock. A developer-specific mirror may expose a different URL even
        # when the committed lock is current.
        env["UV_INDEX_URL"] = "https://pypi.org/simple"
        completed = subprocess.run(
            [uv, "lock", "--check", "--offline", "--python", sys.executable],
            cwd=project_dir,
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert completed.returncode == 0, (
            f"uv rejected current lock file: {lock.relative_to(root)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )

    release_artifact = _release_artifact_module(root)
    selected = {
        relative.as_posix()
        for _source, relative in release_artifact.iter_source_files(root)
    }
    assert expected.issubset(selected), sorted(expected - selected)

    for workflow_name in ("quality.yml", "release.yml"):
        workflow = (root / ".github" / "workflows" / workflow_name).read_text(
            encoding="utf-8"
        )
        assert workflow.count("uv sync --locked --all-groups") >= 2, workflow_name
