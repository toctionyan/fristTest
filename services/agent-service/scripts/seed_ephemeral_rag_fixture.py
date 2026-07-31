#!/usr/bin/env python3
from __future__ import annotations

"""Seed disposable protected-runtime RAG data explicitly.

Serving processes must never seed preprod/production knowledge on startup.  The
release harness invokes this management command only for an owned disposable
preprod PostgreSQL instance and must opt in with AGENT_EPHEMERAL_RAG_FIXTURE.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_core.rag.bootstrap import RagBootstrapService  # noqa: E402
from agent_core.runtime.profile import get_runtime_profile  # noqa: E402


def main() -> int:
    if os.getenv("AGENT_EPHEMERAL_RAG_FIXTURE", "").strip().lower() != "true":
        raise RuntimeError("AGENT_EPHEMERAL_RAG_FIXTURE=true is required")
    profile = get_runtime_profile(strict=True)
    if profile.value not in {"local", "preprod"}:
        raise RuntimeError("ephemeral RAG fixture seeding is limited to local/preprod")
    if (os.getenv("RAG_BACKEND") or "").strip().lower() != "pgvector":
        raise RuntimeError("ephemeral protected RAG fixture requires RAG_BACKEND=pgvector")
    result = RagBootstrapService().seed_builtin_knowledge()
    if result.get("ready") is not True or result.get("backend") != "pgvector":
        raise RuntimeError(f"protected RAG fixture did not become ready: {result}")
    print(json.dumps({
        "contract": "ephemeral-protected-rag-fixture@1",
        "status": "PASS",
        "profile": profile.value,
        "backend": result.get("backend"),
        "seeded": result.get("seeded"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
