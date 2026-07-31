import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import json
from app.services.agent_service import AgentService
from app.services.evaluation_service import EvaluationService
from agent_core.rag.ingest import seed_builtin_knowledge


if __name__ == "__main__":
    seed_builtin_knowledge()
    service = AgentService()
    evaluator = EvaluationService(service)
    print(json.dumps(evaluator.run(), ensure_ascii=False, indent=2, default=str))
