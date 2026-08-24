# Negative paths

- `OPEN` is the only operation carrying natural language; it is forwarded
  unchanged and never routed by keywords or fuzzy matching.
- `SELECT` and `CONFIRM` require exact existing payloads and current revisions;
  the transport supplies no defaults.
- External and Human resumes require non-empty unique durable evidence; external
  resume also requires an explicit correlation reference.
- There is no arbitrary method, shell, completion, release, deploy, or merge
  operation.
- Factory/module selection is explicit operator configuration outside request
  JSON; an unexpected request field is rejected before import.
- Every response fixes write authority to false, TaskRun as completion authority,
  and automatic merge to false.
- Product compatibility reports all 671 protected customer files unchanged.
