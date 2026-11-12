#!/usr/bin/env node
// Regenerates the machine-owned sections of docs/ARCHITECTURE.md from /contracts,
// so the write-up cannot drift from the contracts.
//
//   node tools/gen-architecture.mjs          rewrite the generated sections in place
//
// A generated section sits between <!-- GENERATED:name --> and <!-- /GENERATED:name -->.
// Everything outside those markers is hand-written and left untouched.
// tools/validate-contracts.mjs fails if a generated section is stale.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DocStore } from './lib/schema-lite.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ARCH = path.join(repoRoot, 'docs', 'ARCHITECTURE.md');

const cell = (s) => String(s).replace(/\|/g, '\\|').replace(/\n/g, ' ');
const link = (contract) => `[\`${contract.replace(/^contracts\//, '')}\`](../${contract})`;

function scalingText(svc) {
  const s = svc.scaling;
  if (svc.kind === 'cloud-run-service') {
    return `min ${s.min_instances}, max ${s.max_instances} instances; concurrency ${s.concurrency}; ${s.cpu} CPU, ${s.memory}; timeout ${s.timeout_seconds}s`;
  }
  return `${s.tasks} task, parallelism ${s.parallelism}; ${s.cpu} CPU, ${s.memory}; task timeout ${s.task_timeout_seconds}s; max retries ${s.max_retries}`;
}

export function renderGenerated(catalog, api) {
  const out = {};

  out['service-catalog'] = [
    '| Deployable | Kind | Provides | Consumes | Scaling bounds | Failure and retry policy |',
    '|---|---|---|---|---|---|',
    ...catalog.services.map((svc) =>
      `| \`${svc.name}\` (\`${svc.path}\`) | ${svc.kind === 'cloud-run-service' ? 'Cloud Run service' : 'Cloud Run job'} | ${svc.provides.map(link).join('<br>') || 'none'} | ${svc.consumes.map(link).join('<br>') || 'none'} | ${cell(scalingText(svc))} | ${cell(svc.failure_policy)} |`),
  ].join('\n');

  out['env-iam'] = catalog.services.map((svc) => [
    `### \`${svc.name}\``,
    '',
    `Ingress: \`${svc.ingress}\`. Auth: \`${svc.auth}\`. Runs as \`${svc.iam.service_account}\` with ${svc.iam.roles.map((r) => `\`${r}\``).join(', ')}. May be invoked by: ${svc.iam.invokers.map((i) => `\`${i}\``).join(', ')}. Secrets: ${svc.secrets.length ? svc.secrets.map((s) => `\`${s}\``).join(', ') : 'none'}.`,
    '',
    '| Variable | Required | Default | Purpose |',
    '|---|---|---|---|',
    ...svc.env.map((e) => `| \`${e.name}\` | ${e.required ? 'yes' : 'no'} | ${e.default === null ? '' : `\`${e.default}\``} | ${cell(e.description)} |`),
  ].join('\n')).join('\n\n');

  out.resources = [
    '| Resource | Kind | Contract | Notes |',
    '|---|---|---|---|',
    ...catalog.resources.map((r) => `| \`${r.name}\` | ${r.kind} | ${r.contract ? link(r.contract) : 'none'} | ${cell(r.notes)} |`),
  ].join('\n');

  const rows = [];
  for (const [route, item] of Object.entries(api.paths)) {
    for (const [method, op] of Object.entries(item)) {
      rows.push(`| \`${method.toUpperCase()} ${route}\` | \`${op.operationId}\` | ${cell(op.summary)} | ${Object.keys(op.responses).join(', ')} |`);
    }
  }
  out['http-api'] = [
    `Contract version ${api.info.version} (OpenAPI ${api.openapi}).`,
    '',
    '| Operation | operationId | What it does | Responses |',
    '|---|---|---|---|',
    ...rows,
  ].join('\n');

  return out;
}

const MARKER = /<!-- GENERATED:([a-z-]+) -->\n([\s\S]*?)\n<!-- \/GENERATED:\1 -->/g;

export function extractGenerated(text) {
  const out = {};
  for (const m of text.replace(/\r\n/g, '\n').matchAll(MARKER)) out[m[1]] = m[2];
  return out;
}

function main() {
  const store = new DocStore(repoRoot);
  const sections = renderGenerated(store.load('contracts/services.json'), store.load('contracts/planner/openapi.yaml'));
  const before = fs.readFileSync(ARCH, 'utf8').replace(/\r\n/g, '\n');
  const seen = new Set();
  const after = before.replace(MARKER, (whole, name) => {
    if (!(name in sections)) throw new Error(`docs/ARCHITECTURE.md has unknown generated section "${name}"`);
    seen.add(name);
    return `<!-- GENERATED:${name} -->\n${sections[name]}\n<!-- /GENERATED:${name} -->`;
  });
  const missing = Object.keys(sections).filter((name) => !seen.has(name));
  if (missing.length) throw new Error(`docs/ARCHITECTURE.md is missing markers for: ${missing.join(', ')}`);
  if (after !== before) {
    fs.writeFileSync(ARCH, after);
    console.log('docs/ARCHITECTURE.md: generated sections updated');
  } else {
    console.log('docs/ARCHITECTURE.md: already current');
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
