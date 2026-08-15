from __future__ import annotations

"""Canonical goal-planning import surface with deterministic dependency proof authority.

The implementation body remains in ``goal_planning_core`` so historical source
inspection and the large planning implementation stay intact. This module
installs exactly one proof-maturity boundary, then aliases the public import
name to that patched implementation module so monkeypatching/introspection keep
one module object instead of creating a parallel planning chain.
"""

import sys

from agent_core.lifecycle import goal_planning_core as _core
from agent_core.lifecycle.goal_dependency_proof import install_goal_dependency_proof_authority

install_goal_dependency_proof_authority(_core)
sys.modules[__name__] = _core
