from __future__ import annotations

"""Application-level readiness aggregation for profile-aware deployments.

Liveness belongs to `/health`; this module answers whether the instance may
receive customer traffic.  Local mode is deliberately transparent about
degraded dependencies instead of pretending they are production-ready.
"""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from agent_core.runtime.profile import RuntimeProfile, get_runtime_profile


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    detail: str | None = None
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        data = {"status": self.status, "required": self.required}
        if self.detail:
            data["detail"] = self.detail
        return data


def _ok(name: str, detail: str | None = None, *, required: bool = True) -> ReadinessCheck:
    return ReadinessCheck(name=name, status="ok", detail=detail, required=required)


def _degraded(name: str, detail: str) -> ReadinessCheck:
    return ReadinessCheck(name=name, status="degraded", detail=detail, required=False)


def _failed(name: str, detail: str) -> ReadinessCheck:
    return ReadinessCheck(name=name, status="failed", detail=detail, required=True)


def readiness_report(*, app: Any | None = None) -> dict[str, Any]:
    profile = get_runtime_profile(strict=False)
    if profile is None:
        return {
            "status": "not_ready",
            "profile": None,
            "checks": {"runtime_profile": _failed("runtime_profile", "APP_PROFILE is required").as_dict()},
            "degraded": [],
        }

    checks: list[ReadinessCheck] = [_ok("runtime_profile", profile.value)]
    strict = profile in {RuntimeProfile.PREPROD, RuntimeProfile.PRODUCTION}

    # Config/security validation is intentionally imported lazily: config also
    # imports profile helpers and must not create an import cycle.
    try:
        from agent_core.config import validate_production_security
        if strict:
            validate_production_security()
        checks.append(_ok("security", "profile security constraints satisfied"))
    except Exception as exc:
        checks.append(_failed("security", str(exc)))

    try:
        from agent_core.persistence.store_provider import get_store_provider
        provider = get_store_provider()
        # Provider construction verifies agent persistence wiring.
        checks.append(_ok("agent_store", type(provider).__name__))
    except Exception as exc:
        checks.append(_failed("agent_store", str(exc)))

    try:
        from agent_core.config import build_checkpointer
        checkpointer = build_checkpointer()
        checks.append(_ok("checkpoint", type(checkpointer).__name__))
    except Exception as exc:
        checks.append(_failed("checkpoint", str(exc)))

    react_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if react_dist.is_dir() and (react_dist / "index.html").is_file():
        checks.append(_ok("customer_portal", "dist present"))
    elif strict:
        checks.append(_failed("customer_portal", "frontend/dist/index.html is missing"))
    else:
        checks.append(_degraded("customer_portal", "dist missing; local Vite development is allowed"))

    rag_mode = os.getenv("RAG_AVAILABILITY_MODE", "required" if strict else "optional").strip().lower()
    try:
        from agent_core.rag.bootstrap import RagBootstrapService
        rag = RagBootstrapService().verify_readiness(seed=False)
        if rag.get("ready"):
            checks.append(_ok("rag", str(rag.get("backend") or "ready"), required=rag_mode == "required"))
        elif rag_mode == "required":
            checks.append(_failed("rag", str(rag.get("error") or "RAG is unavailable")))
        else:
            checks.append(_degraded("rag", str(rag.get("error") or "RAG is optional and unavailable")))
    except Exception as exc:
        checks.append(_failed("rag", str(exc)) if rag_mode == "required" else _degraded("rag", str(exc)))

    # Migrations are verified read-only. The application never upgrades a
    # preprod/production database while serving traffic.
    if strict:
        try:
            from agent_core.runtime.migrations import verify_agent_migration
            migration = verify_agent_migration()
            if migration.get("ready"):
                checks.append(_ok("agent_migration", str(migration.get("installed"))))
            else:
                checks.append(_failed("agent_migration", str(migration.get("error") or "migration unavailable")))
        except Exception as exc:
            checks.append(_failed("agent_migration", str(exc)))
    else:
        checks.append(_degraded("agent_migration", "local profile does not require Alembic verification"))

    try:
        from agent_core.business import get_business_port
        adapter = get_business_port()
        if strict:
            # This is a service-to-service health check. It validates the
            # actual downstream dependency rather than only adapter wiring.
            adapter.health()
            checks.append(_ok("business_service", type(adapter).__name__))
        else:
            checks.append(_degraded("business_service", "local mode permits an unavailable business service"))
    except Exception as exc:
        checks.append(_failed("business_service", str(exc)) if strict else _degraded("business_service", str(exc)))

    if app is not None:
        try:
            from app.services.dependency_authority_composition import (
                dependency_authority_composition_readiness,
            )

            service = getattr(getattr(app, "state", None), "agent_service", None)
            composition = getattr(
                service, "dependency_authority_control_composition", None
            )
            if composition is None:
                checks.append(
                    _failed(
                        "dependency_authority_control",
                        "dependency-authority application composition is missing",
                    )
                    if strict
                    else _degraded(
                        "dependency_authority_control",
                        "dependency-authority application composition is missing",
                    )
                )
            else:
                authority = dependency_authority_composition_readiness(composition)
                detail = f"{authority.get('mode')}:{authority.get('status')}"
                if authority.get("ready"):
                    checks.append(_ok("dependency_authority_control", detail))
                else:
                    checks.append(_failed("dependency_authority_control", detail))
        except Exception as exc:
            checks.append(
                _failed("dependency_authority_control", exc.__class__.__name__)
                if strict
                else _degraded(
                    "dependency_authority_control", exc.__class__.__name__
                )
            )

    failed = [item.name for item in checks if item.required and item.status != "ok"]
    degraded = [item.name for item in checks if item.status == "degraded"]
    return {
        "status": "ready" if not failed else "not_ready",
        "profile": profile.value,
        "checks": {item.name: item.as_dict() for item in checks},
        "degraded": degraded,
    }
