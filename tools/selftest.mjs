#!/usr/bin/env node
// Tests the zero-dependency tooling itself, so a green contract gate means something.
//   node tools/selftest.mjs

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseYaml } from './lib/yaml-lite.mjs';
import { DocStore, validate, lintSchema, findCycle } from './lib/schema-lite.mjs';
import { checkPlanInvariants } from './lib/plan-invariants.mjs';
import { checkGraph } from './lib/graph-checks.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const store = new DocStore(repoRoot);
const read = (f) => JSON.parse(fs.readFileSync(path.join(repoRoot, f), 'utf8'));
const clone = (v) => JSON.parse(JSON.stringify(v));
let passed = 0;
const test = (name, fn) => {
  try {
    fn();
    passed++;
  } catch (e) {
    console.log(`FAIL ${name}\n  ${e.message}`);
    process.exitCode = 1;
  }
};

// ---- yaml-lite ---------------------------------------------------------------

test('yaml: mappings, sequences, scalars, comments', () => {
  const doc = parseYaml([
    '# comment',
    'a: 1',
    'b: "x: y # not a comment"   # trailing comment',
    "c: 'it''s'",
    'd: [one, "null", 3, true]',
    'e:',
    '  - name: first',
    '    n: 1.5',
    '  - plain',
    '  - "quoted: scalar"',
    'f: {}',
    'g: null',
    '"200":',
    '  ok: false',
    '/path/{id}:',
    '  get: yes',
    'h:',
    '- same-indent item',
  ].join('\n'));
  assert.deepEqual(doc, {
    a: 1,
    b: 'x: y # not a comment',
    c: "it's",
    d: ['one', 'null', 3, true],
    e: [{ name: 'first', n: 1.5 }, 'plain', 'quoted: scalar'],
    f: {},
    g: null,
    200: { ok: false },
    '/path/{id}': { get: 'yes' },
    h: ['same-indent item'],
  });
});

test('yaml: rejects what it cannot parse faithfully', () => {
  for (const bad of ['a: {x: 1}', 'a: |\n  text', 'a: &anchor 1', 'a: 1\na: 2', 'a:\n\t- tab', 'a: [1, [2]]', 'a: "unterminated']) {
    assert.throws(() => parseYaml(bad), /yaml-lite/, `should reject: ${JSON.stringify(bad)}`);
  }
});

test('yaml: the OpenAPI contract parses to the expected shape', () => {
  const api = store.load('contracts/planner/openapi.yaml');
  assert.equal(api.openapi, '3.1.0');
  assert.deepEqual(Object.keys(api.paths), ['/healthz', '/api/museums', '/api/museums/{museum_id}', '/api/plan']);
  assert.deepEqual(api.components.schemas.Constraints.properties.max_walking_minutes.type, ['integer', 'null']);
  assert.equal(api.components.schemas.Stop.if.properties.kind.const, 'object');
  assert.equal(api.paths['/api/plan'].post.responses['422'].content['application/json'].schema.$ref, '#/components/schemas/Error');
  assert.deepEqual(api.components.schemas.PlanResponse.properties.plan.anyOf[1], { type: 'null' });
});

// ---- schema-lite -------------------------------------------------------------

const api = 'contracts/planner/openapi.yaml';
const schemaOf = (name) => store.resolve(`#/components/schemas/${name}`, api).schema;
const okResponse = read('contracts/examples/plan-response.ok.json');

test('schema: valid example passes, each mutation fails', () => {
  const schema = schemaOf('PlanResponse');
  assert.deepEqual(validate(schema, okResponse, store, api), []);
  const mutations = {
    'extra property': (r) => { r.extra = 1; },
    'wrong enum': (r) => { r.status = 'fine'; },
    'missing required': (r) => { delete r.notices; },
    'empty notices': (r) => { r.notices = []; },
    'wrong type deep': (r) => { r.plan.stops[0].dwell_minutes = '4'; },
    'non-integer dwell': (r) => { r.plan.stops[0].dwell_minutes = 4.5; },
    'object stop without gallery': (r) => { r.plan.stops[0].gallery = null; },
    'object stop without object': (r) => { r.plan.stops[0].object = null; },
    'zero-minute segment': (r) => { r.plan.start.route_to_first[0].walk_minutes = 0; },
    'unknown segment kind': (r) => { r.plan.start.route_to_first[0].kind = 'teleport'; },
    'empty stops': (r) => { r.plan.stops = []; },
  };
  for (const [name, mutate] of Object.entries(mutations)) {
    const r = clone(okResponse);
    mutate(r);
    assert.notEqual(validate(schema, r, store, api).length, 0, `mutation should fail: ${name}`);
  }
});

test('schema: rest stop may have null gallery and null object', () => {
  const r = clone(okResponse);
  Object.assign(r.plan.stops[1], { kind: 'rest', gallery: null, object: null });
  assert.deepEqual(validate(schemaOf('PlanResponse'), r, store, api), []);
});

test('schema: cross-file $ref and floor-graph conditionals', () => {
  const chunk = 'contracts/firestore/object-chunk.schema.json';
  const bad = read('contracts/examples/object-chunk.valid.json');
  bad.objects[0].gallery = '';
  assert.match(validate(store.load(chunk), bad, store, chunk).join('\n'), /objects\[0\]\.gallery/);

  const graphSchema = 'contracts/firestore/floor-graph.schema.json';
  const g = read('contracts/examples/floor-graph.valid.json');
  g.edges[2].name = null; // elevator without a name
  assert.notEqual(validate(store.load(graphSchema), g, store, graphSchema).length, 0);
});

test('schema: lint catches typos, untyped schemas, open objects, bad refs', () => {
  const lint = (s) => lintSchema(s, 'contracts/firestore/x.schema.json', '', store).errors.join('\n');
  assert.match(lint({ type: 'object', additionalProperties: false, requried: ['a'], properties: {} }), /unknown keyword "requried"/);
  assert.match(lint({ type: 'object', additionalProperties: false, properties: { a: {} } }), /accepts any value/);
  assert.match(lint({ type: 'object', properties: { a: { type: 'string' } } }), /additionalProperties/);
  assert.match(lint({ type: 'object', additionalProperties: false, required: ['b'], properties: { a: { type: 'string' } } }), /"b" is not declared/);
  assert.match(lint({ $ref: 'missing.schema.json' }), /ENOENT|unresolvable/);
  assert.match(lint({ type: 'strng' }), /invalid type/);
  assert.match(lint({ type: 'array' }), /must declare items/);
});

test('schema: cycle detection', () => {
  assert.equal(findCycle(new Map([['a', ['b']], ['b', ['c']], ['c', []]])), null);
  assert.deepEqual(findCycle(new Map([['a', ['b']], ['b', ['c']], ['c', ['a']]])), ['a', 'b', 'c', 'a']);
});

// ---- plan invariants ---------------------------------------------------------

const request = read('contracts/examples/plan-request.valid.json');
const check = (r, opts = { request }) => checkPlanInvariants(r, opts).join('\n');

test('invariants: examples are clean', () => {
  assert.equal(check(okResponse), '');
  assert.equal(check(read('contracts/examples/plan-response.no-step-free.json')), '');
});

test('invariants: I1 a plan that claims step-free over stairs is caught', () => {
  const r = clone(okResponse);
  Object.assign(r.plan.stops[0].route_to_next[0], { step_free: false, kind: 'stairs', name: 'Grand Staircase' });
  assert.match(check(r), /I1 plan: step_free is true but 1 segment/);
  r.plan.step_free = false; // now honest about it, but stairs not named, and step-free was requested
  assert.match(check(r), /I1 plan: stairs does not name "Grand Staircase"/);
  assert.match(check(r), /I2 step-free was requested/);
});

test('invariants: I3, I5, I6, I7, I8', () => {
  let r = clone(read('contracts/examples/plan-response.no-step-free.json'));
  r.alternative.stairs = [];
  assert.match(check(r), /I3 alternative must name the stairs/);

  r = clone(okResponse);
  r.plan.stops[0].grounding.matched_tags = ['dinosaurs'];
  assert.match(check(r), /I5 plan: stop 1: matched tag "dinosaurs"/);

  r = clone(okResponse);
  r.plan.totals.walking_minutes = 1;
  assert.match(check(r), /I6 plan: totals.walking_minutes/);

  r = clone(okResponse);
  r.plan.totals.time_budget_minutes = 15;
  assert.match(check(r), /I6 plan: total 16 exceeds budget 15/);

  r = clone(okResponse);
  r.notices = ['Enjoy your visit.'];
  assert.match(check(r), /I7/);

  r = clone(okResponse);
  r.plan.stops[1].order = 3;
  assert.match(check(r), /I8 plan: stop at index 1 has order 3/);
});

test('invariants: I4 gallery must equal the stored gallery', () => {
  const objects = new Map(read('contracts/examples/object-chunk.valid.json').objects.map((o) => [o.id, o]));
  assert.equal(check(okResponse, { request, objects }), '');
  const r = clone(okResponse);
  r.plan.stops[0].gallery = 'A9';
  assert.match(check(r, { request, objects }), /I4 plan: stop 1: gallery "A9" differs from stored "A2"/);
});

// ---- graph checks ------------------------------------------------------------

test('graph: valid example is clean and reports step-free reachability', () => {
  const { errors, stats } = checkGraph(read('contracts/examples/floor-graph.valid.json'));
  assert.deepEqual(errors, []);
  assert.deepEqual(stats.gallery_nodes_not_reachable_step_free, []);
  assert.equal(stats.non_step_free_edges, 1);
});

test('graph: catches dangling edges, duplicate galleries, disconnection, stairs-only nodes', () => {
  const base = read('contracts/examples/floor-graph.valid.json');
  let g = clone(base);
  g.edges[0].to = 'nowhere';
  assert.match(checkGraph(g).errors.join('\n'), /unknown node "nowhere"/);

  g = clone(base);
  g.nodes[2].galleries.push('A1');
  assert.match(checkGraph(g).errors.join('\n'), /gallery "A1" is in both/);

  g = clone(base);
  g.edges = [g.edges[0]];
  assert.match(checkGraph(g).errors.join('\n'), /not connected.*b-wing/);

  g = clone(base);
  g.edges.pop(); // remove the elevator: b-wing is now stairs-only
  const { errors, stats } = checkGraph(g);
  assert.deepEqual(errors, []);
  assert.deepEqual(stats.gallery_nodes_not_reachable_step_free, ['b-wing']);
});

console.log(`${passed} tooling self-tests passed${process.exitCode ? ', some FAILED' : ''}`);
