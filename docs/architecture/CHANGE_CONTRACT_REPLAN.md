# Change Contract Replan Lifecycle

## Purpose

When a product transition has already entered `implementing` or `review` and a frozen governance input is later proven wrong, the active Contract must not be overwritten with `init --force`, and the old Target/baseline must not be edited to make the candidate pass.

The Controller provides a separate fail-closed lifecycle in `skill-system/controller/contract_replan_cli.py`.

## Replan

```bash
python -B skill-system/controller/contract_replan_cli.py replan \
  --successor-change-id <new-change-id> \
  --reason "<why the frozen governance input is invalid>" \
  --evidence governance/<preserved-blocker-evidence>.json
```

Replan is legal only when all of the following remain true:

- current status is `implementing` or `review`;
- target kind is `repair`, `migration`, or `revert`;
- `verification` is null;
- no `release-judge` attestation exists;
- `repair_governance_consumed_at` is null;
- result is still `PENDING`;
- blocker evidence is a preserved file under `governance/`;
- no prior change-history record for the same change exists.

The command writes, in order:

1. `governance/change-history/<old-change-id>/contract-before-replan.json`;
2. `contract-replanned.json` with `status=rejected` and `result=ARCHITECTURE_REPLAN_REQUIRED`;
3. `replan.json`, binding blocker evidence, predecessor/successor IDs and immutable hashes;
4. `governance/pending-replan.json`, binding the only legal successor ID;
5. only then removes `governance/active-change.json`.

If any earlier operation fails, the active pointer is left intact. The old repair permit is recorded but **not consumed**.

## Successor initialization

Run successor initialization in the correct base-source workspace (for the B38 case: fresh exact B36), after restoring the preserved `change-history` and `pending-replan` records:

```bash
python -B skill-system/controller/contract_replan_cli.py init-successor \
  --profile product-code \
  --change-id <new-change-id> \
  --goal "..." \
  --target-kind migration \
  --quality-target <corrected-target-path> \
  --minimum-mode quick \
  --allow <path> ...
```

`init-successor` refuses a different change ID. It binds `predecessor_change_id`, `replan_record`, and `replan_record_sha256` into the new draft Contract and then consumes `pending-replan.json`.

A successor must create a **new corrected Target and new baseline identity**. It may reuse previously frozen candidate source bytes only when their path/SHA scope remains independently proven; it must not reuse the invalid Target's baseline identity.
