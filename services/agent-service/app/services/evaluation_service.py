from app.services.evaluation_cases import EVAL_CASES
from app.schemas.chat_schema import ChatRequest
from app.services.agent_service import AgentService


class EvaluationService:
    def __init__(self, agent_service: AgentService):
        self.agent_service = agent_service

    def run(self) -> dict:
        results = []
        for i, case in enumerate(EVAL_CASES):
            thread_id = f"eval_{i}"
            resp = self.agent_service.chat(ChatRequest(thread_id=thread_id, user_id=case.get("user_id", "u001"), message=case["message"]))
            passed_type = resp.type == case.get("expected_type", resp.type)
            results.append({"case": case, "response": resp.model_dump(), "passed_type": passed_type})
        return {"total": len(results), "passed_type": sum(1 for r in results if r["passed_type"]), "results": results}
