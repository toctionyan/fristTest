from __future__ import annotations

"""Domain-neutral registration surface for model-callable capabilities.

A binding is the only legal join point for three facts that must never drift:
Tool schema, capability contract and permit-protected dispatcher. Concrete
Overlays create bindings; generic Runtime only asks this registry by tool name.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any, Callable, Iterable

from agent_core.kernel.capability import ToolCapabilityContract


PermitDispatcher = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class CapabilityBinding:
    domain_id: str
    contract: ToolCapabilityContract
    schema: dict[str, Any]
    dispatcher: PermitDispatcher
    public_label: str | None = None

    @property
    def tool_name(self) -> str:
        return self.contract.tool_name


class CapabilityRegistry:
    """Immutable registry assembled only by the application Composition Root."""

    def __init__(self, bindings: Iterable[CapabilityBinding], *, version: str = "customer-agent-capability-registry@3.8", allow_empty: bool = False) -> None:
        rows = tuple(bindings)
        if not rows and not allow_empty:
            raise ValueError("CapabilityRegistry requires at least one binding unless an explicit empty Kernel is composed")
        self._bindings = rows
        self.version = version
        self._by_tool = {row.tool_name: row for row in rows}
        self.validate_integrity()

    def validate_integrity(self) -> None:
        if len(self._by_tool) != len(self._bindings):
            raise ValueError("duplicate tool name in CapabilityRegistry")
        seen_keys: set[str] = set()
        for row in self._bindings:
            if not row.domain_id.strip():
                raise ValueError(f"capability {row.tool_name} has empty domain_id")
            if not row.contract.key or row.contract.key in seen_keys:
                raise ValueError(f"duplicate or empty capability key: {row.contract.key!r}")
            seen_keys.add(row.contract.key)
            function = row.schema.get("function") if isinstance(row.schema, dict) else None
            declared_name = str(function.get("name") or "") if isinstance(function, dict) else ""
            if declared_name != row.tool_name:
                raise ValueError(f"schema/contract mismatch for {row.tool_name}: {declared_name!r}")
            if not callable(row.dispatcher):
                raise ValueError(f"capability {row.tool_name} has no dispatcher")
            for relation_name, identities in (
                ("completion_effects", row.contract.completion_effects),
                ("support_effects", row.contract.support_effects),
            ):
                if len(set(identities)) != len(identities):
                    raise ValueError(
                        f"capability {row.tool_name} has duplicate {relation_name}"
                    )
                for identity in identities:
                    normalized = str(identity or "").strip().lower()
                    head, separator, object_type = normalized.partition(":")
                    domain, dot, operation = head.partition(".")
                    if (
                        not separator
                        or not dot
                        or not domain
                        or not operation
                        or not object_type
                        or normalized != identity
                    ):
                        raise ValueError(
                            f"capability {row.tool_name} has invalid {relation_name} identity: {identity!r}"
                        )

    def binding_for_tool(self, tool_name: str) -> CapabilityBinding | None:
        return self._by_tool.get(str(tool_name or ""))

    def contract_for_tool(self, tool_name: str) -> ToolCapabilityContract | None:
        binding = self.binding_for_tool(tool_name)
        return binding.contract if binding else None

    def function_schema(self, tool_name: str) -> dict[str, Any] | None:
        binding = self.binding_for_tool(tool_name)
        if binding is None:
            return None
        function = binding.schema.get("function") if isinstance(binding.schema, dict) else None
        params = function.get("parameters") if isinstance(function, dict) else None
        return dict(params) if isinstance(params, dict) else None

    def schemas(self, tool_names: Iterable[str] | None = None) -> list[dict[str, Any]]:
        allowed = None if tool_names is None else {str(name) for name in tool_names}
        return [
            dict(row.schema)
            for row in self._bindings
            if allowed is None or row.tool_name in allowed
        ]

    def tool_names(self) -> set[str]:
        return set(self._by_tool)

    def capability_keys_for_tools(self, names: Iterable[str]) -> list[str]:
        result: list[str] = []
        for name in names:
            contract = self.contract_for_tool(str(name or ""))
            if contract is not None:
                result.append(contract.key)
        return result

    def public_capability_labels(self) -> list[str]:
        return [row.public_label for row in self._bindings if row.public_label]

    def planning_contract_snapshot(self, tool_names: Iterable[str] | None = None) -> dict[str, Any]:
        """Return a deterministic, read-only planning snapshot.

        Version-1 capabilities remain visible with a null planning contract so
        the next planner stage can distinguish "not migrated" from "missing
        capability" without inferring vertical rules in the Kernel.
        """
        if tool_names is None:
            names = sorted(self._by_tool)
        else:
            names = list(dict.fromkeys(str(name or "") for name in tool_names if str(name or "")))
        capabilities: list[dict[str, Any]] = []
        for name in names:
            contract = self.contract_for_tool(name)
            if contract is None:
                continue
            snapshot = contract.planning_snapshot()
            capabilities.append({
                "tool_name": contract.tool_name,
                "capability_key": contract.key,
                "contract_version": contract.contract_version,
                "planning_contract": snapshot,
            })
        return {
            "version": "capability-planning-snapshot@2",
            "registry_version": self.version,
            "capabilities": capabilities,
            "authority": "module_declared_kernel_validated_read_only",
        }

    def planner_capability_rules(self, tool_names: Iterable[str] | None = None) -> str:
        allowed = None if tool_names is None else {str(name) for name in tool_names}
        lines: list[str] = []
        for row in sorted(self._bindings, key=lambda binding: binding.tool_name):
            if allowed is not None and row.tool_name not in allowed:
                continue
            item = row.contract
            completion = "、".join(item.goal_completion_types) or "仅可作为前置步骤"
            support = "、".join(item.goal_support_types)
            exact_completion = "、".join(item.completion_effects)
            exact_support = "、".join(item.support_effects)
            lines.append(
                f"- {item.tool_name} / {item.execution_kind} / 完成目标:{completion}"
                f"{f' / 可前置支持:{support}' if support else ''}"
                f"{f' / 精确完成效果:{exact_completion}' if exact_completion else ''}"
                f"{f' / 精确前置效果:{exact_support}' if exact_support else ''}：{item.planner_rule}"
            )
        return "\n".join(lines)

    @staticmethod
    def _discovery_text(value: Any) -> str:
        text = str(value or "").lower()
        # Capability discovery compares intent, not the surface form of a
        # Chinese yes/no question.  Planners commonly rewrite ``能…吗`` as
        # ``能否…`` (and likewise for 可以/可否).  Keeping those forms distinct
        # made a validated semantic goal miss an existing capability even
        # though its business predicate was unchanged.  Canonicalise only the
        # modal shell; nouns and verbs remain literal and goal type remains a
        # hard boundary, so a nearby unsupported capability cannot be selected
        # merely because it is also phrased as a question.
        text = text.replace("能不能", "能").replace("能否", "能")
        text = text.replace("可不可以", "可以").replace("可否", "可以")
        text = re.sub(r"吗(?=$|[，。？！,?!；;])", "", text)
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text)

    @staticmethod
    def _bounded_partial_similarity(left: str, right: str) -> float:
        """Compare an intent phrase inside a target-qualified utterance.

        Full-string fuzzy matching is dominated by order ids, product labels
        and conversational prefixes.  Sliding only near the shorter phrase's
        length tolerates small natural variants (for example one inserted
        character) without turning a generic shared modal into a match.  The
        high discovery threshold and explicit negative examples still apply.
        """

        if not left or not right:
            return 0.0
        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        if shorter in longer:
            return 1.0
        minimum = max(1, len(shorter) - 2)
        maximum = min(len(longer), len(shorter) + 2)
        best = 0.0
        for width in range(minimum, maximum + 1):
            for start in range(0, len(longer) - width + 1):
                best = max(
                    best,
                    SequenceMatcher(None, shorter, longer[start : start + width]).ratio(),
                )
        return best

    @classmethod
    def _discovery_score(cls, text: str, contract: ToolCapabilityContract) -> tuple[float, list[str], list[str]]:
        normalized = cls._discovery_text(text)
        positives = [
            str(value).strip()
            for value in contract.discovery_examples
            if str(value).strip()
        ]
        negatives = [
            str(value).strip()
            for value in contract.exclusion_examples
            if str(value).strip()
        ]
        excluded = [value for value in negatives if cls._discovery_text(value) in normalized]
        exact = [value for value in positives if cls._discovery_text(value) in normalized]
        if exact:
            # A distinctive positive phrase may survive shorter negative words
            # in an explicit correction ("只问发票，不要退款"). Conversely a
            # generic marker such as "订单" must not beat the more specific
            # exclusion "发票进度". Ties fail closed. Multi-intent branches are
            # required to be separate Goals rather than hidden in one span.
            positive_weight = max(len(cls._discovery_text(value)) for value in exact)
            negative_weight = max(
                (len(cls._discovery_text(value)) for value in excluded),
                default=0,
            )
            if positive_weight > negative_weight:
                return 100.0 + positive_weight, exact, excluded
        if excluded:
            return -1.0, [], excluded
        corpus = [*positives, contract.planner_rule]
        fuzzy = max((
            max(
                SequenceMatcher(None, normalized, candidate).ratio(),
                cls._bounded_partial_similarity(normalized, candidate),
            )
            for value in corpus
            if (candidate := cls._discovery_text(value))
        ), default=0.0)
        return round(fuzzy * 50.0, 3), [], []

    def discover_surface(
        self,
        goals: Iterable[dict[str, Any]],
        *,
        max_tools_per_goal: int = 4,
        fuzzy_threshold: float = 45.0,
        verified_continuation_tools_by_goal: dict[str, Iterable[str]] | None = None,
    ) -> dict[str, Any]:
        """Compile a bounded capability surface from declared semantic goals.

        This is the domain-neutral equivalent of deferred tool discovery: goal
        type is a hard contract boundary, module examples rank only compatible
        capabilities, exclusions win, and an unsupported reporter is exposed
        only when no registered candidate reaches the discovery threshold.
        Fuzzy similarity is intentionally near-exact only: generic wording such
        as “可以…吗” must never turn an unsupported exchange request into an
        invoice/refund capability merely because their sentence shapes match.
        Exact execution remains the responsibility of CapabilityGate.
        """
        max_count = max(1, int(max_tools_per_goal))
        continuation_hints = verified_continuation_tools_by_goal or {}
        unsupported = next(
            (
                row.tool_name
                for row in self._bindings
                if row.contract.execution_kind == "unsupported"
            ),
            None,
        )
        selected: list[str] = []
        decisions: list[dict[str, Any]] = []
        unsupported_goal_ids: list[str] = []
        for raw_goal in goals:
            if not isinstance(raw_goal, dict):
                continue
            goal_id = str(raw_goal.get("goal_id") or "")
            goal_type = str(raw_goal.get("goal_type") or "")
            # The literal span is the primary discovery query.  A validated
            # semantic goal description is a bounded secondary query only
            # when the literal is correction/ellipsis-shaped and discovers no
            # capability.  This mirrors deferred tool discovery: the semantic
            # planner can retrieve candidates, while CapabilityGate still
            # requires current-turn spans and an exact execution proof.
            text = str(raw_goal.get("evidence_span") or "").strip()
            semantic_text = str(raw_goal.get("description") or "").strip()
            if goal_type in {"narrative", "clarification"}:
                decisions.append({
                    "goal_id": goal_id,
                    "goal_type": goal_type,
                    "status": "terminal_protocol",
                    "candidate_tools": [],
                    "ranked_candidates": [],
                })
                continue
            if goal_type == "unsupported":
                candidate_names = [unsupported] if unsupported else []
                selected.extend(candidate_names)
                if goal_id:
                    unsupported_goal_ids.append(goal_id)
                decisions.append({
                    "goal_id": goal_id,
                    "goal_type": goal_type,
                    "status": "unsupported",
                    "candidate_tools": candidate_names,
                    "ranked_candidates": [],
                })
                continue
            compatible = [
                row for row in self._bindings
                if (
                    goal_type in row.contract.goal_completion_types
                    or goal_type in row.contract.goal_support_types
                )
                and row.contract.execution_kind not in {"unsupported", "clarification_read"}
            ]
            scored: list[dict[str, Any]] = []
            for row in compatible:
                score, exact, excluded = self._discovery_score(text, row.contract)
                semantic_score = -1.0
                semantic_exact: list[str] = []
                semantic_excluded: list[str] = []
                if semantic_text and semantic_text != text:
                    semantic_score, semantic_exact, semantic_excluded = self._discovery_score(
                        semantic_text, row.contract,
                    )
                scored.append({
                    "tool_name": row.tool_name,
                    "score": score,
                    "exact_markers": exact,
                    "excluded_markers": excluded,
                    "semantic_score": semantic_score,
                    "semantic_exact_markers": semantic_exact,
                    "semantic_excluded_markers": semantic_excluded,
                    "discovery_source": "literal",
                })
            usable = [row for row in scored if row["score"] >= 0]
            exact_rows = [row for row in usable if row["exact_markers"]]
            ranked = sorted(usable, key=lambda row: (-float(row["score"]), str(row["tool_name"])))
            if exact_rows:
                candidates = sorted(
                    exact_rows,
                    key=lambda row: (-float(row["score"]), str(row["tool_name"])),
                )[:max_count]
                status = "matched"
            elif ranked and float(ranked[0]["score"]) >= float(fuzzy_threshold):
                candidates = [
                    row for row in ranked
                    if float(row["score"]) >= float(fuzzy_threshold)
                ][:max_count]
                status = "fuzzy_candidates"
            else:
                # Goal declarations reaching this registry have already
                # passed the goal-alignment boundary.  The description may
                # therefore retrieve a small candidate set but never acts as
                # parameter evidence or an execution permit.
                semantic_usable = [
                    {**row, "score": row["semantic_score"], "exact_markers": row["semantic_exact_markers"], "discovery_source": "validated_goal_description"}
                    for row in scored
                    if row["score"] >= 0 and row["semantic_score"] >= 0
                    and (
                        (contract := self.contract_for_tool(str(row["tool_name"]))) is not None
                        and goal_type in contract.goal_completion_types
                    )
                ]
                semantic_exact_rows = [row for row in semantic_usable if row["exact_markers"]]
                semantic_ranked = sorted(
                    semantic_usable,
                    key=lambda row: (-float(row["score"]), str(row["tool_name"])),
                )
                if semantic_exact_rows:
                    candidates = sorted(
                        semantic_exact_rows,
                        key=lambda row: (-float(row["score"]), str(row["tool_name"])),
                    )[:max_count]
                    status = "semantic_goal_candidates"
                elif semantic_ranked and float(semantic_ranked[0]["score"]) >= float(fuzzy_threshold):
                    candidates = [
                        row for row in semantic_ranked
                        if float(row["score"]) >= float(fuzzy_threshold)
                    ][:max_count]
                    status = "semantic_goal_candidates"
                else:
                    candidates = []
                    status = "unsupported"
            candidate_names = [str(row["tool_name"]) for row in candidates]
            has_completion_candidate = any(
                goal_type in contract.goal_completion_types
                for name in candidate_names
                if (contract := self.contract_for_tool(name)) is not None
            )
            verified_continuations = [
                str(name)
                for name in continuation_hints.get(goal_id, ())
                if (
                    (contract := self.contract_for_tool(str(name))) is not None
                    and goal_type in contract.goal_completion_types
                    and contract.execution_kind not in {"unsupported", "clarification_read"}
                )
            ]
            if not has_completion_candidate and verified_continuations:
                candidate_names = list(dict.fromkeys([
                    *candidate_names,
                    *verified_continuations,
                ]))[:max_count]
                status = "matched_with_verified_continuation" if candidates else "verified_continuation"
            if not candidate_names and unsupported:
                candidate_names = [unsupported]
                if goal_id:
                    unsupported_goal_ids.append(goal_id)
            selected.extend(candidate_names)
            decisions.append({
                "goal_id": goal_id,
                "goal_type": goal_type,
                "status": status,
                "candidate_tools": candidate_names,
                "ranked_candidates": scored,
            })
        return {
            "version": "capability-surface@1",
            "tool_names": list(dict.fromkeys(selected)),
            "goals": decisions,
            "unsupported_goal_ids": list(dict.fromkeys(unsupported_goal_ids)),
            "max_tools_per_goal": max_count,
            "authority": "registry_discovery_only_not_execution_permit",
        }

    def dispatch_permitted(
        self,
        state: dict[str, Any],
        tool_name: str,
        args: dict[str, Any] | None,
        *,
        execution_permit: dict[str, Any] | None,
        effect_id: str,
        transactions: Any | None = None,
    ) -> dict[str, Any]:
        binding = self.binding_for_tool(tool_name)
        if binding is None:
            return {
                "ok": False,
                "code": "UNKNOWN_OR_UNSUPPORTED_TOOL",
                "message": f"当前 Capability Registry 未注册工具：{tool_name}。系统不会查找相近工具代替。",
            }
        return binding.dispatcher(
            state,
            tool_name,
            dict(args or {}),
            execution_permit=execution_permit,
            effect_id=effect_id,
            transactions=transactions,
        )
