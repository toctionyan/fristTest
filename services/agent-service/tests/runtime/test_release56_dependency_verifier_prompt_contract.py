from __future__ import annotations

import inspect

from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier


def test_candidate_blind_dependency_prompt_matches_nested_basis_structural_contract() -> None:
    """The semantic verifier must not contradict the structural basis contract.

    Release #55 made a strict relation-only subspan (for example ``它`` inside
    ``它能不能退款``) structurally admissible while keeping equality and
    basis-wrapping-output rejected.  The candidate-blind verifier instruction is
    semantic authority for deciding whether that admissible basis proves a real
    current-turn result dependency, so it must use the same boundary instead of
    forcing every basis to be disjoint from requested-output evidence.
    """

    source = inspect.getsource(ModelGoalAlignmentVerifier.verify)

    assert "must be disjoint from the dependent Goal requested_outputs evidence spans" not in source
    assert "if no disjoint relation-only basis exists" not in source
    assert (
        "a strictly smaller relation-only literal basis nested inside a broader requested-output evidence span is admissible"
        in source
    )
    assert "must not equal a requested-output evidence span" in source
    assert "must not wrap a requested-output evidence span with action/control wording" in source
