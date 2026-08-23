# Focused tests

The #2095 focused suite passed 29 tests, including a synchronized two-thread
conflicting result submission. Exactly one submission published `result.json`;
the other received `HostSkillBridgeError`, and the persisted winner was not
overwritten.

