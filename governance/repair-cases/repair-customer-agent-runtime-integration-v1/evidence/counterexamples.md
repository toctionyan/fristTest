# Counterexamples

The focused suite proves these failure-closed cases:

- missing, undeclared, drifted, or wrong-frontmatter Starter members are rejected;
- registration fingerprint and package digest drift are rejected;
- a registered Skill path cannot escape the project workspace;
- unknown, missing-flag, and combined-flag slash commands select no Workflow;
- a mutating Skill does not reach the Host without the existing Write Guard;
- an in-memory LangGraph saver is rejected;
- an external event with the wrong event or correlation cannot resume the TaskRun;
- a runtime state with the wrong TaskRun or Workflow identity is rejected;
- registration and routing do not start a TaskRun or create a receipt.
