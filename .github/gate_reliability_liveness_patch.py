from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one marker, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


runtime = ROOT / "skill-system/controller/execution_runtime.py"
replace_once(
    runtime,
    '''    next_heartbeat = started_monotonic
    termination_reason: str | None = None
    timed_out = False
    stall_timed_out = False
''',
    '''    next_heartbeat = started_monotonic
    # Bind warning publication to one no-progress epoch instead of scheduler
    # sampling frequency. If the runner jumps across both warning and timeout
    # thresholds in one scheduling interval, the warning transition is emitted
    # immediately before fail-closed timeout rather than disappearing.
    last_warned_progress_monotonic: float | None = None
    termination_reason: str | None = None
    timed_out = False
    stall_timed_out = False
''',
    "stall warning epoch state",
)

replace_once(
    runtime,
    '''        if (
            stall_timeout_seconds is not None
            and idle >= stall_timeout_seconds
            and external_wait is None
        ):
            termination_reason = "no_progress_stall"
            timed_out = True
            stall_timed_out = True
            stall_payload = _payload(
''',
    '''        if (
            stall_timeout_seconds is not None
            and idle >= stall_timeout_seconds
            and external_wait is None
        ):
            if last_warned_progress_monotonic != last_progress:
                warning_payload = _payload(
                    process=process,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    activity=snapshot,
                    liveness_status=LIVENESS_SUSPECTED_STALL,
                )
                if on_heartbeat is not None:
                    on_heartbeat(warning_payload)
                last_warned_progress_monotonic = last_progress
            termination_reason = "no_progress_stall"
            timed_out = True
            stall_timed_out = True
            stall_payload = _payload(
''',
    "guarantee warning before stall timeout",
)

replace_once(
    runtime,
    '''            if on_heartbeat is not None:
                on_heartbeat(heartbeat)
            next_heartbeat = now + heartbeat_seconds
''',
    '''            if on_heartbeat is not None:
                on_heartbeat(heartbeat)
            if status == LIVENESS_SUSPECTED_STALL:
                last_warned_progress_monotonic = last_progress
            next_heartbeat = now + heartbeat_seconds
''',
    "record warning epoch",
)

liveness_test = ROOT / "skill-system/tests/test_wp08_certification_liveness.py"
replace_once(
    liveness_test,
    '''            self.assertIn("SUSPECTED_STALL", visible)
            self.assertIn("[WP08 STALL]", visible)
            self.assertIn("no_progress_stall", visible)
''',
    '''            self.assertIn("SUSPECTED_STALL", visible)
            self.assertIn("[WP08 STALL]", visible)
            self.assertLess(
                visible.index("SUSPECTED_STALL"),
                visible.index("[WP08 STALL]"),
                "warning transition must be observable before fail-closed stall timeout",
            )
            self.assertIn("no_progress_stall", visible)
''',
    "assert warning-before-timeout ordering",
)

(ROOT / ".github/workflows/gate-reliability-liveness-carrier.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
