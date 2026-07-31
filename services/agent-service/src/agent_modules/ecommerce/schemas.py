"""Read-only projection of installed ecommerce function schemas."""
from __future__ import annotations

from agent_modules.ecommerce.capabilities import CAPABILITIES
from agent_modules.ecommerce.capabilities.schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA

SKILL_SCHEMAS = [dict(row.schema) for row in CAPABILITIES]
