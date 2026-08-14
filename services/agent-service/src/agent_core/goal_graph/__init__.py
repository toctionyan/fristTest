"""Shadow typed Goal Graph and verified-dataflow contracts.

Stage 1 is read-only and non-executable. Existing runtime authorities remain
unchanged; this package only makes semantic/dataflow invariants explicit.
"""

from .activation_preflight import (
    DEPENDENCY_ACTIVATION_PREFLIGHT_AUTHORITY,
    DEPENDENCY_ACTIVATION_PREFLIGHT_VERSION,
    DEPENDENCY_ACTIVATION_REQUEST_AUTHORITY,
    DEPENDENCY_ACTIVATION_REQUEST_VERSION,
    dependency_activation_preflight_integrity,
    dependency_activation_request_integrity,
    evaluate_dependency_activation_preflight,
)
from .compiler import compile_frozen_semantic_contract
from .capability_closure import (
    TYPED_GOAL_CAPABILITY_COVERAGE_VERSION,
    build_typed_goal_capability_coverage,
)
from .cutover_gate import (
    DEPENDENCY_CUTOVER_GATE_VERSION,
    DEPENDENCY_CUTOVER_GRANT_AUTHORITY,
    DEPENDENCY_CUTOVER_GRANT_VERSION,
    DEPENDENCY_ROLLBACK_CONTRACT_VERSION,
    LEGACY_DEPENDENCY_AUTHORITY,
    TYPED_DEPENDENCY_AUTHORITY,
    build_dependency_authority_rollback_contract,
    dependency_authority_rollback_integrity,
    dependency_cutover_gate_integrity,
    dependency_cutover_grant_integrity,
    evaluate_dependency_cutover_gate,
)
from .dependency_authority import (
    DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY,
    DEPENDENCY_AUTHORITY_ATTESTATION_VERSION,
    build_dependency_authority_attestation,
    dependency_authority_attestation_integrity,
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
    "DEPENDENCY_ACTIVATION_PREFLIGHT_AUTHORITY",
    "DEPENDENCY_ACTIVATION_PREFLIGHT_VERSION",
    "DEPENDENCY_ACTIVATION_REQUEST_AUTHORITY",
    "DEPENDENCY_ACTIVATION_REQUEST_VERSION",
    "DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY",
    "DEPENDENCY_AUTHORITY_ATTESTATION_VERSION",
    "DEPENDENCY_CUTOVER_GATE_VERSION",
    "DEPENDENCY_CUTOVER_GRANT_AUTHORITY",
    "DEPENDENCY_CUTOVER_GRANT_VERSION",
    "DEPENDENCY_ROLLBACK_CONTRACT_VERSION",
    "LEGACY_DEPENDENCY_AUTHORITY",
    "TYPED_DEPENDENCY_AUTHORITY",
    "compile_frozen_semantic_contract",
    "dependency_activation_preflight_integrity",
    "dependency_activation_request_integrity",
    "evaluate_dependency_activation_preflight",
    "build_typed_goal_capability_coverage",
    "build_dependency_authority_attestation",
    "dependency_authority_attestation_integrity",
    "build_dependency_authority_rollback_contract",
    "dependency_authority_rollback_integrity",
    "dependency_cutover_gate_integrity",
    "dependency_cutover_grant_integrity",
    "evaluate_dependency_cutover_gate",
    "dataflow_closure",
    "graph_structural_integrity",
    "make_verified_artifact_ref",
    "make_verified_dataflow_edge",
    "seal_goal_graph",
    "verify_goal_graph",
    "with_verified_dataflow_edge",
]
