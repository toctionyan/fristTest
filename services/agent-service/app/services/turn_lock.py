"""Conversation lease renewal and fencing boundary."""
from __future__ import annotations

from threading import Event, Thread
from typing import Any


class ConversationBusyError(RuntimeError):
    """Raised when another process owns, or has replaced, the turn lease."""


class ConversationLease:
    """Renew a durable lease and reject a worker after ownership is lost."""

    def __init__(
        self,
        store: Any,
        *,
        lock_key: str,
        owner: str,
        fencing_token: int,
        ttl_seconds: int,
    ) -> None:
        self.store = store
        self.lock_key = lock_key
        self.owner = owner
        self.fencing_token = int(fencing_token)
        self.ttl_seconds = max(3, int(ttl_seconds))
        self._interval = max(1.0, min(self.ttl_seconds / 3.0, 30.0))
        self._stop = Event()
        self._lost = Event()
        self._lost_reason = ""
        self._thread = Thread(
            target=self._renew_loop,
            name=f"conversation-lease-{self.fencing_token}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _mark_lost(self, reason: str) -> None:
        self._lost_reason = reason
        self._lost.set()
        self._stop.set()

    def _renew_loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                result = self.store.renew(
                    self.lock_key,
                    owner=self.owner,
                    fencing_token=self.fencing_token,
                    ttl_seconds=self.ttl_seconds,
                )
            except Exception as exc:  # fail closed on storage uncertainty
                self._mark_lost(f"lease renewal failed: {exc.__class__.__name__}")
                return
            if not bool((result or {}).get("renewed")):
                self._mark_lost("lease ownership was replaced or expired")
                return

    def assert_valid(self) -> None:
        if self._lost.is_set():
            raise ConversationBusyError(
                "会话处理租约已失效，当前 Worker 已停止写入。"
            )
        try:
            valid = self.store.validate(
                self.lock_key,
                owner=self.owner,
                fencing_token=self.fencing_token,
            )
        except Exception as exc:
            self._mark_lost(f"lease validation failed: {exc.__class__.__name__}")
            raise ConversationBusyError(
                "无法确认会话处理租约，已停止当前 Worker 写入。"
            ) from exc
        if not valid:
            self._mark_lost("lease validation rejected current fencing token")
            raise ConversationBusyError(
                "会话处理租约已被新的 Worker 接管，当前 Worker 已停止写入。"
            )

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=min(2.0, self._interval + 0.5))
        self.store.release(
            self.lock_key,
            owner=self.owner,
            fencing_token=self.fencing_token,
        )
