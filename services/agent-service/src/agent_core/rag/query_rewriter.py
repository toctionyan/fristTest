try:
    from langchain_core.messages import HumanMessage, SystemMessage
except Exception:  # pragma: no cover
    class _SimpleMessage:
        def __init__(self, content: str):
            self.content = content
    HumanMessage = SystemMessage = _SimpleMessage  # type: ignore

from agent_core.config import get_model
from agent_core.model_calls import invoke_model
from agent_core.utils.llm_debug import llm_call_to_debug


def rewrite_query(user_input: str, active_context: str | None = None) -> str:
    query, _ = rewrite_query_with_debug(user_input, active_context)
    return query


def rewrite_query_with_debug(user_input: str, active_context: str | None = None) -> tuple[str, dict]:
    try:
        model = get_model()
        llm_messages = [
            SystemMessage(content="""
你是检索查询改写器。
把用户问题改写成适合知识库检索的短查询。
只能使用当前问题与本轮已冻结的执行范围；不要使用聊天历史、摘要或任何未明确给出的对象。
只输出查询文本，不要解释。
"""),
            HumanMessage(content=f"本轮执行范围：{active_context or '无'}\n用户问题：{user_input}"),
        ]
        resp, model_call = invoke_model(purpose="rag_query_rewriter", model=model, payload=llm_messages)
        query = str(resp.content).strip() or user_input
        return query, {**llm_call_to_debug(node="rewrite_query_node", purpose="知识库检索查询改写", input_messages=llm_messages, response=resp), "model_call": model_call}
    except Exception as e:
        return user_input, {"node": "rewrite_query_node", "purpose": "知识库检索查询改写 fallback", "error": str(e), "fallback": True, "query": user_input}
