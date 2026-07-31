"""Composition-registered provider for the domain-neutral BusinessPort."""
from __future__ import annotations

from functools import lru_cache
from typing import Callable

from agent_core.business.contracts import BusinessPort


_factory: Callable[[], BusinessPort] | None = None


def configure_business_port(factory: Callable[[], BusinessPort]) -> None:
    """Register a concrete port at the Composition Root.

    Core modules never import a domain Overlay to discover a port. Replacing a
    domain is an explicit composition action and clears the cached instance.
    """
    global _factory
    _factory = factory
    get_business_port.cache_clear()


@lru_cache(maxsize=1)
def get_business_port() -> BusinessPort:
    if _factory is None:
        raise RuntimeError("BusinessPort is not configured by the Composition Root")
    return _factory()


def reset_business_port_cache() -> None:
    get_business_port.cache_clear()
