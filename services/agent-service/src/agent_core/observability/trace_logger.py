from __future__ import annotations

import time


class TraceTimer:
    """Dependency-neutral elapsed-time helper used by application use cases."""

    def __init__(self) -> None:
        self.start = time.time()

    def ms(self) -> int:
        return int((time.time() - self.start) * 1000)
