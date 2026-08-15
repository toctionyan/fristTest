from __future__ import annotations

from itertools import product
import unittest

import agent_core.lifecycle.goal_dependency_proof as proof_model


POSITIVE_EDGE = (("g2", "g1"),)
EMPTY_GRAPH: tuple[tuple[str, str], ...] = ()


def _observation(
    *,
    premise: str = "premise-a",
    edges: tuple[tuple[str, str], ...] = EMPTY_GRAPH,
    role: proof_model.DependencyObservationRole = proof_model.DependencyObservationRole.PROVISIONAL,
    evidence: str = "evidence",
    supersedes: str | None = None,
    complete: bool = True,
    expected_pair_count: int = 1,
    observed_pair_count: int = 1,
) -> proof_model.DependencyGraphObservation:
    return proof_model.DependencyGraphObservation(
        premise_digest=premise,
        edges=edges,
        complete=complete,
        graph_matches_declaration=True,
        expected_pair_count=expected_pair_count,
        observed_pair_count=observed_pair_count,
        source="transition_property_test",
        role=role,
        evidence_digest=evidence,
        supersedes_evidence_digest=supersedes,
    )


def _verified(
    *,
    premise: str = "premise-a",
    edges: tuple[tuple[str, str], ...] = EMPTY_GRAPH,
    evidence: str = "blind-evidence",
) -> proof_model.DependencyGraphProof:
    state = proof_model.reduce_dependency_graph_proof(
        None,
        _observation(premise=premise, edges=edges, evidence=evidence),
    )
    if state.maturity != proof_model.DependencyProofMaturity.VERIFIED:
        raise AssertionError(f"expected VERIFIED seed, got {state.maturity}")
    return state


def _authority(
    *,
    premise: str = "premise-a",
    edges: tuple[tuple[str, str], ...] = POSITIVE_EDGE,
    evidence: str = "authority-evidence",
) -> proof_model.DependencyGraphProof:
    state = _verified(premise=premise, edges=edges, evidence="blind-evidence")
    state = proof_model.reduce_dependency_graph_proof(
        state,
        _observation(
            premise=premise,
            edges=edges,
            role=proof_model.DependencyObservationRole.ADVERSARIAL_CLOSURE,
            evidence=evidence,
        ),
    )
    if not state.authoritative:
        raise AssertionError(f"expected AUTHORITATIVE seed, got {state.maturity}")
    return state


def _authority_signature(state: proof_model.DependencyGraphProof) -> tuple[object, ...]:
    """Semantic authority identity; observation counters/reason text are diagnostics."""

    return (
        state.premise_digest,
        state.edges,
        state.maturity,
        state.preservable,
        state.dependency_challenge_required,
        state.authority_evidence_digest,
    )


class DependencyProofTransitionPropertyTests(unittest.TestCase):
    def test_verifier_call_count_has_no_semantic_meaning(self) -> None:
        """Any number of provisional reaudits stays VERIFIED until explicit closure."""

        for provisional_count in (1, 2, 3, 5, 8, 20):
            with self.subTest(provisional_count=provisional_count):
                state = None
                for index in range(provisional_count):
                    state = proof_model.reduce_dependency_graph_proof(
                        state,
                        _observation(
                            edges=POSITIVE_EDGE,
                            role=proof_model.DependencyObservationRole.PROVISIONAL,
                            evidence=f"blind-{index}",
                        ),
                    )
                    self.assertEqual(state.maturity, proof_model.DependencyProofMaturity.VERIFIED)
                    self.assertFalse(state.authoritative)

                state = proof_model.reduce_dependency_graph_proof(
                    state,
                    _observation(
                        edges=POSITIVE_EDGE,
                        role=proof_model.DependencyObservationRole.ADVERSARIAL_CLOSURE,
                        evidence="closure",
                    ),
                )
                self.assertTrue(state.authoritative)
                self.assertEqual(state.edges, POSITIVE_EDGE)

                expected = _authority_signature(state)
                repeat_roles = (
                    proof_model.DependencyObservationRole.PROVISIONAL,
                    proof_model.DependencyObservationRole.ADVERSARIAL_CLOSURE,
                    proof_model.DependencyObservationRole.COUNTEREVIDENCE,
                    proof_model.DependencyObservationRole.RECLOSURE,
                )
                for index in range(20):
                    state = proof_model.reduce_dependency_graph_proof(
                        state,
                        _observation(
                            edges=POSITIVE_EDGE,
                            role=repeat_roles[index % len(repeat_roles)],
                            evidence=f"same-edge-repeat-{index}",
                        ),
                    )
                    self.assertEqual(_authority_signature(state), expected)

    def test_all_nonclosure_sequences_fail_to_mint_authority(self) -> None:
        """Before closure, role/edge ordering may update the candidate but never authority."""

        nonclosure_roles = (
            proof_model.DependencyObservationRole.PROVISIONAL,
            proof_model.DependencyObservationRole.COUNTEREVIDENCE,
            proof_model.DependencyObservationRole.RECLOSURE,
        )
        steps = tuple(product(nonclosure_roles, (EMPTY_GRAPH, POSITIVE_EDGE)))

        # 6^3 = 216 distinct pre-closure observation sequences.
        for sequence_index, sequence in enumerate(product(steps, repeat=3)):
            state = _verified(edges=EMPTY_GRAPH, evidence=f"seed-{sequence_index}")
            for step_index, (role, edges) in enumerate(sequence):
                state = proof_model.reduce_dependency_graph_proof(
                    state,
                    _observation(
                        edges=edges,
                        role=role,
                        evidence=f"sequence-{sequence_index}-{step_index}",
                    ),
                )
                self.assertEqual(state.maturity, proof_model.DependencyProofMaturity.VERIFIED)
                self.assertFalse(state.authoritative)
                self.assertTrue(state.dependency_challenge_required)

    def test_authority_is_monotonic_under_unbound_revote_sequences(self) -> None:
        """Same-premise opinions cannot revoke authority without admissible bound counterevidence."""

        revotes = (
            # Same-edge observations are harmless regardless of role.
            (proof_model.DependencyObservationRole.PROVISIONAL, POSITIVE_EDGE, None, "same-provisional"),
            (proof_model.DependencyObservationRole.ADVERSARIAL_CLOSURE, POSITIVE_EDGE, None, "same-closure"),
            (proof_model.DependencyObservationRole.COUNTEREVIDENCE, POSITIVE_EDGE, None, "same-counter"),
            (proof_model.DependencyObservationRole.RECLOSURE, POSITIVE_EDGE, None, "same-reclosure"),
            # Different-edge opinions are harmless unless they are new bound counterevidence.
            (proof_model.DependencyObservationRole.PROVISIONAL, EMPTY_GRAPH, None, "different-provisional"),
            (proof_model.DependencyObservationRole.ADVERSARIAL_CLOSURE, EMPTY_GRAPH, None, "different-closure"),
            (proof_model.DependencyObservationRole.RECLOSURE, EMPTY_GRAPH, None, "different-reclosure"),
            (proof_model.DependencyObservationRole.COUNTEREVIDENCE, EMPTY_GRAPH, None, "unbound-counter"),
            (proof_model.DependencyObservationRole.COUNTEREVIDENCE, EMPTY_GRAPH, "wrong-authority", "wrong-bound-counter"),
            # Correct binding but no new evidence is not a new challenge.
            (proof_model.DependencyObservationRole.COUNTEREVIDENCE, EMPTY_GRAPH, "authority-evidence", "authority-evidence"),
        )

        # 10^3 = 1000 different harmless revote orderings.
        for sequence_index, sequence in enumerate(product(revotes, repeat=3)):
            state = _authority()
            expected = _authority_signature(state)
            for step_index, (role, edges, supersedes, evidence) in enumerate(sequence):
                state = proof_model.reduce_dependency_graph_proof(
                    state,
                    _observation(
                        edges=edges,
                        role=role,
                        evidence=evidence,
                        supersedes=supersedes,
                    ),
                )
                self.assertEqual(
                    _authority_signature(state),
                    expected,
                    msg=f"authority drifted at sequence={sequence_index} step={step_index}",
                )

    def test_bound_counterevidence_cannot_replace_authority_without_reclosure(self) -> None:
        authority = _authority()
        challenged = proof_model.reduce_dependency_graph_proof(
            authority,
            _observation(
                edges=EMPTY_GRAPH,
                role=proof_model.DependencyObservationRole.COUNTEREVIDENCE,
                evidence="new-bound-counterevidence",
                supersedes=authority.authority_evidence_digest,
            ),
        )
        self.assertEqual(challenged.maturity, proof_model.DependencyProofMaturity.CHALLENGED)
        self.assertFalse(challenged.preservable)
        self.assertEqual(challenged.edges, POSITIVE_EDGE)
        self.assertEqual(challenged.challenge_edges, EMPTY_GRAPH)

        non_reclosure_roles = (
            proof_model.DependencyObservationRole.PROVISIONAL,
            proof_model.DependencyObservationRole.ADVERSARIAL_CLOSURE,
            proof_model.DependencyObservationRole.COUNTEREVIDENCE,
        )
        for role, edges in product(non_reclosure_roles, (EMPTY_GRAPH, POSITIVE_EDGE)):
            with self.subTest(role=role, edges=edges):
                still_challenged = proof_model.reduce_dependency_graph_proof(
                    challenged,
                    _observation(
                        edges=edges,
                        role=role,
                        evidence=f"non-reclosure-{role.value}-{len(edges)}",
                    ),
                )
                self.assertEqual(still_challenged.maturity, proof_model.DependencyProofMaturity.CHALLENGED)
                self.assertFalse(still_challenged.authoritative)
                self.assertEqual(still_challenged.edges, POSITIVE_EDGE)

        # Reclosure may independently confirm the challenge or restore the prior authority.
        for reclosed_edges, evidence in (
            (EMPTY_GRAPH, "independent-reclosure-empty"),
            (POSITIVE_EDGE, "independent-reclosure-prior"),
        ):
            with self.subTest(reclosed_edges=reclosed_edges):
                reclosed = proof_model.reduce_dependency_graph_proof(
                    challenged,
                    _observation(
                        edges=reclosed_edges,
                        role=proof_model.DependencyObservationRole.RECLOSURE,
                        evidence=evidence,
                    ),
                )
                self.assertTrue(reclosed.authoritative)
                self.assertEqual(reclosed.edges, reclosed_edges)
                self.assertEqual(reclosed.authority_evidence_digest, evidence)

    def test_premise_change_stales_old_authority_and_requires_fresh_closure(self) -> None:
        old_authority = _authority(premise="premise-a", edges=POSITIVE_EDGE)

        stale = proof_model.preserve_dependency_proof(old_authority, premise_digest="premise-b")
        self.assertIsNotNone(stale)
        assert stale is not None
        self.assertEqual(stale.maturity, proof_model.DependencyProofMaturity.STALE)
        self.assertFalse(stale.authoritative)
        self.assertFalse(stale.preservable)
        self.assertEqual(stale.edges, POSITIVE_EDGE)

        fresh = proof_model.reduce_dependency_graph_proof(
            stale,
            _observation(
                premise="premise-b",
                edges=EMPTY_GRAPH,
                role=proof_model.DependencyObservationRole.PROVISIONAL,
                evidence="fresh-premise-blind",
            ),
        )
        self.assertEqual(fresh.maturity, proof_model.DependencyProofMaturity.VERIFIED)
        self.assertFalse(fresh.authoritative)
        self.assertEqual(fresh.premise_digest, "premise-b")
        self.assertEqual(fresh.edges, EMPTY_GRAPH)

        fresh_authority = proof_model.reduce_dependency_graph_proof(
            fresh,
            _observation(
                premise="premise-b",
                edges=EMPTY_GRAPH,
                role=proof_model.DependencyObservationRole.ADVERSARIAL_CLOSURE,
                evidence="fresh-premise-closure",
            ),
        )
        self.assertTrue(fresh_authority.authoritative)
        self.assertEqual(fresh_authority.premise_digest, "premise-b")
        self.assertEqual(fresh_authority.edges, EMPTY_GRAPH)

    def test_positive_and_absence_claims_share_one_lifecycle(self) -> None:
        """No release-specific polarity branch: present/absent and corrections close identically."""

        lifecycle_pairs = (
            (EMPTY_GRAPH, EMPTY_GRAPH),
            (POSITIVE_EDGE, POSITIVE_EDGE),
            (EMPTY_GRAPH, POSITIVE_EDGE),
            (POSITIVE_EDGE, EMPTY_GRAPH),
        )
        for provisional_edges, closure_edges in lifecycle_pairs:
            with self.subTest(provisional_edges=provisional_edges, closure_edges=closure_edges):
                state = _verified(edges=provisional_edges, evidence="polarity-provisional")
                self.assertFalse(state.authoritative)
                self.assertTrue(state.dependency_challenge_required)

                state = proof_model.reduce_dependency_graph_proof(
                    state,
                    _observation(
                        edges=closure_edges,
                        role=proof_model.DependencyObservationRole.ADVERSARIAL_CLOSURE,
                        evidence="polarity-closure",
                    ),
                )
                self.assertTrue(state.authoritative)
                self.assertEqual(state.edges, closure_edges)
                self.assertFalse(state.dependency_challenge_required)


if __name__ == "__main__":
    unittest.main()
