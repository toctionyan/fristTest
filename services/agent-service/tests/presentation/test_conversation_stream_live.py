"""Regression coverage for the public SSE producer's live delivery boundary."""
from __future__ import annotations

from contextlib import contextmanager
import json
from threading import Event
from types import SimpleNamespace

from app.schemas.chat_schema import ChatRequest, ChatResponse
from app.services.turn_lock import ConversationBusyError
from app.use_cases.conversation_turn import ConversationTurnService


class _Noop:
    def add_message(self, *_args, **_kwargs) -> None:
        return None

    def upsert_thread(self, *_args, **_kwargs) -> None:
        return None

    def log_event(self, *_args, **_kwargs) -> None:
        return None


class _PublicUpdates:
    def project_public_update(self, update):
        return {"phase": update["lifecycle"]["phase"]}


class _BlockingGraph:
    def __init__(self) -> None:
        self.blocked_after_first_update = Event()
        self.allow_completion = Event()

    def stream(self, *_args, **_kwargs):
        yield {"lifecycle": {"phase": "planning"}}
        self.blocked_after_first_update.set()
        assert self.allow_completion.wait(timeout=2), "test must control graph completion"
        yield {"lifecycle": {"phase": "finished"}}

    def get_state(self, _config):
        return SimpleNamespace(values={"summary": "done"})


class _ExplodingGraph:
    def invoke(self, *_args, **_kwargs):
        raise RuntimeError("graph exploded")

    def stream(self, *_args, **_kwargs):
        raise RuntimeError("graph exploded")


class _Service:
    def __init__(self, graph: _BlockingGraph) -> None:
        self.graph = graph
        self.message_store = _Noop()
        self.thread_store = _Noop()
        self.trace_logger = _Noop()
        self.sse_stream_adapter = _PublicUpdates()

    def _claim_or_validate_thread(self, *_args) -> None:
        return None

    @contextmanager
    def _serialized_turn(self, *_args):
        yield {"wait_ms": 0, "assert_valid": lambda: None}

    def _config_for_request(self, *_args):
        return {"configurable": {}}

    def _human_message(self, message):
        return message

    def _require_graph(self):
        return self.graph

    def _normalize(self, thread_id, _result, *, include_debug=False):
        return ChatResponse(type="answer", thread_id=thread_id, answer="完成")

    def _persist_public_response(self, *_args, **_kwargs) -> None:
        return None

    def _debug_snapshot(self, result):
        return result


class _BusyService(_Service):
    @contextmanager
    def _serialized_turn(self, *_args):
        raise ConversationBusyError("turn is busy")
        yield  # pragma: no cover - required by contextmanager's generator contract


def _request(thread_id: str = "live-sse-thread") -> ChatRequest:
    return ChatRequest(
        thread_id=thread_id,
        user_id="u001",
        tenant_id="tenant-a",
        role="customer",
        message="查询订单",
    )


def _event_name(frame: str) -> str:
    return frame.split("\n", 1)[0].removeprefix("event: ")


def test_public_sse_update_is_delivered_before_graph_completion() -> None:
    graph = _BlockingGraph()
    stream = ConversationTurnService(_Service(graph)).stream(_request())

    assert next(stream).startswith("event: start\n")
    first_update = next(stream)
    assert first_update.startswith("event: public_update\n")
    assert '"planning"' in first_update
    assert graph.blocked_after_first_update.wait(timeout=1)
    assert not graph.allow_completion.is_set()

    graph.allow_completion.set()
    tail = list(stream)
    assert any(event.startswith("event: public_update\n") and '"finished"' in event for event in tail)
    assert any(event.startswith("event: result\n") for event in tail)
    assert tail[-1].startswith("event: end\n")


def test_graph_failure_is_typed_for_chat_and_terminates_sse() -> None:
    service = _Service(_ExplodingGraph())
    use_case = ConversationTurnService(service)

    response = use_case.chat(_request("failed-chat"), include_debug=True)

    assert response.type == "error"
    assert response.error == "CHAT_RUNTIME_FAILED"
    assert response.state == {
        "debug_error": {"error_type": "RuntimeError", "error": "graph exploded"}
    }

    frames = list(use_case.stream(_request("failed-stream")))
    assert [_event_name(frame) for frame in frames] == ["start", "result", "end"]
    result = next(frame for frame in frames if _event_name(frame) == "result")
    payload = json.loads(result.split("data: ", 1)[1])
    assert payload["type"] == "error"
    assert payload["error"] == "CHAT_RUNTIME_FAILED"
    assert "debug_error" not in json.dumps(payload, ensure_ascii=False)

    debug_frames = list(use_case.stream(_request("failed-debug-stream"), include_debug=True))
    assert [_event_name(frame) for frame in debug_frames][-2:] == ["error", "end"]
    error = next(frame for frame in debug_frames if _event_name(frame) == "error")
    assert json.loads(error.split("data: ", 1)[1])["code"] == "CHAT_RUNTIME_FAILED"


def test_busy_sse_emits_public_result_then_end() -> None:
    frames = list(ConversationTurnService(_BusyService(_BlockingGraph())).stream(_request("busy-stream")))

    assert [_event_name(frame) for frame in frames] == ["result", "end"]
    result = next(frame for frame in frames if _event_name(frame) == "result")
    assert json.loads(result.split("data: ", 1)[1])["error"] == "CONVERSATION_BUSY"
