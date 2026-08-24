# Red baseline

Base commit: `eea05ac089d083c86c95d87c5f57a4caa79b8eb1`.

Inspection proves that `EventDrivenCIProviderAdapter` yields a durable wait handle and `StarterHostCommandTransport` exposes a manual `RESUME_EXTERNAL` operation, but `concrete_host_bootstrap@2` contains no scheduler declaration and the controller package contains no concrete event inbox or wake-up scheduler.

The missing production path is therefore reproducible without changing product code: an external listener can obtain a CI completion event, but the initialized Harness has no repository-owned component that persists it, binds it to the exact current session revision and TaskRun wait checkpoint, serializes concurrent delivery, or recovers a crash around resume.

This baseline is an architecture gap, not a failing product test. Existing manual resume tests remain green and define the lower boundary that the new bootstrap must reuse.
