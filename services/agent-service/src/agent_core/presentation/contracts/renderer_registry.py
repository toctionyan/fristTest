"""Channel renderer registrations for formal presentation contracts.

The server does not execute browser code.  It does, however, own the release
rule that a formal block may only leave the API when the named channel renderer
has been intentionally registered by the overlay/bootstrap composition root.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RendererRegistration:
    contract_id: str
    channel: str
    renderer_id: str


class RendererRegistry:
    def __init__(self, registrations: Iterable[RendererRegistration | dict[str, str]] | None = None) -> None:
        self._registrations: dict[tuple[str, str], str] = {}
        for registration in registrations or ():
            self.register(registration)

    def register(self, registration: RendererRegistration | dict[str, str]) -> None:
        if isinstance(registration, RendererRegistration):
            contract_id, channel, renderer_id = registration.contract_id, registration.channel, registration.renderer_id
        else:
            contract_id = str(registration.get("contract_id") or "")
            channel = str(registration.get("channel") or "")
            renderer_id = str(registration.get("renderer_id") or "")
        if not contract_id or not channel or not renderer_id:
            raise ValueError("renderer registration requires contract_id, channel and renderer_id")
        key = (contract_id, channel)
        current = self._registrations.get(key)
        if current is not None and current != renderer_id:
            raise ValueError(f"conflicting renderer registration for {contract_id}@{channel}")
        self._registrations[key] = renderer_id

    def renderer_for(self, contract_id: str, channel: str) -> str | None:
        return self._registrations.get((str(contract_id or ""), str(channel or "")))

    def is_registered(self, contract_id: str, channel: str, *, expected_renderer_id: str | None = None) -> bool:
        actual = self.renderer_for(contract_id, channel)
        return bool(actual) and (expected_renderer_id is None or actual == expected_renderer_id)

    def registrations(self) -> tuple[RendererRegistration, ...]:
        return tuple(
            RendererRegistration(contract_id=contract_id, channel=channel, renderer_id=renderer_id)
            for (contract_id, channel), renderer_id in sorted(self._registrations.items())
        )
