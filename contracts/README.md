# Contracts

Every boundary in the system is defined here before any service implements it. Services do not import each other's code. The only things they share are the files in this directory.

| Boundary | Contract | Provider | Consumer |
|---|---|---|---|
| Browser → planner (HTTP) | `planner/openapi.yaml` | planner | web frontend, curl |
| Operator → ingestion (Pub/Sub topic `ingest-museum`) | `events/ingest-museum.schema.json` | whoever publishes | ingestion-job |
| ingestion → planner (Firestore documents) | `firestore/museum.schema.json`, `firestore/object.schema.json`, `firestore/object-chunk.schema.json`, `firestore/floor-graph.schema.json` | ingestion-job (sole writer) | planner (read only) |
| Museum API → ingestion (HTTP, not ours) | `upstream/met-object.schema.json`, `upstream/aic-artwork.schema.json` | the museum | ingestion-job |
| Deploy-time: env, secrets, IAM, scaling | `services.json` (shape: `services.schema.json`) | this repo | deploy scripts, docs |

## Firestore layout

```
museums/{museum_id}                               -> museum.schema.json
museums/{museum_id}/object_chunks/{nnnn}          -> object-chunk.schema.json   (nnnn = chunk_index, 4 digits, from 0000)
floor_graphs/{museum_id}                          -> floor-graph.schema.json
```

Read protocol: read `museums/{id}`, then chunks `0000` to `chunk_count - 1`, then `floor_graphs/{id}`. Chunks at or beyond `chunk_count` are stale and must be ignored.

Write protocol: write the floor graph, then the chunks, then delete chunks at or beyond the new count, then write the museum document last.

Batch-at-a-time ingest: some museum APIs refuse more than a few dozen requests per run. For those, each run reads the stored objects back, fetches only objects it does not have yet, and writes the merged set by the same protocol. `museums/{id}.ingest_complete` is false until the museum reaches `ingest_target_count` or the API has nothing more to give. Readers need not care: every write leaves a complete, valid museum.

### File store (offline tests only)

With `STORE_BACKEND=file`, the same documents are JSON files under `FILE_STORE_DIR`, mirroring the Firestore paths:

```
{FILE_STORE_DIR}/museums/{museum_id}.json
{FILE_STORE_DIR}/museums/{museum_id}/object_chunks/{nnnn}.json
{FILE_STORE_DIR}/floor_graphs/{museum_id}.json
```

## Invariants a schema cannot express

`tools/check-plan.mjs` checks these against any `PlanResponse`. The planner's own tests must check them too.

| # | Invariant |
|---|---|
| I1 | `plan.step_free` is true if and only if every segment in `start.route_to_first` and in every stop's `route_to_next` has `step_free: true`. When it is true, `plan.stairs` is empty. When it is false, `plan.stairs` names every non-step-free segment used. |
| I2 | If the request set `constraints.step_free` and `status` is `ok`, then `plan.step_free` is true. |
| I3 | If `status` is `no_step_free_route`: `plan` is null, `message` is set, and `alternative` is a plan whose `stairs` is non-empty. |
| I4 | Every stop of kind `object` has a non-empty `gallery` that equals the stored object's `gallery`. Nothing is synthesized. |
| I5 | `grounding.matched_tags` is a subset of `object.tags`. `grounding.fields` is non-empty. |
| I6 | `totals.dwell_minutes` equals the sum of stop dwell times. `totals.walking_minutes` equals `start.walk_minutes_to_first` plus the sum of `walk_minutes_to_next`. `totals.total_minutes` is their sum and does not exceed `time_budget_minutes`. |
| I7 | `notices` contains a sentence with both "public data" and "not reviewed". |
| I8 | Stop `order` runs 1, 2, 3 with no gaps. The last stop has `walk_minutes_to_next: null` and an empty `route_to_next`. |

Floor graphs have their own cross-field rules, checked by `tools/validate-contracts.mjs --graph <file>`: edge endpoints exist, node ids are unique, a gallery number appears in at most one node, the entrance node exists, and the graph is connected.

## Changing a contract

1. Edit the contract and its examples under `examples/`.
2. Run `node tools/validate-contracts.mjs`. It must pass.
3. Run `node tools/gen-architecture.mjs` so `docs/ARCHITECTURE.md` matches.
4. Only then change the services.
