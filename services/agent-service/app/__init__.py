"""Controlled Stage-4 governed repair canary.

This branch must never be merged. The deliberate syntax error below should be
repaired by the governed CI repair chain, producing a Draft repair PR.

Canary generation 4: triggers the trusted pull-request-target sweeper wakeup.
"""

GOVERNED_REPAIR_CANARY = (
