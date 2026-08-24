# Negative paths

- The listener is event-driven and adds no provider polling loop.
- The transport does not interpret success, complete a TaskRun, select a
  Workflow, change Graph END behavior, or write product source.
- No merge endpoint, merge adapter, merge token, or automatic merge path is
  added; `merge_authority_changed=false` is sealed in durable receipts.
- The webhook secret is named by configuration but never serialized. Both the
  provider API token and webhook secret are denied to project commands.
- The built-in HTTP server accepts only loopback binding and is documented for
  deployment behind an HTTPS reverse proxy.
- Local-only bootstrap remains valid and has no webhook transport.
- Unsafe paths, symlinks, unsealed evidence, conflicting replays, missing
  credentials, and downstream rejection all fail closed.
