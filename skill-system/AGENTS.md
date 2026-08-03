# Skill Control-Plane Rules

- Only a Skill/control-plane Change Contract may modify this directory.
- Product implementers are forbidden from modifying Policy, Registry, Judge, schemas, hooks, host adapters, baselines, attestations, or evidence.
- Host adapters must remain thin and call the same canonical controller.
- Reviewer role names are not identity. Independent task attestations and deterministic validation are mandatory.
- Any reduction in fail-closed behavior, role separation, evidence binding, or test strength is a rejection.
