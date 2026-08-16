#!/usr/bin/env python3
"""Run isolated Python test suites with machine-readable result evidence."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


# Standard tests must not silently acquire a developer's model credentials,
# database, Business Service, or production profile through the inherited shell
# or a local .env file.  Keep ordinary process variables (PATH, HOME, locale)
# but reset every application configuration namespace to a deterministic local
# contract.  Integration is deliberately separate: its declared service URLs
# are preflighted by quality_loop before this runner is invoked.
STANDARD_CONFIG_PREFIXES = (
    "ACTION_",
    "AGENT_",
    "ANSWER_RELEASE_",
    "APP_",
    "BUSINESS_",
    "CAPABILITY_",
    "CHECKPOINT_",
    "CHUNK_",
    "DATABASE_",
    "DEBUG_NODE_",
    "EMBEDDING_",
    "GOAL_ALIGNMENT_",
    "METRICS_",
    "MODEL_",
    "OPENAI_",
    "QDRANT_",
    "RAG_",
    "RETRIEVAL_",
    "SQLITE_",
    "STATE_",
    "STRICT_",
    "TRACE_",
    "UPLOAD_",
    "VECTOR_",
    "WEB_CONSOLE_",
)

# External trusted-Judge binding belongs to the top-level quality controller.
# Test subprocesses are evidence producers, not controller instances; retaining
# these variables can make nested controller contract tests validate an unrelated
# synthetic workspace against the outer CI Judge before the tested branch runs.
CONTROLLER_ONLY_ENV = (
    "SKILL_JUDGE_ROOT",
    "SKILL_JUDGE_TRUST_MODE",
)

STANDARD_ENV = {
    "APP_PROFILE": "local",
    "LOCAL_DEV": "true",
    "AGENT_DB_BACKEND": "sqlite",
    "AGENT_DB_CREATE_SCHEMA": "true",
    "CHECKPOINT_BACKEND": "sqlite",
    "CHECKPOINT_SETUP": "true",
    "AGENT_REQUIRE_AUTH": "false",
    "AGENT_AUTH_PROVIDER": "dev_headers",
    "AGENT_ALLOW_INSECURE_DEV_HEADERS": "true",
    "AGENT_BUSINESS_ADAPTER": "ecommerce_http",
    "BUSINESS_SERVICE_BASE_URL": "http://127.0.0.1:9",
    "BUSINESS_SERVICE_TOKEN": "dev-service-token",
    "BUSINESS_ACTOR_SIGNING_SECRET": "deterministic-test-business-signing-secret",
    "BUSINESS_SERVICE_TIMEOUT": "0.1",
    "BUSINESS_READ_TRANSPORT_RETRIES": "0",
    "BUSINESS_READ_RETRY_BACKOFF_MS": "0",
    "BUSINESS_REQUIRE_ACTOR_SIGNATURE": "false",
    "BUSINESS_SEED_DEMO_DATA": "false",
    "RAG_BACKEND": "local_sparse",
    "EMBEDDING_PROVIDER": "local_sparse",
    "CAPABILITY_SEMANTIC_VERIFIER_MODE": "candidate",
    "GOAL_ALIGNMENT_VERIFIER_MODE": "candidate",
    "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "candidate",
    "STATE_CONTRACT_MODE": "audit",
    "TRACE_REDACTION_MODE": "standard",
    "TRACE_RETENTION_DAYS": "30",
    # Empty values are treated as absent by python-dotenv, allowing a local
    # .env to put real credentials back into the test process.  Deliberately
    # non-secret, unreachable values keep that override impossible.
    "OPENAI_API_KEY": "deterministic-test-not-a-real-key",
    "OPENAI_API_BASE": "http://127.0.0.1:9/v1",
    "OPENAI_MODEL": "deterministic-test-model",
    "MODEL_TEMPERATURE": "0",
    "MODEL_TIMEOUT_SECONDS": "1",
    "MODEL_MAX_RETRIES": "0",
    "WEB_CONSOLE_DEV_LOGIN": "true",
    "WEB_CONSOLE_DEV_PASSWORD": "123456",
    "WEB_CONSOLE_SESSION_SECRET": "deterministic-test-session-secret",
    "AGENT_JWT_SECRET": "deterministic-test-jwt-secret-not-for-production",
}


def _locked_project_python(workspace: Path, cwd: str) -> Path | None:
    """Use the lock-created interpreter for the suite being executed.

    Project-local locked environments remain the default.  A quality controller
    may explicitly provide QUALITY_AGENT_PYTHON / QUALITY_BUSINESS_PYTHON when
    validating a preserved cross-platform development workspace whose bundled
    venv cannot run on the current host.  This is an explicit policy decision,
    never an implicit fallback to a host `python` alias.
    """
    override_name = "QUALITY_AGENT_PYTHON" if cwd == "services/agent-service" else "QUALITY_BUSINESS_PYTHON"
    override = os.getenv(override_name, "").strip()
    if override:
        # Keep a venv launcher path intact. Resolving the symlink to the base
        # interpreter loses pyvenv.cfg discovery and therefore the venv's
        # installed pytest/coverage packages.
        executable = Path(override).expanduser().absolute()
        return executable if executable.is_file() and os.access(executable, os.X_OK) else None
    executable = workspace / cwd / ".venv" / "bin" / "python"
    return executable if executable.is_file() and os.access(executable, os.X_OK) else None


def _junit_summary(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    return {
        "tests": sum(int(suite.attrib.get("tests", "0")) for suite in suites),
        "failures": sum(int(suite.attrib.get("failures", "0")) for suite in suites),
        "errors": sum(int(suite.attrib.get("errors", "0")) for suite in suites),
        "skipped": sum(int(suite.attrib.get("skipped", "0")) for suite in suites),
    }


def _run(
    workspace: Path,
    *,
    name: str,
    cwd: str,
    selector: str,
    junit_path: Path,
    coverage_path: Path,
) -> dict[str, Any]:
    project_python = _locked_project_python(workspace, cwd)
    if project_python is None:
        return {
            "name": name,
            "cwd": cwd,
            "command": [],
            "exit_code": 78,
            "duration_ms": 0,
            "stdout": "",
            "stderr": f"locked project interpreter is unavailable: {cwd}/.venv/bin/python; run uv sync --locked",
            "junit": str(junit_path),
            "coverage": str(coverage_path),
            "summary": {"tests": 0, "failures": 0, "errors": 0, "skipped": 0},
        }
    env = os.environ.copy()
    for key in CONTROLLER_ONLY_ENV:
        env.pop(key, None)
    if selector != "integration":
        for key in tuple(env):
            if key.startswith(STANDARD_CONFIG_PREFIXES):
                env.pop(key, None)
        env.update(STANDARD_ENV)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    runtime_dir = junit_path.parent / "runtime" / name
    runtime_dir.mkdir(parents=True, exist_ok=True)
    # Keep coverage's mutable SQLite data inside the evidence runtime.  A
    # preserved development workspace may contain a foreign-platform or
    # differently-owned .coverage file; tests must neither depend on nor mutate it.
    env["COVERAGE_FILE"] = str(runtime_dir / ".coverage")
    env.update(
        {
            "SQLITE_DB_PATH": str(runtime_dir / "agent.sqlite3"),
            "AGENT_DATABASE_URL": f"sqlite:///{runtime_dir / 'agent.sqlite3'}",
            "DATABASE_URL": f"sqlite:///{runtime_dir / 'agent.sqlite3'}",
            "CHECKPOINT_DB_PATH": str(runtime_dir / "checkpoints.sqlite3"),
            "VECTOR_DB_PATH": str(runtime_dir / "vector.sqlite3"),
            "UPLOAD_DIR": str(runtime_dir / "uploads"),
            "BUSINESS_DB_PATH": str(runtime_dir / "business.sqlite3"),
        }
    )
    if name == "agent-service-pytest":
        env["PYTHONPATH"] = "src:." + (f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else "")
        # Agent requires Python >=3.12. coverage.py's sys.monitoring core keeps
        # full line coverage while avoiding the order-of-magnitude tracer cost
        # seen in the large counterexample suite on Python 3.12/3.13.
        env["COVERAGE_CORE"] = "sysmon"
        coverage_args = ["--cov=agent_core", "--cov=app"]
    else:
        env.pop("COVERAGE_CORE", None)
        coverage_args = ["--cov=business_service"]
    command = [
        str(project_python),
        "-B",
        "-m",
        "pytest",
        "-q",
        "-ra",
        "-p",
        "no:cacheprovider",
        "-m",
        selector,
        f"--junitxml={junit_path}",
        *coverage_args,
        f"--cov-report=xml:{coverage_path}",
    ]
    started = time.monotonic()
    stdout_path = runtime_dir / "stdout.log"
    stderr_path = runtime_dir / "stderr.log"
    exit_code = 124
    timed_out = False
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        proc = subprocess.Popen(
            command,
            cwd=workspace / cwd,
            stdout=stdout_file,
            stderr=stderr_file,
            env=env,
            start_new_session=True,
        )
        try:
            exit_code = proc.wait(timeout=420)
        except subprocess.TimeoutExpired:
            timed_out = True
            # A test may leave descendants alive with inherited descriptors.
            # Terminate the whole session instead of only pytest so the quality
            # runner cannot hang forever while collecting captured output.
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGTERM)
                else:  # pragma: no cover - Windows fallback
                    proc.terminate()
                proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    if os.name == "posix":
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:  # pragma: no cover - Windows fallback
                        proc.kill()
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    if timed_out:
        stderr = stderr + "\npython_test_suite_timeout"
    summary = _junit_summary(junit_path)
    return {
        "name": name,
        "cwd": cwd,
        "command": command,
        "exit_code": exit_code,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "stdout": stdout,
        "stderr": stderr,
        "junit": str(junit_path),
        "coverage": str(coverage_path),
        "summary": summary,
    }


def _suite_specs(mode: str) -> list[dict[str, str]]:
    if mode not in {"standard", "integration"}:
        raise ValueError(f"unsupported Python suite mode: {mode}")
    return [
        {
            "name": "agent-service-pytest",
            "cwd": "services/agent-service",
        },
        {
            "name": "business-service-pytest",
            "cwd": "services/business-service",
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", nargs="?", default=".")
    parser.add_argument("--mode", choices=("standard", "integration"), default="standard")
    parser.add_argument("--junit-dir", required=True)
    parser.add_argument("--coverage-dir", required=True)
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    junit_dir = Path(args.junit_dir).resolve()
    coverage_dir = Path(args.coverage_dir).resolve()
    junit_dir.mkdir(parents=True, exist_ok=True)
    coverage_dir.mkdir(parents=True, exist_ok=True)
    selector = "integration" if args.mode == "integration" else "not integration and not preprod"
    suites = []
    for spec in _suite_specs(args.mode):
        artifact_name = spec["name"].removesuffix("-pytest")
        suites.append(
            _run(
                workspace,
                name=spec["name"],
                cwd=spec["cwd"],
                selector=selector,
                junit_path=junit_dir / f"{artifact_name}-{args.mode}.xml",
                coverage_path=coverage_dir / f"{artifact_name}-{args.mode}.xml",
            )
        )
    skipped = sum(item["summary"]["skipped"] for item in suites)
    failed = [item["name"] for item in suites if item["exit_code"] != 0]
    status = "PASS" if not failed and skipped == 0 else "FAIL"
    payload = {
        "status": status,
        "mode": args.mode,
        "skipped": skipped,
        "failed_suites": failed,
        "suites": suites,
        "skip_policy": "selected tests must not skip; integration tests are excluded rather than skipped in standard mode",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
