# Contributing

## The one rule

Contracts come first. The two services share no code; they agree only through the files in `contracts/`. If a change crosses a boundary (the planner's HTTP API, a Firestore document, the Pub/Sub message), change the contract and its examples before the code, and say so in the pull request.

## Before you open a pull request

Run what CI runs. The first two need only Node; the rest need Python 3.12.

```bash
node tools/selftest.mjs
```

```bash
node tools/validate-contracts.mjs
```

```bash
cd services/planner && python -m pytest -q
```

```bash
cd services/ingestion-job && python -m pytest -q
```

```bash
bash tools/integration-audit.sh
```

If you changed `contracts/services.json`, the OpenAPI file, or `docs/decisions.json`, regenerate the docs:

```bash
node tools/gen-architecture.mjs
```

## Things that must stay true

These are product guardrails, and there are tests for each. A change that weakens one will not be merged.

1. A route is never labeled step-free unless every connection on it is marked step-free in the floor graph.
2. Gallery numbers are never invented. An object the museum's data does not place in a gallery cannot be planned.
3. Every "why" sentence is built from the object's own tags and fields.
4. Every plan says it is built from public data and that the museum has not reviewed it.

## Floor graphs

The floor graphs in `services/ingestion-job/seed/floor_graphs/` are hand-built and are the part most likely to be wrong. Corrections are the most useful contribution there is. Read `docs/FLOOR_GRAPHS.md`, cite the museum's own published plan, and when unsure whether a connection has steps, mark it as not step-free. Check your change with:

```bash
node tools/validate-contracts.mjs --graph services/ingestion-job/seed/floor_graphs/met.json
```

## Commits

One change per commit, with a message that says what changed and why. Do not commit secrets, credentials, or anything under `.env`; the project needs none.
