#!/usr/bin/env python3
"""Independently validate one governed repair without production credentials."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO, Any

EXCLUDED_REAL_MODEL_GATES = {
    "configured-model-browser-conversation",
    "configured-model-browser-campaign",
}


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def create_deterministic_policy(source: Path, output: Path) -> dict[str, Any]:
    payload = _load(source)
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    by_id = {str(step.get("id") or ""): step for step in steps if isinstance(step, dict)}
    missing = EXCLUDED_REAL_MODEL_GATES - set(by_id)
    if missing:
        raise ValueError("deterministic overlay source is missing protected gates: " + ", ".join(sorted(missing)))
    for gate_id in EXCLUDED_REAL_MODEL_GATES:
        if by_id[gate_id].get("modes") != ["integration"]:
            raise ValueError(f"{gate_id} is no longer an integration-only real-model gate")
    filtered = [step for step in steps if str(step.get("id") or "") not in EXCLUDED_REAL_MODEL_GATES]
    remaining = {str(step.get("id") or "") for step in filtered}
    for step in filtered:
        unknown = {str(item) for item in step.get("depends_on") or []} - remaining
        if unknown:
            raise ValueError(f"deterministic overlay leaves unknown dependencies for {step.get('id')}: {sorted(unknown)}")
    result = dict(payload)
    result["steps"] = filtered
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _run_to_files(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
) -> int:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=stderr,
                text=True,
                timeout=timeout,
                check=False,
            )
            return completed.returncode
        except subprocess.TimeoutExpired:
            stderr.write(f"command timed out after {timeout} seconds\n")
            return 124


def _start_service(command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path) -> tuple[subprocess.Popen[str], IO[str]]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    return process, handle


def _stop_services(services: list[tuple[subprocess.Popen[str], IO[str]]]) -> None:
    for process, _handle in reversed(services):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 10
    for process, _handle in reversed(services):
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
    for _process, handle in services:
        handle.close()


def _wait_health(urls: list[str], services: list[tuple[subprocess.Popen[str], IO[str]]], *, timeout: int = 75) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process, _handle in services):
            return False
        healthy = True
        for url in urls:
            try:
                with urllib.request.urlopen(url, timeout=3) as response:
                    if response.status >= 400:
                        healthy = False
            except (urllib.error.URLError, TimeoutError):
                healthy = False
        if healthy:
            return True
        time.sleep(2)
    return False


def _create_integration_target(workspace: Path, *, ref: str, output: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "scripts/create_ci_quality_target.py",
            "--output",
            str(output.relative_to(workspace)),
            "--ref",
            ref,
            "--workflow",
            "quality-integration",
            "--claims-source",
            "governance/claims/v20.6.2-project-integration-certification.json",
        ],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError("failed to create deterministic integration target: " + completed.stderr[-2000:])


def _clean_environment(source: dict[str, str]) -> dict[str, str]:
    result = dict(source)
    for key in tuple(result):
        if key.startswith("GOVERNED_REPAIR_MODEL_") or key in {
            "PRODUCTION_MODEL_API_KEY",
            "PRODUCTION_EMBEDDING_API_KEY",
            "QUALITY_EVIDENCE_SIGNING_KEY",
        }:
            result.pop(key, None)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--evidence-dir")
    parser.add_argument("--quick-target", default=".quality/targets/governed-repair-quick.md")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    evidence_root = Path(args.evidence_dir or os.environ.get("QUALITY_EVIDENCE_DIR") or ".quality/evidence/governed-repair").resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    python = workspace / "services" / "agent-service" / ".venv" / "bin" / "python"
    business_python = workspace / "services" / "business-service" / ".venv" / "bin" / "python"
    if not python.is_file() or not business_python.is_file():
        raise SystemExit("locked Agent and Business Python environments are required")

    env = _clean_environment(os.environ.copy())
    quick_dir = evidence_root / "quick"
    quick_env = dict(env)
    quick_env["QUALITY_EVIDENCE_DIR"] = str(quick_dir)
    quick_code = _run_to_files(
        [str(python), "-B", "scripts/quality_loop.py", "--mode", "quick", "--target", args.quick_target],
        cwd=workspace,
        env=quick_env,
        stdout_path=evidence_root / "quick.stdout.txt",
        stderr_path=evidence_root / "quick.stderr.txt",
        timeout=5400,
    )
    if quick_code:
        print(json.dumps({"status": "QUICK_FAILED", "returncode": quick_code}))
        return quick_code

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True, capture_output=True, check=True
    ).stdout.strip()
    overlay = evidence_root / "deterministic-integration-policy.json"
    create_deterministic_policy(workspace / "governance" / "quality-loop-policy.json", overlay)
    integration_target = workspace / ".quality" / "targets" / "governed-repair-integration.md"
    _create_integration_target(workspace, ref=head, output=integration_target)

    runtime = dict(env)
    runtime.update(
        {
            "APP_PROFILE": "local",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENAI_API_KEY": "deterministic-ci-key",
            "OPENAI_API_BASE": "http://127.0.0.1:18081/v1",
            "OPENAI_MODEL": "deterministic-ci-model",
            "BUSINESS_SERVICE_TOKEN": "dev-service-token",
            "BUSINESS_REQUIRE_ACTOR_SIGNATURE": "false",
            "BUSINESS_SEED_DEMO_DATA": "true",
            "BUSINESS_SERVICE_RELOAD": "false",
            "AGENT_SERVICE_RELOAD": "false",
            "WEB_CONSOLE_DEV_LOGIN": "true",
            "AGENT_REQUIRE_AUTH": "true",
            "AGENT_AUTH_PROVIDER": "dev_token",
            "RAG_BACKEND": "local_sparse",
            "AGENT_DB_BACKEND": "sqlite",
            "CHECKPOINT_BACKEND": "sqlite",
            "SQLITE_DB_PATH": str(evidence_root / "runtime" / "agent.db"),
            "CHECKPOINT_DB_PATH": str(evidence_root / "runtime" / "checkpoints.db"),
            "BUSINESS_DB_PATH": str(evidence_root / "runtime" / "business.db"),
        }
    )
    services: list[tuple[subprocess.Popen[str], IO[str]]] = []
    try:
        services.append(
            _start_service(
                [str(python), "-m", "uvicorn", "tests.integration.model_stub:app", "--app-dir", "services/agent-service", "--host", "127.0.0.1", "--port", "18081"],
                cwd=workspace,
                env=runtime,
                log_path=evidence_root / "model-stub.log",
            )
        )
        services.append(
            _start_service(
                [str(business_python), "services/business-service/scripts/run_business_api.py"],
                cwd=workspace,
                env=runtime,
                log_path=evidence_root / "business.log",
            )
        )
        services.append(
            _start_service(
                [str(python), "services/agent-service/scripts/run_api.py"],
                cwd=workspace,
                env=runtime,
                log_path=evidence_root / "agent.log",
            )
        )
        if not _wait_health(["http://127.0.0.1:9000/health", "http://127.0.0.1:8000/health"], services):
            print(json.dumps({"status": "SERVICE_START_FAILED"}))
            return 2

        integration_env = dict(runtime)
        integration_env.update(
            {
                "BUSINESS_SERVICE_BASE_URL": "http://127.0.0.1:9000",
                "AGENT_TEST_URL": "http://127.0.0.1:8000",
                "BUSINESS_TEST_URL": "http://127.0.0.1:9000",
                "PRODUCT_HTTP_SMOKE_EPHEMERAL_DATA": "true",
                "AGENT_TEST_POSTGRES_URL": "postgresql+psycopg://agent:agent-test-password@127.0.0.1:5432/agent_test",
                "QUALITY_EVIDENCE_DIR": str(evidence_root / "integration"),
            }
        )
        integration_code = _run_to_files(
            [
                str(python),
                "-B",
                "scripts/quality_loop.py",
                "--policy",
                str(overlay),
                "--mode",
                "integration",
                "--target",
                str(integration_target.relative_to(workspace)),
            ],
            cwd=workspace,
            env=integration_env,
            stdout_path=evidence_root / "integration.stdout.txt",
            stderr_path=evidence_root / "integration.stderr.txt",
            timeout=7200,
        )
    finally:
        _stop_services(services)

    status = "PASS" if integration_code == 0 else "INTEGRATION_FAILED"
    (evidence_root / "validation-result.json").write_text(
        json.dumps(
            {
                "schema": "github-repair-validation@1",
                "status": status,
                "quick_returncode": quick_code,
                "integration_returncode": integration_code,
                "excluded_real_model_gates": sorted(EXCLUDED_REAL_MODEL_GATES),
                "source_commit": head,
                "production_closed": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": status, "integration_returncode": integration_code}))
    return integration_code


if __name__ == "__main__":
    raise SystemExit(main())
