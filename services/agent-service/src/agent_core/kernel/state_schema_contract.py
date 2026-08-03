from __future__ import annotations

"""Closed State Schema version values.

Legacy checkpoint interpretation is owned exclusively by
``agent_core.lifecycle.state_schema``. Runtime modules must not branch on a
legacy schema version after the prepare-turn migration boundary.
"""

CURRENT_STATE_SCHEMA_VERSION = 2
LEGACY_STATE_SCHEMA_VERSION = 1

__all__ = [
    "CURRENT_STATE_SCHEMA_VERSION",
    "LEGACY_STATE_SCHEMA_VERSION",
]
