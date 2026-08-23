# Focused tests

Command:

```text
PYTHONPATH=services/agent-service/.venv/lib/python3.12/site-packages:skill-system/controller:skill-system python3 -B -m unittest +  skill-system.tests.test_workspace_provider_adapter +  skill-system.tests.test_concrete_publication_hosts +  skill-system.tests.test_starter_provider_bootstrap +  skill-system.tests.test_starter_runtime +  skill-system.tests.test_publication_provider_adapters +  skill-system.tests.test_publication_e2e_workflow +  skill-system.tests.test_workflow_dispatcher +  skill-system.tests.test_provider_adapters +  skill-system.tests.test_capability_resolution +  skill-system.tests.test_composition_bootstrap +  skill-system.tests.test_customer_agent_starter
```

Result: PASS, 64 tests.

The new direct set contributes 20 tests covering structured mutation, real local
Git, GitHub create/reload, concrete Provider bootstrap, and the installed
Customer Agent CI route.
