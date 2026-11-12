#!/usr/bin/env node
// Checks a planner response against the contract: the PlanResponse schema and
// invariants I1-I8 (contracts/README.md). Use it on real output:
//
//   curl -s -X POST localhost:8080/api/plan -H 'content-type: application/json' \
//        -d @request.json > response.json
//   node tools/check-plan.mjs response.json --request request.json --store .local/store
//
//   --request <file>   the PlanRequest that produced the response (enables I2)
//   --store <dir>      a file-store directory (FILE_STORE_DIR layout); enables the store
//                      half of I4: every stop's gallery equals the stored object's gallery

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DocStore, validate } from './lib/schema-lite.mjs';
import { checkPlanInvariants } from './lib/plan-invariants.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const flag = (name) => {
  const i = args.indexOf(name);
  return i === -1 ? null : args.splice(i, 2)[1];
};
const requestFile = flag('--request');
const storeDir = flag('--store');
const [responseFile] = args;
if (!responseFile) {
  console.error('usage: node tools/check-plan.mjs <response.json> [--request <request.json>] [--store <dir>]');
  process.exit(2);
}

const read = (f) => JSON.parse(fs.readFileSync(f, 'utf8'));
const store = new DocStore(repoRoot);
const api = 'contracts/planner/openapi.yaml';
const response = read(responseFile);
const request = requestFile ? read(requestFile) : null;
const problems = [];

problems.push(...validate(store.resolve('#/components/schemas/PlanResponse', api).schema, response, store, api).map((e) => `schema ${e}`));
if (request) problems.push(...validate(store.resolve('#/components/schemas/PlanRequest', api).schema, request, store, api).map((e) => `request schema ${e}`));

let objects = null;
if (storeDir && response.museum?.id) {
  objects = new Map();
  const chunkDir = path.join(storeDir, 'museums', response.museum.id, 'object_chunks');
  for (const name of fs.readdirSync(chunkDir)) {
    for (const obj of read(path.join(chunkDir, name)).objects) objects.set(obj.id, obj);
  }
}

if (problems.length === 0) problems.push(...checkPlanInvariants(response, { request, objects }));

if (problems.length) {
  console.log(`FAIL ${responseFile}`);
  problems.forEach((p) => console.log(`  ${p}`));
  process.exit(1);
}
const plan = response.plan ?? response.alternative;
console.log(`ok   ${responseFile}: status ${response.status}${plan ? `, ${plan.stops.length} stop(s), ${plan.totals.total_minutes} of ${plan.totals.time_budget_minutes} min, step_free ${plan.step_free}` : ''}`);
