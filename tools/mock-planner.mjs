#!/usr/bin/env node
// Contract-faithful stand-in for the planner service, for frontend work on a
// machine with no Python. Zero dependencies.
//
//   node tools/mock-planner.mjs [port]        default port 8099
//
// Serves services/planner/static at / and answers the API with the canned
// examples in contracts/examples. Requests are validated against the OpenAPI
// contract, so a frontend that sends a malformed PlanRequest gets the same 422
// the real service must return. It does NOT plan anything:
//
//   interests include "nostepfree"  -> the no_step_free_route example
//   interests include "nothing"     -> the no_plannable_objects example
//   anything else                   -> the ok example

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DocStore, validate } from './lib/schema-lite.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const staticDir = path.join(repoRoot, 'services', 'planner', 'static');
const port = Number(process.argv[2] ?? 8099);
const store = new DocStore(repoRoot);
const API = 'contracts/planner/openapi.yaml';
const example = (name) => JSON.parse(fs.readFileSync(path.join(repoRoot, 'contracts', 'examples', name), 'utf8'));
const requestSchema = store.resolve('#/components/schemas/PlanRequest', API).schema;

const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.json': 'application/json', '.png': 'image/png', '.ico': 'image/x-icon' };

function send(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
}
const error = (res, status, code, message, details) => send(res, status, { error: { code, message, ...(details ? { details } : {}) } });

function serveStatic(urlPath, res) {
  const relPath = urlPath === '/' ? 'index.html' : decodeURIComponent(urlPath.slice(1));
  const file = path.resolve(staticDir, relPath);
  if (!file.startsWith(staticDir) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    res.writeHead(404, { 'content-type': 'text/plain' });
    res.end('not found');
    return;
  }
  res.writeHead(200, { 'content-type': TYPES[path.extname(file)] ?? 'application/octet-stream', 'cache-control': 'no-store' });
  fs.createReadStream(file).pipe(res);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const detail = example('museum-detail.valid.json');

  if (req.method === 'GET' && url.pathname === '/healthz') return send(res, 200, { status: 'ok' });
  if (req.method === 'GET' && url.pathname === '/api/museums') {
    const { id, name, city, object_count, last_ingested_at } = detail;
    return send(res, 200, { museums: [{ id, name, city, object_count, last_ingested_at }] });
  }
  const one = url.pathname.match(/^\/api\/museums\/([^/]+)$/);
  if (req.method === 'GET' && one) {
    return one[1] === detail.id ? send(res, 200, detail) : error(res, 404, 'museum_not_found', `No museum with id '${one[1]}' has been ingested.`);
  }
  if (req.method === 'POST' && url.pathname === '/api/plan') {
    let raw = '';
    req.on('data', (chunk) => { raw += chunk; });
    req.on('end', () => {
      let body;
      try {
        body = JSON.parse(raw);
      } catch {
        return error(res, 422, 'invalid_request', 'Body is not valid JSON.');
      }
      const problems = validate(requestSchema, body, store, API);
      console.log(`POST /api/plan ${JSON.stringify(body)}${problems.length ? `\n  REJECTED: ${problems.join('; ')}` : ''}`);
      if (problems.length) return error(res, 422, 'invalid_request', 'Request does not match the contract.', problems);
      if (body.museum_id !== detail.id) return error(res, 404, 'museum_not_found', `No museum with id '${body.museum_id}' has been ingested.`);
      const interests = (body.interests ?? []).map((s) => s.toLowerCase());
      const name = interests.includes('nostepfree') ? 'plan-response.no-step-free.json' : interests.includes('nothing') ? 'plan-response.no-plannable.json' : 'plan-response.ok.json';
      return send(res, 200, example(name));
    });
    return undefined;
  }
  if (url.pathname.startsWith('/api/')) return error(res, 404, 'invalid_request', 'No such endpoint.');
  if (req.method === 'GET') return serveStatic(url.pathname, res);
  res.writeHead(405);
  return res.end();
});

server.listen(port, () => console.log(`mock planner on http://localhost:${port} (static: ${path.relative(repoRoot, staticDir)})`));
