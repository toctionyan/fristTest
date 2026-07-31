import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.services.agent_service import AgentService
from app.schemas.chat_schema import ChatRequest, ResumeRequest
from agent_core.rag.ingest import seed_builtin_knowledge


def show(resp):
    print("\n========== RESPONSE ==========")
    print(resp.model_dump_json(indent=2))


if __name__ == "__main__":
    seed_builtin_knowledge()
    service = AgentService()

    show(service.chat(ChatRequest(thread_id="demo_001", user_id="u001", message="帮我查订单 10001")))
    show(service.chat(ChatRequest(thread_id="demo_001", user_id="u001", message="那它到哪了？")))
    show(service.chat(ChatRequest(thread_id="demo_001", user_id="u001", message="签收多久内可以售后？")))
    show(service.chat(ChatRequest(thread_id="demo_001", user_id="u001", message="订单 10002 的机械键盘坏了，帮我申请售后")))
    show(service.resume(ResumeRequest(thread_id="demo_001", decision="approved", comment="确认申请售后")))
