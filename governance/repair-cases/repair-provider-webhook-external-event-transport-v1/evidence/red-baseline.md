# Provider Webhook / External Event Transport red baseline

Base candidate: `e34d96696708528e72cb7031792561d15e2e6ce7` (merge of PR #2100).

Inspection of the exact base proves:

- `skillctl scheduler` accepts only an already trusted normalized
  `external-event-ingest-request@1` file;
- `DurableExternalEventScheduler` intentionally does not authenticate provider
  requests or interpret provider-native payloads;
- `concrete-host-bootstrap@3` contains no webhook secret reference, provider
  evidence root, delivery replay store, or HTTP transport declaration;
- no controller verifies `X-Hub-Signature-256`, `X-GitHub-Event`, or
  `X-GitHub-Delivery`;
- no root command can accept a real GitHub `workflow_run` webhook and submit its
  authenticated event to the existing Scheduler.

The negative baseline is therefore reproduced: an operator can manually create
a trusted normalized event, but the concrete Host cannot safely receive a real
provider webhook.

