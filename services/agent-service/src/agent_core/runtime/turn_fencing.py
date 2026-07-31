from __future__ import annotations

"""Conversation-turn fencing boundary.

The durable lease is owned by the application service, while the checkpointer
lives in agent_core. A ContextVar carries only a validator callback so every
checkpoint mutation can reject a stale worker without importing application
code.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from langgraph.checkpoint.base import BaseCheckpointSaver


@dataclass(frozen=True)
class TurnFence:
    lock_key: str
    owner: str
    fencing_token: int
    assert_valid: Callable[[], None]


_CURRENT_TURN_FENCE: ContextVar[TurnFence | None] = ContextVar(
    "current_turn_fence", default=None
)
_ATOMIC_FENCE_WRITE: ContextVar[bool] = ContextVar(
    "atomic_fence_write", default=False
)


class StaleTurnFenceError(RuntimeError):
    """Raised by storage when a checkpoint transaction has a stale token."""


@contextmanager
def activate_turn_fence(fence: TurnFence) -> Iterator[None]:
    token = _CURRENT_TURN_FENCE.set(fence)
    try:
        yield
    finally:
        _CURRENT_TURN_FENCE.reset(token)


def assert_current_turn_fence() -> None:
    fence = _CURRENT_TURN_FENCE.get()
    if fence is not None:
        fence.assert_valid()


def current_turn_fence() -> TurnFence | None:
    return _CURRENT_TURN_FENCE.get()


try:
    from langgraph.checkpoint.postgres import PostgresSaver
except Exception:  # pragma: no cover - optional runtime dependency
    PostgresSaver = None  # type: ignore[assignment]


if PostgresSaver is not None:

    class AtomicallyFencedPostgresSaver(PostgresSaver):  # type: ignore[misc]
        """Postgres saver whose lease guard and mutation share one transaction.

        The guard locks the current ``agent_action_locks`` row. A replacement
        worker cannot delete/replace that row until this transaction commits,
        so a write accepted for token N is serialized before token N+1.
        """

        FENCE_GUARD_SQL = """
            SELECT fencing_token
            FROM agent_action_locks
            WHERE lock_key = %s
              AND owner = %s
              AND fencing_token = %s
              AND expires_at::timestamptz > clock_timestamp()
            FOR UPDATE
        """

        @contextmanager
        def atomic_fence_write(self) -> Iterator[None]:
            token = _ATOMIC_FENCE_WRITE.set(True)
            try:
                yield
            finally:
                _ATOMIC_FENCE_WRITE.reset(token)

        @contextmanager
        def _cursor(self, *, pipeline: bool = False) -> Iterator[Any]:
            # Do not use PostgresSaver's autocommit pipeline here: the guard and
            # every checkpoint/blob/write statement must be one transaction.
            from psycopg.rows import dict_row

            with self.lock, self.conn.transaction():
                with self.conn.cursor(binary=True, row_factory=dict_row) as cur:
                    if _ATOMIC_FENCE_WRITE.get():
                        fence = current_turn_fence()
                        if fence is not None:
                            cur.execute(
                                self.FENCE_GUARD_SQL,
                                (fence.lock_key, fence.owner, fence.fencing_token),
                            )
                            if cur.fetchone() is None:
                                raise StaleTurnFenceError(
                                    "checkpoint storage rejected a stale conversation fencing token"
                                )
                    yield cur

else:

    class AtomicallyFencedPostgresSaver:  # pragma: no cover - dependency error facade
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("langgraph-checkpoint-postgres is required")


class FencedCheckpointer(BaseCheckpointSaver):
    """LangGraph-compatible saver decorator with fail-closed write fencing.

    LangGraph validates checkpointers with ``isinstance(BaseCheckpointSaver)``;
    a duck-typed proxy is therefore not sufficient. Reads delegate unchanged,
    while every mutating operation first verifies the active conversation
    lease/fencing token.
    """

    def __init__(self, inner: BaseCheckpointSaver) -> None:
        super().__init__(serde=inner.serde)
        self._inner = inner

    @property
    def config_specs(self) -> list:
        return list(self._inner.config_specs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def get_tuple(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.get_tuple(*args, **kwargs)

    def list(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.list(*args, **kwargs)

    async def aget_tuple(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.aget_tuple(*args, **kwargs)

    def alist(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.alist(*args, **kwargs)

    def get_delta_channel_history(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.get_delta_channel_history(*args, **kwargs)

    async def aget_delta_channel_history(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.aget_delta_channel_history(*args, **kwargs)

    def get_next_version(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.get_next_version(*args, **kwargs)

    def _assert_write_owner(self) -> None:
        assert_current_turn_fence()

    def _finish_write(self, value: Any) -> Any:
        # Revalidate after the delegate returns as well.  The pre-check prevents
        # an already-stale worker from starting a mutation; the post-check makes
        # lease loss during a slow driver call visible to the caller instead of
        # allowing the request to publish a successful outcome.
        self._assert_write_owner()
        return value

    def _write(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self._assert_write_owner()
        atomic = getattr(self._inner, "atomic_fence_write", None)
        if callable(atomic) and current_turn_fence() is not None:
            try:
                with atomic():
                    # Storage performs the authoritative guard in the same
                    # transaction. A post-commit Python check would only create
                    # a false failure if the next worker acquires immediately.
                    return getattr(self._inner, method)(*args, **kwargs)
            except StaleTurnFenceError:
                # The application validator maps ordinary lease replacement to
                # its public ConversationBusyError without coupling core code
                # to the HTTP/application package.
                fence = current_turn_fence()
                if fence is not None:
                    fence.assert_valid()
                raise
        return self._finish_write(getattr(self._inner, method)(*args, **kwargs))

    async def _awrite(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self._assert_write_owner()
        fence = current_turn_fence()
        atomic = getattr(self._inner, "aatomic_fence_write", None)
        if fence is not None:
            if callable(atomic):
                try:
                    async with atomic():
                        return await getattr(self._inner, method)(*args, **kwargs)
                except StaleTurnFenceError:
                    fence.assert_valid()
                    raise
            # A Python pre/post check cannot make an async database mutation
            # atomic. Fail closed until an async saver exposes a transaction-
            # scoped aatomic_fence_write() contract.
            raise RuntimeError(
                "async checkpoint mutation requires storage-level aatomic_fence_write; "
                "use the synchronous fenced graph path or install an atomic async saver"
            )
        return self._finish_write(await getattr(self._inner, method)(*args, **kwargs))

    def put(self, *args: Any, **kwargs: Any) -> Any:
        return self._write("put", *args, **kwargs)

    def put_writes(self, *args: Any, **kwargs: Any) -> Any:
        return self._write("put_writes", *args, **kwargs)

    def delete_thread(self, *args: Any, **kwargs: Any) -> Any:
        return self._write("delete_thread", *args, **kwargs)

    def delete_for_runs(self, *args: Any, **kwargs: Any) -> Any:
        return self._write("delete_for_runs", *args, **kwargs)

    def copy_thread(self, *args: Any, **kwargs: Any) -> Any:
        return self._write("copy_thread", *args, **kwargs)

    def prune(self, *args: Any, **kwargs: Any) -> Any:
        return self._write("prune", *args, **kwargs)

    async def aput(self, *args: Any, **kwargs: Any) -> Any:
        return await self._awrite("aput", *args, **kwargs)

    async def aput_writes(self, *args: Any, **kwargs: Any) -> Any:
        return await self._awrite("aput_writes", *args, **kwargs)

    async def adelete_thread(self, *args: Any, **kwargs: Any) -> Any:
        return await self._awrite("adelete_thread", *args, **kwargs)

    async def adelete_for_runs(self, *args: Any, **kwargs: Any) -> Any:
        return await self._awrite("adelete_for_runs", *args, **kwargs)

    async def acopy_thread(self, *args: Any, **kwargs: Any) -> Any:
        return await self._awrite("acopy_thread", *args, **kwargs)

    async def aprune(self, *args: Any, **kwargs: Any) -> Any:
        return await self._awrite("aprune", *args, **kwargs)

    def with_allowlist(self, extra_allowlist: Any) -> "FencedCheckpointer":
        return FencedCheckpointer(self._inner.with_allowlist(extra_allowlist))
