from __future__ import annotations

from contextlib import contextmanager
import inspect
import sys
from types import ModuleType
from typing import Iterator
from uuid import uuid4

import agent_core.lifecycle.goal_dependency_proof as production


@contextmanager
def _mutated_dependency_proof_module(*, old: str, new: str) -> Iterator[ModuleType]:
    """Load one deliberately-mutated reducer without changing repository source.

    These tests are mutation canaries, not source-shape assertions alone: every
    mutant is executed through the same invariant probe as the production
    reducer. The exact replacement must remain unique so a refactor cannot
    silently turn a mutation guard into a no-op.
    """

    source = inspect.getsource(production)
    assert source.count(old) == 1, f"mutation target must be unique: {old!r}"
    mutated = source.replace(old, new, 1)
    assert mutated != source

    module_name = f"{production.__name__}__mutation_{uuid4().hex}"
    module = ModuleType(module_name)
    module.__file__ = production.__file__
    module.__package__ = production.__package__
    sys.modules[module_name] = module
    try:
        exec(compile(mutated, module.__file__ or module_name, "exec"), module.__dict__)
        yield module
    finally:
        sys.modules.pop(module_name, None)


def _observation(
    module: ModuleType,
    *,
    digest: str = "premise",
    edges: tuple[tuple[str, str], ...] = (),
    role_name: str = "PROVISIONAL",
    complete: bool = True,
    expected_pair_count: int = 1,
    observed_pair_count: int = 1,
    evidence: str = "evidence",
    supersedes: str | None = None,
):
    return module.DependencyGraphObservation(
        premise_digest=digest,
        edges=edges,
        complete=complete,
        graph_matches_declaration=True,
        expected_pair_count=expected_pair_count,
        observed_pair_count=observed_pair_count,
        source="mutation_guard",
        role=getattr(module.DependencyObservationRole, role_name),
        evidence_digest=evidence,
        supersedes_evidence_digest=supersedes,
    )


def _pair_coverage_invariant(module: ModuleType) -> bool:
    proof = module.reduce_dependency_graph_proof(
        None,
        _observation(
            module,
            complete=True,
            expected_pair_count=1,
            observed_pair_count=0,
            evidence="partial-pair-evidence",
        ),
    )
    return proof.maturity == module.DependencyProofMaturity.REJECTED


def _provisional_cannot_mint_authority_invariant(module: ModuleType) -> bool:
    first = module.reduce_dependency_graph_proof(
        None,
        _observation(module, evidence="blind-1"),
    )
    second = module.reduce_dependency_graph_proof(
        first,
        _observation(module, evidence="blind-2", role_name="PROVISIONAL"),
    )
    return (
        second.maturity == module.DependencyProofMaturity.VERIFIED
        and second.authoritative is False
        and second.dependency_challenge_required is True
    )


def _unbound_counterevidence_cannot_downgrade_invariant(module: ModuleType) -> bool:
    first = module.reduce_dependency_graph_proof(
        None,
        _observation(module, evidence="blind"),
    )
    authority = module.reduce_dependency_graph_proof(
        first,
        _observation(
            module,
            edges=(("g2", "g1"),),
            role_name="ADVERSARIAL_CLOSURE",
            evidence="authority-evidence",
        ),
    )
    assert authority.authoritative

    attempted = module.reduce_dependency_graph_proof(
        authority,
        _observation(
            module,
            edges=(),
            role_name="COUNTEREVIDENCE",
            evidence="unbound-new-evidence",
            supersedes=None,
        ),
    )
    return (
        attempted.authoritative
        and attempted.edges == (("g2", "g1"),)
        and attempted.reason_code == "counterevidence_not_bound_to_current_authority"
    )


def test_mutation_guard_pair_coverage_is_behaviorally_killed() -> None:
    assert _pair_coverage_invariant(production) is True

    with _mutated_dependency_proof_module(
        old="            and self.expected_pair_count == self.observed_pair_count\n",
        new="            and True  # MUTANT: pair coverage gate deleted\n",
    ) as mutant:
        assert _pair_coverage_invariant(mutant) is False


def test_mutation_guard_provisional_role_cannot_become_authority() -> None:
    assert _provisional_cannot_mint_authority_invariant(production) is True

    with _mutated_dependency_proof_module(
        old=(
            "        if observation.role != DependencyObservationRole.ADVERSARIAL_CLOSURE:\n"
            "            return _verified_from(\n"
        ),
        new=(
            "        if False:  # MUTANT: VERIFIED -> AUTHORITATIVE role gate deleted\n"
            "            return _verified_from(\n"
        ),
    ) as mutant:
        assert _provisional_cannot_mint_authority_invariant(mutant) is False


def test_mutation_guard_counterevidence_must_bind_current_authority() -> None:
    assert _unbound_counterevidence_cannot_downgrade_invariant(production) is True

    with _mutated_dependency_proof_module(
        old=(
            "        if (\n"
            "            not observation.supersedes_evidence_digest\n"
            "            or observation.supersedes_evidence_digest != previous.authority_evidence_digest\n"
            "        ):\n"
            "            return _preserve(\n"
        ),
        new=(
            "        if False:  # MUTANT: authority binding gate deleted\n"
            "            return _preserve(\n"
        ),
    ) as mutant:
        assert _unbound_counterevidence_cannot_downgrade_invariant(mutant) is False
