from __future__ import annotations

"""Explicit RAG bootstrap and readiness boundary.

Application startup never silently seeds production data.  Schema/seed writes
are delegated to an explicit management command.  The service only verifies
that the configured provider can be constructed and, when requested, that
readiness requirements are satisfied.
"""

import os
from typing import Any

from agent_core.rag.factory import get_rag_provider


class RagBootstrapService:
    def verify_readiness(self, *, seed: bool = False) -> dict[str, Any]:
        try:
            provider = get_rag_provider()
            if seed:
                # Explicit local/management-only action.  Callers must never
                # invoke this from preprod/production app startup.
                from agent_core.rag.ingest import seed_builtin_knowledge
                result = seed_builtin_knowledge()
                return {"ready": True, "backend": getattr(provider, "backend_name", type(provider).__name__), "seeded": result}
            return {"ready": True, "backend": getattr(provider, "backend_name", type(provider).__name__)}
        except Exception as exc:
            return {"ready": False, "backend": os.getenv("RAG_BACKEND", "local_sparse"), "error": f"{exc.__class__.__name__}: {exc}"}

    def seed_builtin_knowledge(self) -> dict[str, Any]:
        return self.verify_readiness(seed=True)

