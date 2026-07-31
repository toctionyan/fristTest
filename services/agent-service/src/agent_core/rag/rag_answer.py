import json
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


def answer_with_docs(question: str, docs: list[dict]) -> str:
    answer, _ = answer_with_docs_with_debug(question, docs)
    return answer


def answer_with_docs_with_debug(question: str, docs: list[dict]) -> tuple[str, dict]:
    docs_text = json.dumps(docs, ensure_ascii=False, indent=2)
    try:
        model = get_model()
        llm_messages = [
            SystemMessage(content="""
你是企业客服知识库问答助手。
必须基于【检索资料】和当前用户问题回答。
资料没有说的，不要编造；资料不足时明确说明资料不足。
不要引用聊天历史或摘要中的对象。
回答要简洁，并尽量指出依据的文档标题或来源。
"""),
            HumanMessage(content=f"用户问题：{question}\n检索资料：\n{docs_text}"),
        ]
        resp, model_call = invoke_model(purpose="rag_answer", model=model, payload=llm_messages)
        answer = str(resp.content).strip()
        return answer, {**llm_call_to_debug(node="rag_answer_node", purpose="基于检索资料生成政策回答", input_messages=llm_messages, response=resp), "model_call": model_call}
    except Exception as e:
        answer = "根据当前知识库资料，我还无法确定这个问题的答案。建议补充更具体的信息或转人工客服确认。"
        return answer, {"node": "rag_answer_node", "purpose": "基于检索资料生成政策回答 fallback", "error": str(e), "fallback": True}
