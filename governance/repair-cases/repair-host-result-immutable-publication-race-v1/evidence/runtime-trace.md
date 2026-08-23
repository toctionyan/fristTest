# Runtime trace

Both test threads completed request validation and reached a barrier immediately
before final publication. Each then invoked the real same-directory hard-link
publication. The filesystem accepted one link and rejected the second because
the final pathname existed. The loser re-read `result.json`, detected different
content and returned a conflict. The final JSON matched one complete submitted
payload and remained digest-valid.

