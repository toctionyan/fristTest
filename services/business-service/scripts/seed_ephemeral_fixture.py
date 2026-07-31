#!/usr/bin/env python3
from __future__ import annotations

"""Seed disposable CI data explicitly, never from protected app startup."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from business_service.config import BusinessSettings  # noqa: E402
from business_service.database import build_business_database  # noqa: E402
from business_service.seed import seed_demo_data  # noqa: E402


def main() -> int:
    if os.getenv("BUSINESS_EPHEMERAL_FIXTURE", "").strip().lower() != "true":
        raise RuntimeError("BUSINESS_EPHEMERAL_FIXTURE=true is required")
    settings = BusinessSettings.from_env()
    if settings.profile.value not in {"local", "preprod"}:
        raise RuntimeError("ephemeral fixture seeding is limited to local/preprod")
    database = build_business_database(settings)
    database.initialize()
    seed_demo_data(database)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
