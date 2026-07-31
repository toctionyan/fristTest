from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

# Host, port and reload are read by this runner before FastAPI imports the
# business configuration module, so load the same service-local `.env` here.
load_dotenv(ROOT / ".env")


if __name__ == "__main__":
    host = os.getenv("BUSINESS_SERVICE_HOST", "127.0.0.1")
    port = int(os.getenv("BUSINESS_SERVICE_PORT", "9000"))
    uvicorn.run("business_service.main:app", host=host, port=port, reload=os.getenv("BUSINESS_SERVICE_RELOAD", "true").lower() in {"1", "true", "yes"})
