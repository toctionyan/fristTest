# Red baseline

Base: `9c71f715f6f6c33f0005e58fad75f1b49b656177`.

`python3 -B skillctl.py authoring host-init --help` exits 2 because `host-init` is not a registered authoring command.

The trusted factory name `concrete_host_bootstrap:build_orchestrator` cannot be loaded because the module does not exist. Operators must still hand-write a factory that separately assembles registration, Providers, a durable checkpointer, and `StarterHostOrchestrator`.

The pre-existing environment also lacks the optional `langgraph` package, so an executable runtime invocation is environment-blocked locally; unit tests will use the repository's dependency-enabled profile/CI environment.
