# Runtime trace

The end-to-end unit test `test_root_cli_initializes_and_opens_one_durable_host_session` invoked the canonical root CLI twice:

1. `skillctl.py authoring host-init --project-workspace <temp-git-project>` returned PASS and generated the verified Starter copy, sealed registration, and fingerprinted bootstrap.
2. With the returned factory/bootstrap environment, `skillctl.py host --request open.json` returned PASS.

The real durable session phase was `AWAITING_SELECTION`; its response policy reported `write_authority_granted=false`. The factory also created the project-local SQLite checkpoint database.
