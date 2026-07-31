#!/usr/bin/env python3
"""Run the Integration Quality Loop in an owned, disposable local environment.

The normal controller intentionally refuses to guess service URLs or database
credentials.  This entry point is the local product-grade counterpart: it owns
a temporary pgvector container plus isolated Agent and Business HTTP processes,
exports their exact endpoints to every Gate, runs the unchanged quality policy,
and tears everything down even when a Gate fails.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from verify_full_lifecycle_canary import AGENT_PYTHON, ProductRuntimeHarness, find_free_port
from verify_managed_postgres_recovery import run_managed_postgres_recovery


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POSTGRES_IMAGE = "pgvector/pgvector@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"


class ManagedPostgres:
    """Own one disposable pgvector container with a random local port."""

    def __init__(self) -> None:
        self.image = os.getenv("QUALITY_POSTGRES_IMAGE", DEFAULT_POSTGRES_IMAGE).strip()
        if os.getenv("PRODUCTION_CERTIFICATION_SESSION_ID") and self.image != DEFAULT_POSTGRES_IMAGE:
            raise RuntimeError("protected production certification requires the locked pgvector image digest")
        self.image_id_sha256 = ""
        self.name = f"customer-agent-quality-{os.getpid()}-{secrets.token_hex(4)}"
        self.port = find_free_port()
        self.database = "quality_loop"
        self.user = "quality_loop"
        self.password = secrets.token_urlsafe(24)
        self.started = False

    @property
    def url(self) -> str:
        return (
            "postgresql+psycopg://"
            f"{quote(self.user)}:{quote(self.password)}@127.0.0.1:{self.port}/{quote(self.database)}"
        )

    def _docker(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )

    def start(self) -> "ManagedPostgres":
        if shutil.which("docker") is None:
            raise RuntimeError("managed integration requires the docker command")
        daemon = self._docker("info", "--format", "{{.ServerVersion}}")
        if daemon.returncode != 0:
            raise RuntimeError("managed integration requires a running Docker daemon")
        started = self._docker(
            "run",
            "--detach",
            "--rm",
            "--name",
            self.name,
            "--publish",
            f"127.0.0.1:{self.port}:5432",
            "--env",
            f"POSTGRES_DB={self.database}",
            "--env",
            f"POSTGRES_USER={self.user}",
            "--env",
            f"POSTGRES_PASSWORD={self.password}",
            self.image,
            timeout=180,
        )
        if started.returncode != 0:
            raise RuntimeError(f"failed to start managed pgvector: {started.stderr.strip()[-1200:]}")
        self.started = True
        inspected = self._docker("inspect", "--format", "{{.Image}}", self.name)
        image_id = inspected.stdout.strip().lower()
        if inspected.returncode != 0 or not __import__("re").fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RuntimeError("managed pgvector image identity is unavailable")
        self.image_id_sha256 = image_id

        deadline = time.monotonic() + 75
        last_error = ""
        while time.monotonic() < deadline:
            ready = self._docker(
                "exec",
                self.name,
                "pg_isready",
                "--username",
                self.user,
                "--dbname",
                self.database,
                timeout=5,
            )
            if ready.returncode == 0:
                return self
            last_error = (ready.stderr or ready.stdout).strip()
            inspect = self._docker("inspect", "--format", "{{.State.Running}}", self.name)
            if inspect.returncode != 0 or inspect.stdout.strip() != "true":
                logs = self._docker("logs", "--tail", "80", self.name)
                raise RuntimeError(f"managed pgvector exited before ready: {logs.stderr[-2000:]}")
            time.sleep(0.4)
        raise RuntimeError(f"managed pgvector did not become ready: {last_error[-1000:]}")

    @property
    def image_evidence(self) -> dict[str, str]:
        return {
            "container_image_reference": self.image,
            "container_image_id_sha256": self.image_id_sha256,
        }

    def stop(self) -> None:
        if self.started:
            self._docker("rm", "--force", self.name, timeout=20)
            self.started = False

    def __enter__(self) -> "ManagedPostgres":
        try:
            return self.start()
        except Exception:
            self.stop()
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.stop()


def _controller_command(args: argparse.Namespace) -> list[str]:
    command = [
        str(AGENT_PYTHON),
        "-B",
        str(ROOT / "scripts/quality_loop.py"),
        "--workspace-root",
        str(ROOT),
        "--policy",
        args.policy,
        "--mode",
        "integration",
        "--target",
        str(Path(args.target).expanduser().resolve()),
        "--baseline-evidence",
        str(Path(args.baseline_evidence).expanduser().resolve()),
    ]
    for flag, value in (
        ("--evidence-dir", args.evidence_dir),
        ("--state-dir", args.state_dir),
        ("--rerun-from", args.rerun_from),
    ):
        if value:
            resolved = value if flag == "--rerun-from" else str(Path(value).expanduser().resolve())
            command.extend([flag, resolved])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Integration Quality Loop with owned Postgres, Agent, and Business services."
    )
    parser.add_argument("--policy", default="governance/quality-loop-policy.json")
    parser.add_argument("--target", required=True)
    parser.add_argument("--baseline-evidence", required=True)
    parser.add_argument("--evidence-dir")
    parser.add_argument("--state-dir")
    parser.add_argument("--rerun-from")
    args = parser.parse_args()

    if not AGENT_PYTHON.is_file():
        print(f"managed integration dependency is missing: {AGENT_PYTHON}", file=sys.stderr)
        return 78

    try:
        with ManagedPostgres() as postgres, ProductRuntimeHarness(persistence_url=postgres.url) as product:
            environment = product.env.copy()
            # The owned public Agent was already started against the local
            # deterministic provider.  Do not leak that provider identity into
            # the controller: the configured-model browser Gate must reload the
            # workspace .env itself and bind the real configured provider.
            for key in ("OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_MODEL"):
                environment.pop(key, None)
            environment.update(
                {
                    "AGENT_TEST_POSTGRES_URL": postgres.url,
                    "BUSINESS_TEST_POSTGRES_URL": postgres.url,
                    "BUSINESS_SERVICE_BASE_URL": product.business_url,
                    "BUSINESS_SERVICE_TOKEN": "dev-service-token",
                    "AGENT_TEST_URL": product.agent_url,
                    "BUSINESS_TEST_URL": product.business_url,
                    "PRODUCT_HTTP_SMOKE_EPHEMERAL_DATA": "true",
                }
            )
            recovery = run_managed_postgres_recovery(product)
            recovery_path_raw = str(os.getenv("B16C_POSTGRES_RECOVERY_EVIDENCE") or "").strip()
            if recovery_path_raw:
                recovery_path = Path(recovery_path_raw).expanduser().resolve()
            elif args.evidence_dir:
                recovery_path = Path(args.evidence_dir).expanduser().resolve() / "managed-postgres-recovery.json"
            else:
                recovery_path = ROOT / ".quality" / "managed-postgres-recovery.json"
            recovery_path.parent.mkdir(parents=True, exist_ok=True)
            recovery_path.write_text(json.dumps(recovery, ensure_ascii=False, indent=2) + "\n")
            environment["B16C_POSTGRES_RECOVERY_EVIDENCE"] = str(recovery_path)
            completed = subprocess.run(
                _controller_command(args),
                cwd=ROOT,
                env=environment,
                check=False,
            )
            return int(completed.returncode)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"managed integration environment failed: {exc}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
