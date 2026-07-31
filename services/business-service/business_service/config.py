from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .runtime_profile import RuntimeProfile, get_runtime_profile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BusinessSettings:
    profile: RuntimeProfile
    database_backend: str
    database_url: str | None
    database_path: Path
    service_token: str
    require_actor_signature: bool
    actor_signing_secret: str | None
    actor_signature_ttl_seconds: int
    seed_demo_data: bool

    @property
    def is_production(self) -> bool:
        return self.profile is RuntimeProfile.PRODUCTION

    @property
    def is_protected(self) -> bool:
        return self.profile in {RuntimeProfile.PREPROD, RuntimeProfile.PRODUCTION}

    def validate_security(self) -> None:
        """Reject accidental production startup with development trust settings.

        The Agent's service token authenticates the caller, but it must not become
        a master key that lets an intermediate service invent a customer identity.
        Production therefore requires both a non-default service token and signed,
        replay-protected actor headers.
        """
        if not self.is_protected:
            return
        failures: list[str] = []
        backend = self.database_backend.strip().lower()
        if backend not in {"postgres", "postgresql"}:
            failures.append("BUSINESS_DB_BACKEND must be postgres in preprod/production")
        if not (self.database_url or "").lower().startswith(
            ("postgresql://", "postgresql+psycopg://")
        ):
            failures.append("BUSINESS_DATABASE_URL must use PostgreSQL in preprod/production")
        token_lower = self.service_token.lower()
        if (
            not self.service_token
            or len(self.service_token) < 24
            or any(marker in token_lower for marker in ("dev-service-token", "change-me", "changeme", "default"))
        ):
            failures.append("BUSINESS_SERVICE_TOKEN must be a strong non-default secret")
        if not self.require_actor_signature:
            failures.append("BUSINESS_REQUIRE_ACTOR_SIGNATURE=true is required")
        actor_secret = self.actor_signing_secret or ""
        actor_lower = actor_secret.lower()
        if (
            len(actor_secret) < 32
            or any(marker in actor_lower for marker in ("change-me", "changeme", "default", "dev-secret"))
        ):
            failures.append("BUSINESS_ACTOR_SIGNING_SECRET must be a strong non-default secret")
        if self.seed_demo_data:
            failures.append("BUSINESS_SEED_DEMO_DATA must be false in production")
        if failures:
            raise RuntimeError(
                "business service production security invalid: " + "; ".join(failures)
            )

    @classmethod
    def from_env(cls) -> "BusinessSettings":
        raw_path = os.getenv("BUSINESS_DB_PATH", "runtime/business-service/business.db")
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        profile = get_runtime_profile(strict=True)
        assert profile is not None
        return cls(
            profile=profile,
            database_backend=os.getenv("BUSINESS_DB_BACKEND", "sqlite").strip().lower(),
            database_url=os.getenv("BUSINESS_DATABASE_URL") or None,
            database_path=path,
            service_token=os.getenv("BUSINESS_SERVICE_TOKEN", "dev-service-token"),
            require_actor_signature=_truthy(
                os.getenv("BUSINESS_REQUIRE_ACTOR_SIGNATURE")
            ),
            actor_signing_secret=os.getenv("BUSINESS_ACTOR_SIGNING_SECRET") or None,
            actor_signature_ttl_seconds=max(
                30, int(os.getenv("BUSINESS_ACTOR_SIGNATURE_TTL_SECONDS", "300"))
            ),
            # Seeding changes business data and therefore must be chosen by the
            # deployment explicitly.  The local template and isolated CI set it
            # to true; an absent production setting is safely false.
            seed_demo_data=_truthy(os.getenv("BUSINESS_SEED_DEMO_DATA", "false")),
        )
