from __future__ import annotations

import os

# Test collection imports business_service.api, which constructs its ASGI app at
# module import time. Tests run in the explicit local profile unless a case
# overrides APP_PROFILE through monkeypatch.
os.environ.setdefault("APP_PROFILE", "local")
