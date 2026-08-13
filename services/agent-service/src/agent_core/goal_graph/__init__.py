"""Shadow typed Goal Graph and verified-dataflow contracts.

Stage 1 is read-only and non-executable. Existing runtime authorities remain
unchanged; this package only makes semantic/dataflow invariants explicit.
"""

from .compiler import compile_frozen_semantic_contract
from .capability_closure import (
    TYPED_GOAL_CAPABILITY_COVERAGE_VERSION,
    build_typed_goal_capability_coverage,
)
from .contracts import (
    CANONICAL_GOAL_GRAPH_VERSION,
    GOAL_PORT_VERSION,
    TYPED_DATAFLOW_EDGE_VERSION,
    TYPED_TARGET_BINDING_VERSION,
    VERIFIED_ARTIFACT_REF_VERSION,
    make_verified_artifact_ref,
    make_verified_dataflow_edge,
    seal_goal_graph,
    with_verified_dataflow_edge,
)
from .verifier import dataflow_closure, graph_structural_integrity, verify_goal_graph

__all__ = [
    "CANONICAL_GOAL_GRAPH_VERSION",
    "GOAL_PORT_VERSION",
    "TYPED_DATAFLOW_EDGE_VERSION",
    "TYPED_TARGET_BINDING_VERSION",
    "VERIFIED_ARTIFACT_REF_VERSION",
    "TYPED_GOAL_CAPABILITY_COVERAGE_VERSION",
    "compile_frozen_semantic_contract",
    "build_typed_goal_capability_coverage",
    "dataflow_closure",
    "graph_structural_integrity",
    "make_verified_artifact_ref",
    "make_verified_dataflow_edge",
    "seal_goal_graph",
    "verify_goal_graph",
    "with_verified_dataflow_edge",
]
