#!/usr/bin/env python3
"""Run PostgreSQL, pgvector, restart and concurrency certification live."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "services" / "agent-service"
for path in (ROOT / "scripts", AGENT_ROOT, AGENT_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from locked_python import locked_project_python  # noqa: E402

from production_certification_contract import (  # noqa: E402
    ProductionCertificationError,
    production_session_evidence,
)
from run_managed_quality_integration import ManagedPostgres  # noqa: E402
from verify_full_lifecycle_canary import ProductRuntimeHarness  # noqa: E402
from verify_managed_postgres_recovery import run_managed_postgres_recovery  # noqa: E402

POSTGRES_TESTS = [
    "tests/integration/test_postgres_migrations.py",
    "tests/integration/test_pgvector_runtime.py",
    "tests/integration/test_atomic_postgres_fencing.py",
]


def _database_identity(url: str, *, instance_nonce: str) -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:
        raise ProductionCertificationError(
            "psycopg_dependency_missing",
            "PostgreSQL certification requires psycopg",
            environment_blocked=True,
        ) from exc
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            cursor.execute(
                "SELECT current_database(), current_user, current_setting('server_version_num'), "
                "pg_postmaster_start_time()::text, EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')"
            )
            database, user, server_version_num, postmaster_started_at, vector_enabled = cursor.fetchone()
    safe = {
        "database": str(database),
        "user": str(user),
        "server_version_num": str(server_version_num),
        "postmaster_started_at": str(postmaster_started_at),
        "instance_nonce": str(instance_nonce),
    }
    fingerprint = hashlib.sha256(json.dumps(safe, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return {
        "database_instance_fingerprint_sha256_16": fingerprint,
        "server_version_num": str(server_version_num),
        "pgvector_extension": bool(vector_enabled),
    }


def _run_integration_tests(url: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="production-postgres-junit-") as temp:
        junit = Path(temp) / "postgres.xml"
        env = os.environ.copy()
        env.update({
            "AGENT_TEST_POSTGRES_URL": url,
            "BUSINESS_TEST_POSTGRES_URL": url,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        completed = subprocess.run(
            [
                str(locked_project_python(ROOT, "agent", env=env)),
                "-B",
                str(ROOT / "scripts" / "run_agent_pytest.py"),
                "--workspace-root",
                str(ROOT),
                "--junitxml",
                str(junit),
                *POSTGRES_TESTS,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=1200,
            check=False,
        )
        if completed.returncode == 78:
            raise ProductionCertificationError(
                "postgres_test_runtime_missing",
                "verified PostgreSQL integration test runtime is unavailable",
                environment_blocked=True,
            )
        if completed.returncode != 0:
            raise ProductionCertificationError(
                "postgres_integration_tests_failed",
                (completed.stdout + "\n" + completed.stderr)[-4000:],
            )
        summary = completed.stdout + "\n" + completed.stderr
        match = re.search(r"(\d+) passed", summary)
        passed = int(match.group(1)) if match else 0
        if passed < 3:
            raise ProductionCertificationError(
                "postgres_integration_test_count_invalid",
                "PostgreSQL integration suite did not attest all required tests",
            )
        return {
            "integration_test_file_count": len(POSTGRES_TESTS),
            "integration_test_case_count": passed,
            "integration_test_files": list(POSTGRES_TESTS),
        }


def main() -> int:
    try:
        session = production_session_evidence(component="postgres")
        with ManagedPostgres() as postgres:
            identity = _database_identity(postgres.url, instance_nonce=postgres.name)
            image_evidence = postgres.image_evidence
            integration = _run_integration_tests(postgres.url)
            with ProductRuntimeHarness(persistence_url=postgres.url) as product:
                recovery = run_managed_postgres_recovery(product)
        result = {
            "contract": "production-postgres-certification@1",
            "status": "PASS",
            "production_session": session,
            **identity,
            **image_evidence,
            **integration,
            "recovery": recovery,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ProductionCertificationError as exc:
        print(json.dumps({
            "contract": "production-postgres-certification@1",
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "reason": exc.code,
            "error": str(exc),
        }, ensure_ascii=False))
        return 78 if exc.environment_blocked else 1
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        text = str(exc)
        environment = any(token in text.lower() for token in (
            "docker command", "docker daemon", "failed to start managed pgvector", "did not become ready"
        ))
        print(json.dumps({
            "contract": "production-postgres-certification@1",
            "status": "BLOCKED_BY_ENVIRONMENT" if environment else "FAIL",
            "reason": "postgres_environment_unavailable" if environment else "postgres_component_failed",
            "error_type": exc.__class__.__name__,
            "error": text,
        }, ensure_ascii=False))
        return 78 if environment else 1
    except Exception as exc:
        print(json.dumps({
            "contract": "production-postgres-certification@1",
            "status": "FAIL",
            "reason": "postgres_component_exception",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
