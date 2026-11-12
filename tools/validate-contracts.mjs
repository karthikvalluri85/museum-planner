#!/usr/bin/env node
// Contract lint gate. Zero dependencies: runs on a bare Node 20+.
//
//   node tools/validate-contracts.mjs                      full gate
//   node tools/validate-contracts.mjs --graph <file>...    schema + cross-field checks for floor graphs
//   node tools/validate-contracts.mjs --instance <schema> <file>...
//                                                          validate JSON files against one contract schema
//                                                          (schema may carry a pointer: file.yaml#/components/schemas/X)
//
// The full gate checks: every contract parses; every schema is lint-clean (known
// keywords only, typed, closed objects, resolvable $refs); no circular references
// between files, between schemas, or between services; the OpenAPI document is
// structurally sound; services.json is complete and points at real contracts;
// every example validates (or fails) as its manifest entry says; and
// docs/ARCHITECTURE.md matches what tools/gen-architecture.mjs would generate.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DocStore, validate, lintSchema, walkSchema, findCycle } from './lib/schema-lite.mjs';
import { checkPlanInvariants } from './lib/plan-invariants.mjs';
import { checkGraph } from './lib/graph-checks.mjs';
import { renderGenerated, extractGenerated } from './gen-architecture.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const store = new DocStore(repoRoot);
const errors = [];
const warnings = [];
let checks = 0;

const rel = (p) => path.relative(repoRoot, path.resolve(p)).split(path.sep).join('/');
const readJson = (file) => JSON.parse(fs.readFileSync(path.join(repoRoot, file), 'utf8'));
const exists = (file) => fs.existsSync(path.join(repoRoot, file));

function listFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(path.join(repoRoot, dir), { withFileTypes: true })) {
    const p = `${dir}/${entry.name}`;
    if (entry.isDirectory()) out.push(...listFiles(p));
    else out.push(p);
  }
  return out;
}

function schemaAt(spec) {
  const [file, pointer] = spec.split('#');
  const target = store.resolve(pointer ? `#${pointer}` : '#', file);
  return { schema: target.schema, file };
}

function validateInstance(schemaSpec, instanceFile) {
  const { schema, file } = schemaAt(schemaSpec);
  return validate(schema, readJson(instanceFile), store, file);
}

// ---- modes -----------------------------------------------------------------

function runGraphMode(files) {
  for (const f of files) {
    const file = rel(f);
    const schemaErrors = validateInstance('contracts/firestore/floor-graph.schema.json', file);
    const { errors: crossErrors, stats } = schemaErrors.length ? { errors: [], stats: null } : checkGraph(readJson(file));
    const all = [...schemaErrors, ...crossErrors];
    if (all.length) {
      console.log(`FAIL ${file}`);
      all.forEach((e) => console.log(`  ${e}`));
      process.exitCode = 1;
    } else {
      console.log(`ok   ${file}`);
      console.log(`  ${JSON.stringify(stats)}`);
    }
  }
}

function runInstanceMode(schemaSpec, files) {
  for (const f of files) {
    const file = rel(f);
    const errs = validateInstance(schemaSpec, file);
    if (errs.length) {
      console.log(`FAIL ${file}`);
      errs.slice(0, 20).forEach((e) => console.log(`  ${e}`));
      if (errs.length > 20) console.log(`  ... and ${errs.length - 20} more`);
      process.exitCode = 1;
    } else {
      console.log(`ok   ${file}`);
    }
  }
}

// ---- full gate ---------------------------------------------------------------

function gate() {
  const contractFiles = listFiles('contracts').filter((f) => !f.startsWith('contracts/examples/') && /\.(json|ya?ml)$/.test(f));
  const schemaFiles = contractFiles.filter((f) => f.endsWith('.schema.json'));
  const openapiFile = 'contracts/planner/openapi.yaml';

  // 1. Parse everything.
  for (const f of contractFiles) {
    checks++;
    try {
      store.load(f);
    } catch (e) {
      errors.push(`${f}: does not parse: ${e.message}`);
    }
  }
  if (errors.length) return;

  // 2. Lint JSON Schema files; collect file-level reference graph.
  const fileRefs = new Map(contractFiles.map((f) => [f, new Set()]));
  const ids = new Map();
  for (const f of schemaFiles) {
    checks++;
    const doc = store.load(f);
    if (doc.$schema !== 'https://json-schema.org/draft/2020-12/schema') errors.push(`${f}: $schema must be draft 2020-12`);
    if (!doc.$id) errors.push(`${f}: missing $id`);
    else if (ids.has(doc.$id)) errors.push(`${f}: $id duplicates ${ids.get(doc.$id)}`);
    else ids.set(doc.$id, f);
    if (!doc.title || !doc.description) errors.push(`${f}: needs both title and description`);
    const { errors: lintErrors, refs } = lintSchema(doc, f, '', store);
    errors.push(...lintErrors);
    for (const ref of refs) {
      const filePart = ref.split('#')[0];
      if (filePart) fileRefs.get(f).add(path.posix.join(path.posix.dirname(f), filePart));
    }
  }

  // 3. OpenAPI structure.
  const api = store.load(openapiFile);
  lintOpenApi(api, openapiFile);

  // 4. Circular references: between files, and between named schemas inside a file.
  checks++;
  const fileCycle = findCycle(fileRefs);
  if (fileCycle) errors.push(`circular $ref between files: ${fileCycle.join(' -> ')}`);
  for (const f of [...schemaFiles, openapiFile]) {
    const doc = store.load(f);
    const named = f === openapiFile ? { prefix: '#/components/schemas/', defs: doc.components?.schemas ?? {} } : { prefix: '#/$defs/', defs: doc.$defs ?? {} };
    const graph = new Map();
    for (const [name, schema] of Object.entries(named.defs)) {
      const targets = new Set();
      walkSchema(schema, '', (s) => {
        if (typeof s.$ref === 'string' && s.$ref.startsWith(named.prefix)) targets.add(s.$ref.slice(named.prefix.length));
      });
      graph.set(name, targets);
    }
    const cycle = findCycle(graph);
    if (cycle) errors.push(`${f}: circular schema reference: ${cycle.join(' -> ')}`);
  }

  // 5. Service catalog.
  lintServices(contractFiles);

  // 6. Examples.
  const manifest = readJson('contracts/examples/manifest.json');
  const usedExamples = new Set(['contracts/examples/manifest.json']);
  for (const c of manifest.cases) {
    checks++;
    usedExamples.add(c.instance);
    if (c.request) usedExamples.add(c.request);
    let errs;
    try {
      errs = validateInstance(c.schema, c.instance);
    } catch (e) {
      errors.push(`example ${c.instance}: ${e.message}`);
      continue;
    }
    if (c.valid && errs.length) errors.push(`example ${c.instance} should be valid against ${c.schema}:\n      ${errs.join('\n      ')}`);
    if (!c.valid && errs.length === 0) errors.push(`example ${c.instance} should be INVALID against ${c.schema} but passed`);
    if (c.valid && c.invariants) {
      const request = c.request ? readJson(c.request) : null;
      const violations = checkPlanInvariants(readJson(c.instance), { request });
      if (violations.length) errors.push(`example ${c.instance} breaks plan invariants:\n      ${violations.join('\n      ')}`);
    }
    if (c.valid && c.graph) {
      const { errors: graphErrors } = checkGraph(readJson(c.instance));
      if (graphErrors.length) errors.push(`example ${c.instance} breaks graph rules:\n      ${graphErrors.join('\n      ')}`);
    }
  }
  for (const f of listFiles('contracts/examples')) {
    if (f.endsWith('.json') && !usedExamples.has(f) && f !== 'contracts/services.json') warnings.push(`${f}: example is not listed in manifest.json`);
  }
  const covered = new Set(manifest.cases.map((c) => c.schema.split('#')[0]));
  for (const f of [...schemaFiles, openapiFile]) if (!covered.has(f)) errors.push(`${f}: no example exercises this contract`);

  // 7. docs/ARCHITECTURE.md generated sections are current.
  checks++;
  const archFile = 'docs/ARCHITECTURE.md';
  if (!exists(archFile)) {
    warnings.push(`${archFile} does not exist yet`);
  } else {
    const current = extractGenerated(fs.readFileSync(path.join(repoRoot, archFile), 'utf8'));
    const expected = renderGenerated(readJson('contracts/services.json'), api);
    for (const [name, body] of Object.entries(expected)) {
      if (current[name] === undefined) errors.push(`${archFile}: missing generated section "${name}"`);
      else if (current[name].trim() !== body.trim()) errors.push(`${archFile}: section "${name}" is out of date. Run: node tools/gen-architecture.mjs`);
    }
  }
}

function lintOpenApi(api, file) {
  checks++;
  if (typeof api.openapi !== 'string' || !api.openapi.startsWith('3.1.')) errors.push(`${file}: openapi must be 3.1.x`);
  if (!api.info?.title || !api.info?.version) errors.push(`${file}: info.title and info.version are required`);
  if (!api.paths || Object.keys(api.paths).length === 0) errors.push(`${file}: no paths`);

  const schemas = api.components?.schemas ?? {};
  const used = new Set();
  const noteRefs = (refs) => refs.forEach((r) => r.startsWith('#/components/schemas/') && used.add(r.slice('#/components/schemas/'.length)));

  for (const [name, schema] of Object.entries(schemas)) {
    const { errors: lintErrors, refs } = lintSchema(schema, file, `/components/schemas/${name}`, store);
    errors.push(...lintErrors);
    noteRefs(refs);
  }

  const operationIds = new Set();
  const methods = ['get', 'put', 'post', 'delete', 'patch', 'head', 'options'];
  const deref = (node) => (node && node.$ref ? store.resolve(node.$ref, file).schema : node);
  const lintContent = (content, where) => {
    if (!content?.['application/json']?.schema) {
      errors.push(`${where}: needs content.application/json.schema`);
      return;
    }
    const { errors: lintErrors, refs } = lintSchema(content['application/json'].schema, file, where, store);
    errors.push(...lintErrors);
    noteRefs(refs);
  };

  for (const [route, item] of Object.entries(api.paths ?? {})) {
    if (!route.startsWith('/')) errors.push(`${file}: path "${route}" must start with /`);
    const templated = [...route.matchAll(/\{([^}]+)\}/g)].map((m) => m[1]);
    for (const method of Object.keys(item)) {
      if (!methods.includes(method)) {
        errors.push(`${file}: ${route}: unknown key "${method}"`);
        continue;
      }
      checks++;
      const op = item[method];
      const where = `${file}: ${method.toUpperCase()} ${route}`;
      if (!op.operationId) errors.push(`${where}: missing operationId`);
      else if (operationIds.has(op.operationId)) errors.push(`${where}: duplicate operationId ${op.operationId}`);
      else operationIds.add(op.operationId);
      if (!op.summary) errors.push(`${where}: missing summary`);

      let params = [];
      try {
        params = (op.parameters ?? []).map(deref);
      } catch (e) {
        errors.push(`${where}: ${e.message}`);
      }
      for (const name of templated) {
        const p = params.find((x) => x.in === 'path' && x.name === name);
        if (!p) errors.push(`${where}: path parameter {${name}} is not declared`);
        else if (p.required !== true) errors.push(`${where}: path parameter {${name}} must be required`);
      }
      for (const p of params) {
        if (!p.schema) errors.push(`${where}: parameter ${p.name} has no schema`);
        else noteRefs(lintSchema(p.schema, file, `/parameters/${p.name}`, store).refs);
      }

      if (op.requestBody) lintContent(op.requestBody.content, `${where} requestBody`);
      const responses = op.responses ?? {};
      if (Object.keys(responses).length === 0) errors.push(`${where}: no responses`);
      if (!Object.keys(responses).some((code) => /^2\d\d$/.test(code))) errors.push(`${where}: no 2xx response`);
      for (const [code, response] of Object.entries(responses)) {
        if (!/^[1-5]\d\d$/.test(code)) errors.push(`${where}: invalid status code "${code}"`);
        if (!response.description) errors.push(`${where} ${code}: missing description`);
        lintContent(response.content, `${where} ${code}`);
      }
    }
  }
  for (const name of Object.keys(schemas)) if (!used.has(name)) warnings.push(`${file}: schema ${name} is never referenced`);
}

function lintServices(contractFiles) {
  checks++;
  const catalogErrors = validateInstance('contracts/services.schema.json', 'contracts/services.json');
  if (catalogErrors.length) {
    errors.push(`contracts/services.json does not match services.schema.json:\n      ${catalogErrors.join('\n      ')}`);
    return;
  }
  const catalog = readJson('contracts/services.json');
  const names = new Set();
  const providers = new Map();
  const referenced = new Set(['contracts/services.json', 'contracts/services.schema.json']);

  for (const svc of catalog.services) {
    if (names.has(svc.name)) errors.push(`services.json: duplicate service "${svc.name}"`);
    names.add(svc.name);
    if (svc.path !== `services/${svc.name}`) errors.push(`services.json: ${svc.name}: path must be services/${svc.name}`);
    if (!exists(svc.path)) warnings.push(`services.json: ${svc.name}: directory ${svc.path} does not exist yet`);
    const envNames = svc.env.map((e) => e.name);
    if (new Set(envNames).size !== envNames.length) errors.push(`services.json: ${svc.name}: duplicate env var`);
    for (const e of svc.env) if (e.required && e.default !== null) errors.push(`services.json: ${svc.name}: ${e.name} is required but has a default`);
    if (svc.kind === 'cloud-run-service') {
      for (const k of ['min_instances', 'max_instances', 'concurrency', 'timeout_seconds']) if (svc.scaling[k] === undefined) errors.push(`services.json: ${svc.name}: scaling.${k} is required for a service`);
      if (svc.auth === 'allow-unauthenticated' && !svc.iam.invokers.includes('allUsers')) errors.push(`services.json: ${svc.name}: allow-unauthenticated needs invoker allUsers`);
    } else {
      for (const k of ['tasks', 'parallelism', 'max_retries', 'task_timeout_seconds']) if (svc.scaling[k] === undefined) errors.push(`services.json: ${svc.name}: scaling.${k} is required for a job`);
      if (svc.ingress !== 'none') errors.push(`services.json: ${svc.name}: a job has no ingress; use "none"`);
    }
    for (const c of [...svc.provides, ...svc.consumes]) {
      referenced.add(c);
      if (!exists(c)) errors.push(`services.json: ${svc.name}: contract ${c} does not exist`);
    }
    for (const c of svc.provides) {
      if (providers.has(c)) errors.push(`services.json: ${c} has two providers: ${providers.get(c)} and ${svc.name}`);
      providers.set(c, svc.name);
    }
    if (svc.provides.some((c) => svc.consumes.includes(c))) errors.push(`services.json: ${svc.name} both provides and consumes the same contract`);
  }
  for (const r of catalog.resources) {
    if (r.contract) {
      referenced.add(r.contract);
      if (!exists(r.contract)) errors.push(`services.json: resource ${r.name}: contract ${r.contract} does not exist`);
    }
  }

  // A consumed contract needs a provider inside the system, unless it is an external boundary.
  const graph = new Map(catalog.services.map((s) => [s.name, new Set()]));
  for (const svc of catalog.services) {
    for (const c of svc.consumes) {
      const external = c.startsWith('contracts/events/') || c.startsWith('contracts/upstream/');
      if (providers.has(c)) graph.get(svc.name).add(providers.get(c));
      else if (!external) errors.push(`services.json: ${svc.name} consumes ${c} but no service provides it`);
    }
  }
  const cycle = findCycle(graph);
  if (cycle) errors.push(`circular dependency between services: ${cycle.join(' -> ')}`);

  // A file that is only ever $ref'd from a referenced file is fine; anything else is an orphan.
  for (const f of contractFiles) {
    if (referenced.has(f)) continue;
    const reachedByRef = [...referenced].some((r) => r.endsWith('.schema.json') && JSON.stringify(store.load(r)).includes(`"${path.posix.basename(f)}`));
    if (!reachedByRef) errors.push(`${f}: contract is not referenced by any service in services.json`);
  }
}

// ---- main --------------------------------------------------------------------

const args = process.argv.slice(2);
try {
  if (args[0] === '--graph') {
    runGraphMode(args.slice(1));
  } else if (args[0] === '--instance') {
    runInstanceMode(args[1], args.slice(2));
  } else {
    gate();
    warnings.forEach((w) => console.log(`warn  ${w}`));
    errors.forEach((e) => console.log(`ERROR ${e}`));
    console.log(`\n${checks} checks, ${errors.length} error(s), ${warnings.length} warning(s)`);
    if (errors.length) process.exitCode = 1;
    else console.log('contracts: OK');
  }
} catch (e) {
  console.error(`validate-contracts crashed: ${e.stack}`);
  process.exitCode = 2;
}
