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
        calls = self._migrate_live_goal_declarations(calls)
        self.emitted_tool_batches.append(deepcopy(calls))
        self.emitted_tool_calls.extend(calls)
        return AIMessage(content=str(step.get("content") or ""), tool_calls=calls)

    @staticmethod
    def _condition_goal_output_ids(value: Any) -> set[str]:
        """Return explicit Condition-AST producer references without interpreting text."""

        if isinstance(value, dict):
            found = {
                str(value.get("goal_id") or "")
                if str(value.get("source") or "") == "goal_output"
                else ""
            }
            for item in value.values():
                found.update(ScriptedChatModel._condition_goal_output_ids(item))
            found.discard("")
            return found
        if isinstance(value, list):
            found: set[str] = set()
            for item in value:
                found.update(ScriptedChatModel._condition_goal_output_ids(item))
            return found
        return set()

    def _migrate_live_goal_declarations(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Upgrade legacy scripted candidates at the live provider boundary.

        Catalog JSON remains an historical semantic oracle.  The executable
        model double, however, must submit the current production schema.  It
        may translate an explicit legacy edge only when the producer and its
        one canonical semantic output are already present in the same
        declaration.  Ambiguous fixtures fail instead of guessing.
        """

        if "declare_turn_goals" not in self.bound_tool_names:
            return calls
        for call in calls:
            if not isinstance(call, dict) or str(call.get("name") or "") != "declare_turn_goals":
                continue
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            goals = [row for row in list(args.get("goals") or []) if isinstance(row, dict)]
            by_id = {str(goal.get("goal_id") or ""): goal for goal in goals}
            for goal in goals:
                effect = goal.get("requested_effect") if isinstance(goal.get("requested_effect"), dict) else {}
                output_ids = [
                    str(row.get("output_id") or "").strip()
                    for row in list(effect.get("requested_outputs") or [])
                    if isinstance(row, dict) and str(row.get("output_id") or "").strip()
                ]
                # The historical catalogs predate cardinality declarations.
                # Only their canonical collection identity is structurally
                # unambiguous; every other omitted value remains unknown.
                if not str(goal.get("expected_result_cardinality") or "").strip() and output_ids == ["order.collection"]:
                    goal["expected_result_cardinality"] = "collection"
            seen: set[str] = set()
            for goal in goals:
                goal_id = str(goal.get("goal_id") or "")
                if not goal_id:
                    raise AssertionError("scripted live Goal declaration requires goal_id")
                if "input_bindings" in goal:
                    if "depends_on" in goal:
                        raise AssertionError(f"{goal_id}: scripted declaration cannot contain both dependency writers")
                    seen.add(goal_id)
                    continue
                legacy_dependencies = [str(value) for value in list(goal.pop("depends_on", []) or []) if str(value)]
                condition_dependencies = self._condition_goal_output_ids(goal.get("condition"))
                bindings: list[dict[str, Any]] = []
                for index, producer_id in enumerate(legacy_dependencies):
                    if producer_id in condition_dependencies:
                        continue
                    producer = by_id.get(producer_id)
                    if producer is None or producer_id not in seen:
                        raise AssertionError(
                            f"{goal_id}: scripted dependency producer {producer_id!r} must precede its consumer"
                        )
                    effect = producer.get("requested_effect") if isinstance(producer.get("requested_effect"), dict) else {}
                    outputs = [
                        str(row.get("output_id") or "").strip()
                        for row in list(effect.get("requested_outputs") or [])
                        if isinstance(row, dict) and str(row.get("output_id") or "").strip()
                    ]
                    if len(outputs) != 1:
                        raise AssertionError(
                            f"{goal_id}: producer {producer_id!r} must declare exactly one canonical output, got {outputs!r}"
                        )
                    cardinality = str(producer.get("expected_result_cardinality") or "unknown").lower()
                    if cardinality not in {"single", "collection", "unknown"}:
                        cardinality = "unknown"
                    bindings.append({
                        "port": "target" if len(legacy_dependencies) == 1 else f"input:{producer_id}",
                        "source": {
                            "kind": "current_goal_output",
                            "producer_goal_id": producer_id,
                            "output_id": outputs[0],
                        },
                        "relation_kind": "result_reference",
                        "expected_cardinality": cardinality,
                        "evidence_span": str(goal.get("evidence_span") or ""),
                    })
                goal["input_bindings"] = bindings
                seen.add(goal_id)
        return calls

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
