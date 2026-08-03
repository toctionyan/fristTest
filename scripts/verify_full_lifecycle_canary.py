#!/usr/bin/env python3
"""Run the complete public product lifecycle against isolated local services.

The harness deliberately starts ``tests.integration.model_stub:app``,
``run_business_api.py`` and ``run_api.py`` as real HTTP processes.  The
existing ``verify_product_http_smoke.py`` then enters through login/chat and
finishes Draft input, authority, commit and Receipt verification.  No service
function is imported as a shortcut and every database lives in a temporary
directory.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "services/agent-service"
BUSINESS_ROOT = ROOT / "services/business-service"
def _resolve_python(env_name: str, locked_path: Path) -> Path:
    """Resolve a declared interpreter without assuming in-tree virtualenvs exist."""
    configured = str(os.getenv(env_name) or "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend([locked_path, Path(sys.executable)])
    for candidate in candidates:
        if candidate.is_file():
            # Preserve the virtualenv launcher path. Resolving this symlink to the
            # base interpreter discards pyvenv.cfg discovery and loses locked packages.
            return candidate.absolute()
    raise RuntimeError(
        f"no usable Python interpreter for {env_name}; checked: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


AGENT_PYTHON = _resolve_python("QUALITY_AGENT_PYTHON", AGENT_ROOT / ".venv/bin/python")
BUSINESS_PYTHON = _resolve_python("QUALITY_BUSINESS_PYTHON", BUSINESS_ROOT / ".venv/bin/python")


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_http(url: str, *, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310 - fixed local canary URL
                if 200 <= int(response.status) < 500:
                    return
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"service did not become ready at {url}: {last_error}")


def terminate_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=8)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _b64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


class ProductRuntimeEnvironmentBlocked(RuntimeError):
    """A protected runtime dependency is unavailable outside product code."""

    def __init__(self, reason: str, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = dict(diagnostics or {})


def build_ephemeral_customer_jwt(secret: str, *, lifetime_seconds: int = 14400) -> str:
    """Create a short-lived browser token for the disposable protected runtime."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({
        "sub": "u001",
        "user_id": "u001",
        "role": "customer",
        "tenant_id": "default",
        "exp": int(time.time()) + max(300, int(lifetime_seconds)),
    }, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    signature = _b64url(hmac.new(secret.encode(), signing_input.encode("ascii"), hashlib.sha256).digest())
    return f"{signing_input}.{signature}"


class ProductRuntimeHarness:
    """Own isolated processes and disposable state for one canary run.

    ``protected_preprod`` is opt-in so existing local canaries keep their
    original lightweight behaviour.  The protected mode binds the Agent,
    Checkpoint, Business service, RAG and document jobs to one PostgreSQL URL,
    disables development login, enables JWT and signed actor propagation, and
    applies migrations/fixtures through explicit management commands before
    either public service starts.
    """

    def __init__(
        self,
        *,
        deterministic_model: bool = True,
        persistence_url: str | None = None,
        protected_preprod: bool = False,
        allowed_origins: str | None = None,
    ) -> None:
        if persistence_url:
            scheme = urlsplit(persistence_url.strip()).scheme.lower()
            if scheme not in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
                raise ValueError("managed product runtime requires a PostgreSQL URL")
        if protected_preprod and not persistence_url:
            raise ValueError("protected preprod runtime requires PostgreSQL persistence")
        if protected_preprod and not str(allowed_origins or "").strip():
            raise ValueError("protected preprod runtime requires an explicit browser origin")
        self.persistence_url = persistence_url.strip() if persistence_url else None
        self.protected_preprod = bool(protected_preprod)
        self.allowed_origins = str(allowed_origins or "").strip()
        self._temporary = tempfile.TemporaryDirectory(prefix="customer-agent-product-canary-")
        self.runtime_dir = Path(self._temporary.name)
        self.document_object_root = self.runtime_dir / "document-objects"
        self.document_object_root.mkdir(parents=True, exist_ok=True)
        self.deterministic_model = deterministic_model
        self.model_port = find_free_port()
        self.business_port = find_free_port()
        self.agent_port = find_free_port()
        self.secondary_agent_port = find_free_port()
        self.model_url = f"http://127.0.0.1:{self.model_port}"
        self.business_url = f"http://127.0.0.1:{self.business_port}"
        self.agent_url = f"http://127.0.0.1:{self.agent_port}"
        self.secondary_agent_url = f"http://127.0.0.1:{self.secondary_agent_port}"
        self.jwt_secret = secrets.token_urlsafe(48) if self.protected_preprod else ""
        self.business_service_token = secrets.token_urlsafe(48) if self.protected_preprod else "dev-service-token"
        self.actor_signing_secret = secrets.token_urlsafe(48) if self.protected_preprod else ""
        self.browser_auth_token = (
            build_ephemeral_customer_jwt(self.jwt_secret) if self.protected_preprod else ""
        )
        self.processes: list[subprocess.Popen[Any]] = []
        self._logs: list[Any] = []
        self._processes_by_name: dict[str, subprocess.Popen[Any]] = {}
        self._logs_by_name: dict[str, Any] = {}
        self._launch_generation: dict[str, int] = {}
        self.env = self._build_env()

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Standalone local certification should use the same configured model
        # identity as the service runner without printing or rewriting secrets.
        for env_file in (AGENT_ROOT / ".env", BUSINESS_ROOT / ".env"):
            for key, value in dotenv_values(env_file).items():
                if value is not None:
                    env.setdefault(str(key), str(value))

        env.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "BUSINESS_SERVICE_BASE_URL": self.business_url,
            "BUSINESS_SERVICE_RELOAD": "false",
            "BUSINESS_SERVICE_HOST": "127.0.0.1",
            "BUSINESS_SERVICE_PORT": str(self.business_port),
            "AGENT_SERVICE_RELOAD": "false",
            "HOST": "127.0.0.1",
            "PORT": str(self.agent_port),
            "AGENT_TEST_URL": self.agent_url,
            "BUSINESS_TEST_URL": self.business_url,
            "PRODUCT_HTTP_SMOKE_EPHEMERAL_DATA": "true",
        })

        if self.protected_preprod:
            assert self.persistence_url is not None
            env.update({
                "APP_PROFILE": "preprod",
                "BUSINESS_SERVICE_TOKEN": self.business_service_token,
                "BUSINESS_REQUIRE_ACTOR_SIGNATURE": "true",
                "BUSINESS_ACTOR_SIGNING_SECRET": self.actor_signing_secret,
                "BUSINESS_ACTOR_SIGNATURE_TTL_SECONDS": "300",
                "BUSINESS_SEED_DEMO_DATA": "false",
                "WEB_CONSOLE_DEV_LOGIN": "false",
                "AGENT_REQUIRE_AUTH": "true",
                "AGENT_AUTH_PROVIDER": "jwt_hs256",
                "AGENT_JWT_SECRET": self.jwt_secret,
                "AGENT_TEST_JWT_SECRET": self.jwt_secret,
                "AGENT_ALLOWED_ORIGINS": self.allowed_origins,
                "AGENT_DB_BACKEND": "postgres",
                "AGENT_DATABASE_URL": self.persistence_url,
                "AGENT_DB_CREATE_SCHEMA": "false",
                "CHECKPOINT_BACKEND": "postgres",
                "CHECKPOINT_DATABASE_URL": self.persistence_url,
                "CHECKPOINT_SETUP": "true",
                "STRICT_PERSISTENCE": "true",
                "BUSINESS_DB_BACKEND": "postgres",
                "BUSINESS_DATABASE_URL": self.persistence_url,
                "RAG_BACKEND": "pgvector",
                "RAG_DATABASE_URL": self.persistence_url,
                "RAG_CREATE_SCHEMA": "false",
                "DOCUMENT_JOB_BACKEND": "sqlalchemy",
                "DOCUMENT_JOB_DATABASE_URL": self.persistence_url,
                "DOCUMENT_OBJECT_STORE_BACKEND": "shared_filesystem",
                "DOCUMENT_OBJECT_STORE_ROOT": str(self.document_object_root.resolve()),
                "STATE_CONTRACT_MODE": "strict",
                "TRACE_REDACTION_MODE": "standard",
                "TRACE_RETENTION_DAYS": "30",
                "CONVERSATION_LOCK_TTL_SECONDS": "300",
                "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
                "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
                "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
            })
            for retired in (
                "WEB_CONSOLE_DEV_PASSWORD", "SQLITE_DB_PATH", "CHECKPOINT_DB_PATH",
                "BUSINESS_DB_PATH", "DATABASE_BACKEND",
            ):
                env.pop(retired, None)
        else:
            env.update({
                "APP_PROFILE": "local",
                "BUSINESS_SERVICE_TOKEN": "dev-service-token",
                "BUSINESS_REQUIRE_ACTOR_SIGNATURE": "false",
                "BUSINESS_SEED_DEMO_DATA": "true",
                "WEB_CONSOLE_DEV_LOGIN": "true",
                "WEB_CONSOLE_DEV_PASSWORD": "123456",
                "AGENT_REQUIRE_AUTH": "true",
                "AGENT_AUTH_PROVIDER": "dev_token",
                "RAG_BACKEND": "local_sparse",
            })
            if self.persistence_url:
                env.update({
                    "AGENT_DB_BACKEND": "postgres",
                    "AGENT_DATABASE_URL": self.persistence_url,
                    "AGENT_DB_CREATE_SCHEMA": "true",
                    "CHECKPOINT_BACKEND": "postgres",
                    "CHECKPOINT_DATABASE_URL": self.persistence_url,
                    "BUSINESS_DB_BACKEND": "postgres",
                    "BUSINESS_DATABASE_URL": self.persistence_url,
                })
                for retired_local_path in ("SQLITE_DB_PATH", "CHECKPOINT_DB_PATH", "BUSINESS_DB_PATH"):
                    env.pop(retired_local_path, None)
            else:
                env.update({
                    "BUSINESS_DB_BACKEND": "sqlite",
                    "BUSINESS_DB_PATH": str(self.runtime_dir / "business.db"),
                    "AGENT_DB_BACKEND": "sqlite",
                    "CHECKPOINT_BACKEND": "sqlite",
                    "SQLITE_DB_PATH": str(self.runtime_dir / "agent.db"),
                    "CHECKPOINT_DB_PATH": str(self.runtime_dir / "checkpoints.db"),
                })

        if self.deterministic_model:
            env.update({
                "OPENAI_API_KEY": "deterministic-canary-key",
                "OPENAI_API_BASE": f"{self.model_url}/v1",
                "OPENAI_MODEL": "deterministic-canary-model",
            })
        else:
            missing = [
                name for name in ("OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_MODEL")
                if not str(env.get(name) or "").strip()
            ]
            if missing:
                raise RuntimeError(f"configured model environment is missing: {missing}")
        return env

    def runtime_authority_evidence(self) -> dict[str, Any]:
        """Return non-secret, fail-closed settings used by the public runtime."""
        verifier_modes = {
            name: self.env.get(name)
            for name in (
                "CAPABILITY_SEMANTIC_VERIFIER_MODE",
                "GOAL_ALIGNMENT_VERIFIER_MODE",
                "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE",
            )
        }
        return {
            "contract": "protected-browser-runtime-authority@1",
            "runtime_profile": self.env.get("APP_PROFILE"),
            "auth_provider": self.env.get("AGENT_AUTH_PROVIDER"),
            "dev_login_enabled": str(self.env.get("WEB_CONSOLE_DEV_LOGIN") or "").lower() == "true",
            "actor_signature_required": str(self.env.get("BUSINESS_REQUIRE_ACTOR_SIGNATURE") or "").lower() == "true",
            "agent_db_backend": self.env.get("AGENT_DB_BACKEND"),
            "checkpoint_backend": self.env.get("CHECKPOINT_BACKEND"),
            "business_db_backend": self.env.get("BUSINESS_DB_BACKEND"),
            "rag_backend": self.env.get("RAG_BACKEND"),
            "document_job_backend": self.env.get("DOCUMENT_JOB_BACKEND"),
            "document_object_store_backend": self.env.get("DOCUMENT_OBJECT_STORE_BACKEND"),
            "strict_persistence": str(self.env.get("STRICT_PERSISTENCE") or "").lower() == "true",
            "state_contract_mode": self.env.get("STATE_CONTRACT_MODE"),
            "verifier_modes": verifier_modes,
            "single_postgres_authority": len({
                self.env.get("AGENT_DATABASE_URL"),
                self.env.get("CHECKPOINT_DATABASE_URL"),
                self.env.get("BUSINESS_DATABASE_URL"),
                self.env.get("RAG_DATABASE_URL"),
                self.env.get("DOCUMENT_JOB_DATABASE_URL"),
            }) == 1,
        }

    def _run_management(
        self,
        command: list[str],
        *,
        cwd: Path,
        env_override: dict[str, str] | None = None,
        timeout: int = 300,
        environment_sensitive: bool = False,
    ) -> None:
        management_env = self.env.copy()
        if env_override:
            management_env.update(env_override)
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=management_env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            output = (completed.stdout + "\n" + completed.stderr)[-5000:]
            lowered = output.lower()
            environment_markers = (
                "http 401", "http_401", "unauthorized",
                "http 402", "http_402", "quota",
                "http 403", "http_403", "forbidden",
                "http 429", "http_429", "rate limit",
                "timed out", "timeout", "connection refused",
                "connection reset", "name or service not known",
                "temporary failure in name resolution",
            )
            if environment_sensitive and any(marker in lowered for marker in environment_markers):
                signal = next(marker for marker in environment_markers if marker in lowered)
                raise ProductRuntimeEnvironmentBlocked(
                    "protected_embedding_environment_unavailable",
                    {
                        "phase": "protected_runtime_management",
                        "command": Path(command[-1]).name,
                        "exit_code": completed.returncode,
                        "signal": signal,
                        "output_sha256_16": hashlib.sha256(output.encode("utf-8", errors="replace")).hexdigest()[:16],
                    },
                )
            raise RuntimeError(f"protected runtime management command failed: {output}")

    def _prepare_protected_runtime(self) -> None:
        if not self.protected_preprod:
            return
        self._run_management(
            [str(AGENT_PYTHON), "-B", "-m", "alembic", "-c", "migrations/alembic.ini", "upgrade", "head"],
            cwd=AGENT_ROOT,
            timeout=600,
        )
        self._run_management(
            [str(BUSINESS_PYTHON), "-B", str(BUSINESS_ROOT / "scripts/seed_ephemeral_fixture.py")],
            cwd=BUSINESS_ROOT,
            env_override={"BUSINESS_EPHEMERAL_FIXTURE": "true"},
        )
        self._run_management(
            [str(AGENT_PYTHON), "-B", str(AGENT_ROOT / "scripts/seed_ephemeral_rag_fixture.py")],
            cwd=AGENT_ROOT,
            env_override={"AGENT_EPHEMERAL_RAG_FIXTURE": "true"},
            timeout=900,
            environment_sensitive=True,
        )

    def _start(
        self,
        name: str,
        command: list[str],
        *,
        env_override: dict[str, str] | None = None,
    ) -> subprocess.Popen[Any]:
        active = self._processes_by_name.get(name)
        if active is not None and active.poll() is None:
            raise RuntimeError(f"product runtime process is already active: {name}")
        generation = int(self._launch_generation.get(name, 0)) + 1
        self._launch_generation[name] = generation
        log = (self.runtime_dir / f"{name}-{generation}.log").open("w", encoding="utf-8")
        self._logs.append(log)
        self._logs_by_name[name] = log
        process_env = self.env.copy()
        if env_override:
            process_env.update(env_override)
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=process_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self.processes.append(process)
        self._processes_by_name[name] = process
        return process

    def _stop_named(self, name: str) -> None:
        process = self._processes_by_name.pop(name, None)
        if process is not None:
            terminate_process(process)
            if process in self.processes:
                self.processes.remove(process)
        log = self._logs_by_name.pop(name, None)
        if log is not None and not log.closed:
            log.close()

    def _start_model(self) -> None:
        self._start("model", [
            str(AGENT_PYTHON), "-m", "uvicorn", "tests.integration.model_stub:app",
            "--app-dir", str(AGENT_ROOT), "--host", "127.0.0.1", "--port", str(self.model_port),
        ])
        wait_http(f"{self.model_url}/openapi.json")

    def _start_business(self) -> None:
        self._start("business", [
            str(BUSINESS_PYTHON), str(BUSINESS_ROOT / "scripts/run_business_api.py"),
        ])
        wait_http(f"{self.business_url}/health")

    def _start_agent(self) -> None:
        self._start("agent", [
            str(AGENT_PYTHON), str(AGENT_ROOT / "scripts/run_api.py"),
        ])
        wait_http(f"{self.agent_url}/health", timeout=60)

    def start_secondary_agent(self) -> str:
        """Start a second public Agent process against the same PostgreSQL authority."""
        if not self.persistence_url:
            raise RuntimeError("secondary Agent certification requires PostgreSQL persistence")
        self._start(
            "agent-secondary",
            [str(AGENT_PYTHON), str(AGENT_ROOT / "scripts/run_api.py")],
            env_override={"PORT": str(self.secondary_agent_port), "AGENT_TEST_URL": self.secondary_agent_url},
        )
        wait_http(f"{self.secondary_agent_url}/health", timeout=60)
        return self.secondary_agent_url

    def start(self) -> "ProductRuntimeHarness":
        for executable in (AGENT_PYTHON, BUSINESS_PYTHON):
            if not executable.is_file():
                raise ProductRuntimeEnvironmentBlocked(
                    "locked_workspace_dependency_missing",
                    {"phase": "runtime_start", "executable": str(executable)},
                )
        self._prepare_protected_runtime()
        if self.deterministic_model:
            self._start_model()
        self._start_business()
        self._start_agent()
        return self

    def restart_product_services(self) -> "ProductRuntimeHarness":
        """Restart public services while preserving the owned PostgreSQL state."""
        if not self.persistence_url:
            raise RuntimeError("public service restart certification requires PostgreSQL persistence")
        self._stop_named("agent-secondary")
        self._stop_named("agent")
        self._stop_named("business")
        self._start_business()
        self._start_agent()
        return self

    def diagnostic_tails(self) -> dict[str, str]:
        for log in self._logs:
            if not log.closed:
                log.flush()
        tails: dict[str, str] = {}
        for path in sorted(self.runtime_dir.glob("*.log")):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            tails[path.stem] = "\n".join(lines[-25:])
        return tails

    def stop(self) -> None:
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        for name in reversed(list(self._processes_by_name)):
            self._stop_named(name)
        for process in reversed(self.processes):
            terminate_process(process)
        self.processes.clear()
        for log in self._logs:
            if not log.closed:
                log.close()
        self._temporary.cleanup()

    def __enter__(self) -> "ProductRuntimeHarness":
        try:
            return self.start()
        except ProductRuntimeEnvironmentBlocked as exc:
            diagnostics = self.diagnostic_tails()
            self.stop()
            exc.diagnostics.setdefault("service_logs", diagnostics)
            raise
        except Exception as exc:
            diagnostics = self.diagnostic_tails()
            self.stop()
            raise RuntimeError(
                f"isolated product runtime failed to start: {exc.__class__.__name__}: {exc}; "
                f"service_logs={diagnostics}"
            ) from exc

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.stop()


def run_http_lifecycle_smoke(harness: ProductRuntimeHarness) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(AGENT_PYTHON), "-B", str(ROOT / "scripts/verify_product_http_smoke.py")],
        cwd=ROOT,
        env=harness.env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )


def main() -> int:
    try:
        with ProductRuntimeHarness() as harness:
            result = run_http_lifecycle_smoke(harness)
            if result.returncode != 0:
                raise RuntimeError({
                    "smoke_stdout": result.stdout[-4000:],
                    "smoke_stderr": result.stderr[-4000:],
                    "service_logs": harness.diagnostic_tails(),
                })
            print(json.dumps({
                "status": "PASS",
                "boundary": "authenticated_public_http",
                "model": "deterministic_openai_compatible_process",
                "services": ["agent", "business"],
                "lifecycle": ["login", "chat", "draft", "input", "authority", "commit", "receipt", "sse"],
                "data": "temporary_sqlite_ephemeral",
            }, ensure_ascii=False))
            return 0
    except Exception as exc:
        print(json.dumps({
            "status": "FAIL",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
