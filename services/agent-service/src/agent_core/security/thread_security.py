from __future__ import annotations

from hashlib import sha256


def secure_checkpoint_thread_id(thread_id: str, user_id: str, tenant_id: str | None = None) -> str:
    """Return a checkpoint-safe thread id namespaced by tenant and user.

    The visible thread_id alone is client-supplied and therefore must not be used
    as the LangGraph checkpoint key in a multi-user API.
    """

    tenant = tenant_id or "default"
    raw = f"tenant={tenant}::user={user_id}::thread={thread_id}"
    if len(raw) <= 255:
        return raw
    digest = sha256(raw.encode("utf-8")).hexdigest()
    return f"tenant={tenant[:48]}::user={user_id[:48]}::thread_hash={digest}"
