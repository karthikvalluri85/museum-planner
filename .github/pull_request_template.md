## What this changes and why

<!-- One or two sentences. -->

## Checks

- [ ] `node tools/validate-contracts.mjs` passes
- [ ] `pytest` passes in every service I touched
- [ ] `bash tools/integration-audit.sh` passes (if either service or a contract changed)
- [ ] If a boundary changed: the contract and its examples were changed first, and `node tools/gen-architecture.mjs` was run

## Guardrails

- [ ] No route can be labeled step-free unless every connection on it is marked step-free
- [ ] No gallery number is invented or reformatted
- [ ] Not applicable: this change does not touch planning, floor graphs, or ingestion
