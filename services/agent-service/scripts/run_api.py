import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import uvicorn  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from agent_core.runtime.profile import require_runtime_profile  # noqa: E402
from agent_core.rag.bootstrap import RagBootstrapService  # noqa: E402
from agent_core.composition import get_module_registry  # noqa: E402

load_dotenv(ROOT / ".env")

if __name__ == "__main__":
    profile = require_runtime_profile()
    # ``run_api.py`` is the documented local management entrypoint.  Local
    # runtime files are intentionally absent from a clean workspace, so seed
    # the installed modules' idempotent builtin knowledge before serving.
    # Protected profiles never enter this branch: their data lifecycle remains
    # an explicit deployment/migration responsibility.
    if profile.value == "local":
        get_module_registry()
        rag_readiness = RagBootstrapService().seed_builtin_knowledge()
        if not rag_readiness.get("ready"):
            raise RuntimeError(
                f"local RAG bootstrap failed: {rag_readiness.get('error') or 'unknown error'}"
            )
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("AGENT_SERVICE_RELOAD", "true").lower() in {"1", "true", "yes"}
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)
