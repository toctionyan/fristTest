from __future__ import annotations

"""Canonical goal-planning import surface with deterministic dependency proof authority.

The implementation body remains in ``goal_planning_core`` so historical source
inspection and the large planning implementation stay intact. This module is
the only public import path and installs one deterministic proof-maturity
boundary before exporting that implementation.
"""

from agent_core.lifecycle import goal_planning_core as _core
from agent_core.lifecycle.goal_dependency_proof import install_goal_dependency_proof_authority

install_goal_dependency_proof_authority(_core)

for _name, _value in vars(_core).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__doc__ = _core.__doc__

del _name, _value
