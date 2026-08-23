# Counterexample evidence

The focused and related suites directly reject these counterexamples:

- stale session revision;
- concurrent selection with the same old revision;
- changed registration digest or selection request identity;
- unknown/fabricated Starter entrypoint;
- wrong mutating effect-preview confirmation digest;
- start before selection confirmation;
- starting a session that already advanced;
- Host result for an execution other than the active `host_wait`;
- external-event resume while the session is `WAITING_HOST`;
- missing durable external/Human-Gate evidence;
- Graph END paired with TaskRun `COMPLETED` instead of `VALIDATING`;
- stale Skill, changed result, conflicting result resubmission, and unguarded
  mutating Host tool receipt through the existing Host bridge tests.

The diff integrity reviewer found no out-of-scope path, test weakening,
forbidden pattern, or deterministic finding.

