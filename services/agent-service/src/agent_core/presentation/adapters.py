from __future__ import annotations

"""Compatibility export for the module contribution presentation protocol.

The structural protocol is owned by ``agent_core.modules.contracts`` so module
contributions do not depend on the presentation implementation package.
"""

from agent_core.modules.contracts import PresentationAdapter

__all__ = ["PresentationAdapter"]
