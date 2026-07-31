"""Deterministic OpenAI-compatible model double for lifecycle graph tests.

It implements the tiny LangChain chat-model surface used by the Agent Loop:
``bind_tools`` returns a bound model and ``invoke`` returns scripted
``AIMessage`` tool calls.  It never opens a network connection and deliberately
fails if a graph asks for more model turns than the case contract declared.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import json
from typing import Any, Iterable

from langchain_core.messages import AIMessage


class ScriptedChatModel:
    """Replay one explicit model script and record the bound protocol surface."""

    model_name = "scripted-conversation-regression"

    def __init__(
        self,
        model_steps: Iterable[dict[str, Any]],
        *,
        previous_turn_data: dict[str, Any] | None = None,
    ) -> None:
        self._steps = deque(deepcopy(list(model_steps)))
        self.previous_turn_data = deepcopy(dict(previous_turn_data or {}))
        self.bound_tool_names: set[str] = set()
        self.bound_tool_history: list[set[str]] = []
        self.invoked_bound_tool_history: list[set[str]] = []
        self.bound_tool_choices: list[str | None] = []
        self.invocations: list[list[str]] = []
        self.emitted_tool_calls: list[dict[str, Any]] = []
        self.emitted_tool_batches: list[list[dict[str, Any]]] = []

    def bind_tools(
        self,
        schemas: Iterable[dict[str, Any]],
        *,
        tool_choice: str | None = None,
        **_kwargs: Any,
    ) -> "ScriptedChatModel":
        self.bound_tool_names = {
            str((schema.get("function") or {}).get("name") or "")
            for schema in schemas
            if isinstance(schema, dict)
        }
        self.bound_tool_names.discard("")
        self.bound_tool_history.append(set(self.bound_tool_names))
        self.bound_tool_choices.append(tool_choice)
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        self.invocations.append([message.__class__.__name__ for message in messages])
        self.invoked_bound_tool_history.append(set(self.bound_tool_names))
        if not self._steps:
            raise AssertionError("ScriptedChatModel received an undeclared model invocation")
        step = self._steps.popleft()
        calls = self._resolve_runtime_placeholders(
            deepcopy(list(step.get("tool_calls") or [])),
            messages=messages,
        )
        self.emitted_tool_batches.append(deepcopy(calls))
        self.emitted_tool_calls.extend(calls)
        return AIMessage(content=str(step.get("content") or ""), tool_calls=calls)

    @staticmethod
    def _latest_tool_data(messages: list[Any], *, field: str | None = None) -> dict[str, Any]:
        for message in reversed(messages):
            try:
                payload = json.loads(str(getattr(message, "content", "")))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or not bool(payload.get("ok")):
                continue
            data = payload.get("data")
            if isinstance(data, dict) and data and (not field or field in data):
                return data
        return {}

    def _resolve_runtime_placeholders(self, value: Any, *, messages: list[Any]) -> Any:
        """Resolve opaque handles returned by the immediately preceding tool.

        Conversation contracts must be able to exercise real multi-step
        ResultRef pipelines without predicting UUID-backed handles.  Only an
        exact ``$last_tool.data.<field>`` token is substituted; ordinary model
        text is never rewritten.
        """
        if isinstance(value, dict):
            return {key: self._resolve_runtime_placeholders(item, messages=messages) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_runtime_placeholders(item, messages=messages) for item in value]
        if isinstance(value, str) and value.startswith("$last_tool.data."):
            field = value.removeprefix("$last_tool.data.").strip()
            data = self._latest_tool_data(messages, field=field)
            resolved = data.get(field)
            if resolved is None or resolved == "":
                raise AssertionError(f"script placeholder {value!r} has no preceding successful tool value")
            return deepcopy(resolved)
        if isinstance(value, str) and value.startswith("$previous_turn.data."):
            field = value.removeprefix("$previous_turn.data.").strip()
            resolved = self.previous_turn_data.get(field)
            if resolved is None or resolved == "":
                raise AssertionError(f"script placeholder {value!r} has no previous-turn runtime value")
            return deepcopy(resolved)
        return value

    @property
    def remaining_steps(self) -> int:
        return len(self._steps)
