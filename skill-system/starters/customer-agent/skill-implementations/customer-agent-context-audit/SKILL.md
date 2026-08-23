---
name: customer-agent-context-audit
description: Enrich customer-Agent findings with multi-turn context, reference, entity-set, and stale-state analysis. Use as an additive finding-enrichment extension for scoped audits.
---

# Enrich findings with context analysis

Consume the existing finding set without replacing the host audit.

1. Trace how verified entities, recent sets, referents, constraints, corrections, and interruptions move across turns.
2. Test singular and plural references, set filtering, union/intersection/difference, stale references, and user corrections without relying on hardcoded pronoun lists.
3. Check that context snapshots are built before planning and that business facts remain business-service authority.
4. Attach reproduction turns and expected resolution to each added or enriched finding.

Return `continue` with `context-enriched-finding-set@1` evidence, or `blocked` when the source finding set or required context trace is unavailable.
