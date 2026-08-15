#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CONTRACTS = "services/agent-service/src/agent_core/modules/contracts.py"
REGISTRY = "services/agent-service/src/agent_core/modules/registry.py"
GOAL = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
VOCAB = "services/agent-service/src/agent_modules/ecommerce/semantic_vocabulary.py"
TEST = "services/agent-service/tests/architecture/test_refund_semantic_vocabulary_distinction.py"
TRIGGER = ".github/release-trigger"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_contracts(root: Path) -> None:
    path = root / CONTRACTS
    replace_once(
        path,
        '''    description: str\n    legacy_effect_aliases: tuple[str, ...] = ()\n''',
        '''    description: str\n    legacy_effect_aliases: tuple[str, ...] = ()\n    included_result_meanings: tuple[str, ...] = ()\n    excluded_result_meanings: tuple[str, ...] = ()\n''',
        "semantic boundary fields",
    )
    replace_once(
        path,
        '''        aliases = tuple(dict.fromkeys(str(value or "").strip().casefold() for value in self.legacy_effect_aliases if str(value or "").strip()))\n        if not output_id or output_id == "open":\n''',
        '''        aliases = tuple(dict.fromkeys(str(value or "").strip().casefold() for value in self.legacy_effect_aliases if str(value or "").strip()))\n        included_result_meanings = tuple(\n            dict.fromkeys(\n                str(value or "").strip()\n                for value in self.included_result_meanings\n                if str(value or "").strip()\n            )\n        )\n        excluded_result_meanings = tuple(\n            dict.fromkeys(\n                str(value or "").strip()\n                for value in self.excluded_result_meanings\n                if str(value or "").strip()\n            )\n        )\n        overlap = {value.casefold() for value in included_result_meanings}.intersection(\n            value.casefold() for value in excluded_result_meanings\n        )\n        if overlap:\n            raise ValueError(\n                f"semantic output {output_id or '<missing>'} cannot include and exclude the same result meaning"\n            )\n        if not output_id or output_id == "open":\n''',
        "semantic boundary normalization",
    )
    replace_once(
        path,
        '''        object.__setattr__(self, "description", description)\n        object.__setattr__(self, "legacy_effect_aliases", aliases)\n''',
        '''        object.__setattr__(self, "description", description)\n        object.__setattr__(self, "legacy_effect_aliases", aliases)\n        object.__setattr__(self, "included_result_meanings", included_result_meanings)\n        object.__setattr__(self, "excluded_result_meanings", excluded_result_meanings)\n''',
        "semantic boundary assignment",
    )
    replace_once(
        path,
        '''            "effect_kinds": list(self.effect_kinds),\n            "description": self.description,\n''',
        '''            "effect_kinds": list(self.effect_kinds),\n            "description": self.description,\n            "included_result_meanings": list(self.included_result_meanings),\n            "excluded_result_meanings": list(self.excluded_result_meanings),\n''',
        "semantic boundary public snapshot",
    )


def patch_registry(root: Path) -> None:
    path = root / REGISTRY
    text = path.read_text(encoding="utf-8")
    count = text.count('"version": "semantic-output-vocabulary@1"')
    if count != 2:
        raise SystemExit(f"registry vocabulary version: expected duplicated two anchors, found {count}")
    path.write_text(
        text.replace('"version": "semantic-output-vocabulary@1"', '"version": "semantic-output-vocabulary@2"'),
        encoding="utf-8",
    )


def patch_vocabulary(root: Path) -> None:
    path = root / VOCAB
    replace_once(
        path,
        '''contains no tool name, capability key, availability flag, planner rule,\ndiscovery example or exclusion example. Legacy aliases are internal migration\nmetadata used only by the deterministic post-freeze compatibility compiler.\n''',
        '''contains no tool name, capability key, availability flag or planner rule.\nOptional included/excluded result meanings are canonical domain-semantic boundaries,\nnot capability examples. Legacy aliases remain internal migration metadata used only\nby the deterministic post-freeze compatibility compiler.\n''',
        "vocabulary module contract",
    )
    replace_once(
        path,
        '''    *legacy_aliases: str,\n) -> SemanticOutputDefinition:\n''',
        '''    *legacy_aliases: str,\n    included_result_meanings: tuple[str, ...] = (),\n    excluded_result_meanings: tuple[str, ...] = (),\n) -> SemanticOutputDefinition:\n''',
        "vocabulary helper signature",
    )
    replace_once(
        path,
        '''        description=description,\n        legacy_effect_aliases=tuple(legacy_aliases),\n''',
        '''        description=description,\n        legacy_effect_aliases=tuple(legacy_aliases),\n        included_result_meanings=included_result_meanings,\n        excluded_result_meanings=excluded_result_meanings,\n''',
        "vocabulary helper forwarding",
    )
    replace_once(
        path,
        '''        "读取已经存在的退款申请记录、退款历史以及这些退款申请的当前处理状态；退款申请记录或退款历史属于退款申请本身的业务记录，不等同于此前产生的退款资格核验结论记录。",\n        "refund.query_status:refund",\n''',
        '''        "读取已经存在的退款申请记录、退款历史以及这些退款申请的当前处理状态；退款申请记录或退款历史属于退款申请本身的业务记录，不等同于此前产生的退款资格核验结论记录。",\n        "refund.query_status:refund",\n        included_result_meanings=(\n            "已存在退款申请或退款历史记录本身，以及这些记录当前处于什么处理状态",\n        ),\n        excluded_result_meanings=(\n            "退款资金预计或承诺何时完成结算、入账或到账的时间结果",\n        ),\n''',
        "refund status semantic boundary",
    )


def patch_goal_planning(root: Path) -> None:
    path = root / GOAL
    replace_once(
        path,
        '''        effect_kinds = [\n            _clean_text(value, limit=80).casefold()\n            for value in list(raw.get("effect_kinds") or [])\n            if _clean_text(value, limit=80)\n        ]\n        if not output_id or not subject_type or not description or not effect_kinds:\n            continue\n        outputs.append({\n            "output_id": output_id,\n            "subject_type": subject_type,\n            "effect_kinds": list(dict.fromkeys(effect_kinds)),\n            "description": description,\n        })\n''',
        '''        effect_kinds = [\n            _clean_text(value, limit=80).casefold()\n            for value in list(raw.get("effect_kinds") or [])\n            if _clean_text(value, limit=80)\n        ]\n        included_result_meanings = [\n            _clean_text(value, limit=600)\n            for value in list(raw.get("included_result_meanings") or [])\n            if _clean_text(value, limit=600)\n        ]\n        excluded_result_meanings = [\n            _clean_text(value, limit=600)\n            for value in list(raw.get("excluded_result_meanings") or [])\n            if _clean_text(value, limit=600)\n        ]\n        if not output_id or not subject_type or not description or not effect_kinds:\n            continue\n        outputs.append({\n            "output_id": output_id,\n            "subject_type": subject_type,\n            "effect_kinds": list(dict.fromkeys(effect_kinds)),\n            "description": description,\n            "included_result_meanings": list(dict.fromkeys(included_result_meanings)),\n            "excluded_result_meanings": list(dict.fromkeys(excluded_result_meanings)),\n        })\n''',
        "alignment vocabulary boundary projection",
    )
    replace_once(
        path,
        '"version": "semantic-output-vocabulary@1",',
        '"version": "semantic-output-vocabulary@2",',
        "alignment vocabulary version",
    )
    replace_once(
        path,
        '''            "requested_effect fidelity is judged against the literal business effect in each Goal evidence_span and the capability-independent canonical vocabulary description; a nearby registered semantic identity is never acceptable merely because its name is related, and when no registered description exactly represents the requested outcome the declaration must retain open",\n''',
        '''            "requested_effect fidelity is judged against the literal business effect in each Goal evidence_span and the capability-independent canonical vocabulary. description states the broad meaning, included_result_meanings adds authoritative positive boundaries, and excluded_result_meanings adds authoritative negative boundaries; an explicitly excluded user-visible result dimension can never be certified by that output_id even when its name/subject is related. Empty boundary lists add no extra claim. When no registered meaning exactly represents the requested outcome the declaration must retain open",\n''',
        "blind semantic boundary rule",
    )
    replace_once(
        path,
        '''                            "then retain it only when the canonical vocabulary description exactly covers the literal user's requested "\n                            "information dimension/outcome. Lexical relatedness, shared subject type, a nearby status/eligibility/action "\n                            "meaning, or implementation availability is not enough. If the user's requested user-visible outcome is "\n''',
        '''                            "then retain it only when the canonical vocabulary description plus included_result_meanings exactly covers the literal user's requested "\n                            "information dimension/outcome and excluded_result_meanings does not cover that outcome. Treat included/excluded_result_meanings as normative domain boundaries, not examples: if the literal requested result dimension falls inside an excluded meaning, the identity is semantic substitution even when description text, identifier, or subject is nearby. Lexical relatedness, shared subject type, a nearby status/eligibility/action "\n                            "meaning, or implementation availability is not enough. If the user's requested user-visible outcome is "\n''',
        "registered output exactness boundary adjudication",
    )
    replace_once(
        path,
        '''            "requested_effect must preserve the user's business effect even when the current system may not implement it; never rewrite an unsupported effect to a nearby available effect. When requested_outputs selects a registered output_id, judge that identity against CANONICAL_SEMANTIC_OUTPUT_VOCABULARY.description, not the identifier name alone. If USER_TEXT requests a materially different user-visible information dimension or outcome that no registered description represents exactly, using a nearby registered output_id is semantic substitution and verdict must be incomplete; the reserved open identity is then required. Capability availability remains forbidden evidence",\n''',
        '''            "requested_effect must preserve the user's business effect even when the current system may not implement it; never rewrite an unsupported effect to a nearby available effect. When requested_outputs selects a registered output_id, judge that identity against CANONICAL_SEMANTIC_OUTPUT_VOCABULARY.description plus its included_result_meanings/excluded_result_meanings, not the identifier name alone. included_result_meanings and excluded_result_meanings are normative domain boundaries, not examples; an outcome that falls inside an excluded meaning cannot be certified by that output_id. If USER_TEXT requests a materially different user-visible information dimension or outcome that no registered meaning represents exactly, using a nearby registered output_id is semantic substitution and verdict must be incomplete; the reserved open identity is then required. Capability availability remains forbidden evidence",\n''',
        "first pass semantic boundary rule",
    )
    replace_once(
        path,
        '''            "coercing an unsupported/open effect into a nearby registered effect; use CANONICAL_SEMANTIC_OUTPUT_VOCABULARY descriptions as the meaning authority for registered requested_outputs, and require open when the requested information dimension/outcome has no exact registered meaning; (3) whether every explicit user-stated "\n''',
        '''            "coercing an unsupported/open effect into a nearby registered effect; use CANONICAL_SEMANTIC_OUTPUT_VOCABULARY description plus included_result_meanings/excluded_result_meanings as the meaning authority for registered requested_outputs. Explicit exclusions are normative contradictions, not weak examples, and require open when the requested information dimension/outcome has no exact registered meaning; (3) whether every explicit user-stated "\n''',
        "blind instruction semantic boundary rule",
    )


def patch_tests(root: Path) -> None:
    path = root / TEST
    text = path.read_text(encoding="utf-8")
    marker = "test_refund_status_exposes_capability_independent_result_boundary"
    if marker in text:
        raise SystemExit("Release 49 semantic boundary regressions already present")
    addition = r'''


def test_refund_status_exposes_capability_independent_result_boundary():
    refund_status = _semantic_output_index()["refund.status"]
    assert refund_status.included_result_meanings == (
        "已存在退款申请或退款历史记录本身，以及这些记录当前处于什么处理状态",
    )
    assert refund_status.excluded_result_meanings == (
        "退款资金预计或承诺何时完成结算、入账或到账的时间结果",
    )

    public = refund_status.public_snapshot()
    assert public["included_result_meanings"] == list(refund_status.included_result_meanings)
    assert public["excluded_result_meanings"] == list(refund_status.excluded_result_meanings)

    boundary_text = "\n".join(
        refund_status.included_result_meanings + refund_status.excluded_result_meanings
    )
    for forbidden in (
        "list_refunds",
        "refund.query_status:refund",
        "ecommerce.refunds.list",
        "可用能力",
    ):
        assert forbidden not in boundary_text


def test_alignment_projection_preserves_semantic_boundaries_without_capability_metadata(monkeypatch):
    from agent_core.lifecycle.goal_planning import _semantic_vocabulary_for_alignment
    import agent_core.modules.registry as registry_module

    class _Registry:
        @staticmethod
        def semantic_vocabulary_snapshot():
            return {
                "version": "semantic-output-vocabulary@2",
                "outputs": [
                    {
                        "output_id": "domain.sample",
                        "subject_type": "sample",
                        "effect_kinds": ["read"],
                        "description": "sample meaning",
                        "included_result_meanings": ["included dimension"],
                        "excluded_result_meanings": ["excluded dimension"],
                        "legacy_effect_aliases": ["must.not.leak"],
                        "tool_name": "must_not_leak",
                        "available": True,
                    }
                ],
            }

    monkeypatch.setattr(registry_module, "current_module_registry", lambda: _Registry())
    projected = _semantic_vocabulary_for_alignment()
    assert projected["version"] == "semantic-output-vocabulary@2"
    assert projected["availability_exposed"] is False
    assert projected["tool_names_exposed"] is False
    assert projected["outputs"] == [
        {
            "output_id": "domain.sample",
            "subject_type": "sample",
            "effect_kinds": ["read"],
            "description": "sample meaning",
            "included_result_meanings": ["included dimension"],
            "excluded_result_meanings": ["excluded dimension"],
        }
    ]


def test_semantic_output_rejects_contradictory_boundary_meaning():
    import pytest
    from agent_core.modules.contracts import SemanticOutputDefinition

    with pytest.raises(ValueError, match="cannot include and exclude the same result meaning"):
        SemanticOutputDefinition(
            output_id="sample.result",
            subject_type="sample",
            effect_kinds=("read",),
            description="sample",
            included_result_meanings=("same result dimension",),
            excluded_result_meanings=("Same Result Dimension",),
        )
'''
    path.write_text(text + addition, encoding="utf-8")


def patch_trigger(root: Path) -> None:
    (root / TRIGGER).write_text(
        "release_request: 2026-08-15T21:05:00+08:00\n"
        "provider: deepseek\n"
        "model: deepseek-v4-flash\n"
        "embedding_model: text-embedding-v4\n"
        "embedding_dimension: 1024\n"
        "reason: rerun protected release after canonical semantic-output result-boundary contract\n",
        encoding="utf-8",
    )


def patch(root: Path) -> None:
    patch_contracts(root)
    patch_registry(root)
    patch_vocabulary(root)
    patch_goal_planning(root)
    patch_tests(root)
    patch_trigger(root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    patch(Path(args.workspace).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
