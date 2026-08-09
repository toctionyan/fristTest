#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / 'skill-system/tests/test_wp08_new_release_attempt2_root_fixes.py'
text = path.read_text(encoding='utf-8')

anchor = '''    from agent_core.kernel.semantic_contract import (\n        FROZEN_SEMANTIC_CONTRACT_VERSION,\n        compute_semantic_digest,\n    )\n    goal = {\n'''
replacement = '''    from agent_core.kernel.semantic_contract import (\n        FROZEN_SEMANTIC_CONTRACT_VERSION,\n        compute_semantic_digest,\n    )\n    from agent_core.context.reference_resolution import (\n        normalize_reference_expression,\n        resolve_reference_expression,\n    )\n\n    reference_expression = normalize_reference_expression(\n        {\n            "reference_type": "temporal_visible_result",\n            "temporal_relation": "latest",\n            "evidence_span": "它",\n            "object_type": "order",\n            "expected_cardinality": reference_cardinality,\n        },\n        user_text="它现在是什么状态？",\n        expected_object_type="order",\n        expected_cardinality=reference_cardinality,\n    )\n    proof = resolve_reference_expression(\n        reference_expression,\n        visible_result_refs=[{\n            "result_ref": "h_result:latest-singleton",\n            "source_turn": 4,\n            "shape": "collection",\n            "member_handles": ["artifact:order:10001"],\n            "canonical_order": ["artifact:order:10001"],\n            "resource_types": ["order"],\n            "member_resource_types": ["order"],\n            "discourse_recency_rank": 1,\n        }],\n    )\n    assert proof["resolution_status"] == "UNIQUE"\n    goal = {\n'''
if text.count(anchor) != 1:
    raise SystemExit(f'contract fixture anchor count={text.count(anchor)}')
text = text.replace(anchor, replacement)

old_fields = '''        "reference_expression": {\n            "version": "reference-expression@1",\n            "reference_type": "temporal_visible_result",\n            "temporal_relation": "latest",\n            "evidence_span": "它",\n            "object_type": "order",\n            "expected_cardinality": reference_cardinality,\n        },\n        "resolved_reference": {\n            "result_ref": "h_result:latest-singleton",\n            "member_handles": ["artifact:order:10001"],\n            "proof_digest": "proof-placeholder",\n        },\n'''
new_fields = '''        "reference_expression": reference_expression,\n        "referent_resolution_proof": proof,\n        "resolved_reference": {\n            "result_ref": proof["resolved_result_ref"],\n            "member_handles": list(proof["resolved_member_handles"]),\n            "proof_digest": proof["proof_digest"],\n        },\n'''
if text.count(old_fields) != 1:
    raise SystemExit(f'reference fixture field anchor count={text.count(old_fields)}')
text = text.replace(old_fields, new_fields)
path.write_text(text, encoding='utf-8')
print('Attempt-2 semantic-reference test fixture repaired with a real UNIQUE proof')
