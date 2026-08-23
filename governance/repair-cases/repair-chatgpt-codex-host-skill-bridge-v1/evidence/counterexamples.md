# Counterexamples

The executable tests reject:

- a Host result bound to another request fingerprint, Host or loaded Skill SHA;
- output, result-file or tool argument/result digest drift;
- an undeclared Skill outcome or empty output/evidence identity;
- zero structured tool receipts, duplicate tool call IDs, or an unguarded
  mutating tool receipt;
- conflicting result resubmission and a changed canonical Skill after request;
- a Host wait emitted by a non-Skill step or with the wrong TaskRun, Workflow,
  step, Skill, event or authority flag;
- Host resume with the wrong execution ID, event, result reference or digest;
- an unknown/fabricated Starter entrypoint, modified selection request, extra
  fields or wrong Host identity;
- a mutating natural-language selection without exact entrypoint/effect-preview
  confirmation;
- TaskRun Host resume from an unrelated wait phase or without durable evidence.

No counterexample is converted to a PASS receipt, write authorization, Workflow
success, merge permission or TaskRun completion.

