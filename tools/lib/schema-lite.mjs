// Zero-dependency JSON Schema (draft 2020-12 subset) loader, linter, and validator.
// Covers exactly the keywords the contracts use. An unknown keyword is a lint
// error, so a typo such as "requried" cannot pass silently.

import fs from 'node:fs';
import path from 'node:path';
import { parseYaml } from './yaml-lite.mjs';

const ANNOTATIONS = new Set(['$schema', '$id', 'title', 'description', 'default', 'examples', 'summary', 'deprecated', 'format']);
const VALIDATION = new Set([
  '$ref', '$defs', 'type', 'enum', 'const', 'properties', 'required', 'additionalProperties', 'items',
  'minItems', 'maxItems', 'uniqueItems', 'minLength', 'maxLength', 'pattern', 'minimum', 'maximum',
  'exclusiveMinimum', 'exclusiveMaximum', 'anyOf', 'oneOf', 'allOf', 'if', 'then', 'else', 'not',
]);
const TYPES = new Set(['string', 'number', 'integer', 'boolean', 'object', 'array', 'null']);

export class DocStore {
  constructor(repoRoot) {
    this.repoRoot = repoRoot;
    this.docs = new Map();
  }

  // file: repo-relative path with forward slashes.
  load(file) {
    const key = path.posix.normalize(file);
    if (!this.docs.has(key)) {
      const abs = path.join(this.repoRoot, key);
      const text = fs.readFileSync(abs, 'utf8');
      const doc = /\.ya?ml$/.test(key) ? parseYaml(text) : JSON.parse(text);
      this.docs.set(key, doc);
    }
    return this.docs.get(key);
  }

  // ref: "#/a/b", "other.json", or "other.json#/a/b", relative to fromFile.
  resolve(ref, fromFile) {
    const [filePart, pointer = ''] = ref.split('#');
    const file = filePart ? path.posix.join(path.posix.dirname(fromFile), filePart) : path.posix.normalize(fromFile);
    let node = this.load(file);
    if (pointer) {
      for (const raw of pointer.split('/').slice(1)) {
        const token = raw.replace(/~1/g, '/').replace(/~0/g, '~');
        if (node === null || typeof node !== 'object' || !(token in node)) {
          throw new Error(`unresolvable $ref "${ref}" from ${fromFile}`);
        }
        node = node[token];
      }
    }
    return { schema: node, file };
  }
}

function typeOf(v) {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  if (typeof v === 'number') return Number.isInteger(v) ? 'integer' : 'number';
  return typeof v;
}

function matchesType(v, t) {
  const actual = typeOf(v);
  return actual === t || (t === 'number' && actual === 'integer');
}

const deepEqual = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const FORMATS = {
  'date-time': (s) => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/.test(s) && !Number.isNaN(Date.parse(s)),
  date: (s) => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s)),
};

// Returns a list of error strings; empty means valid.
export function validate(schema, instance, store, file, at = '$') {
  const errors = [];
  const fail = (msg) => errors.push(`${at}: ${msg}`);
  if (schema === true || schema === undefined) return errors;
  if (schema === false) return [`${at}: no value allowed here`];

  if (schema.$ref) {
    const target = store.resolve(schema.$ref, file);
    errors.push(...validate(target.schema, instance, store, target.file, at));
  }
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((t) => matchesType(instance, t))) fail(`expected type ${types.join('|')}, got ${typeOf(instance)}`);
  }
  if (schema.enum && !schema.enum.some((e) => deepEqual(e, instance))) fail(`value ${JSON.stringify(instance)} not in enum ${JSON.stringify(schema.enum)}`);
  if ('const' in schema && !deepEqual(schema.const, instance)) fail(`expected const ${JSON.stringify(schema.const)}, got ${JSON.stringify(instance)}`);

  if (typeof instance === 'string') {
    if (schema.minLength !== undefined && instance.length < schema.minLength) fail(`shorter than minLength ${schema.minLength}`);
    if (schema.maxLength !== undefined && instance.length > schema.maxLength) fail(`longer than maxLength ${schema.maxLength}`);
    if (schema.pattern !== undefined && !new RegExp(schema.pattern, 'u').test(instance)) fail(`"${instance}" does not match pattern ${schema.pattern}`);
    if (schema.format && FORMATS[schema.format] && !FORMATS[schema.format](instance)) fail(`"${instance}" is not a valid ${schema.format}`);
  }
  if (typeof instance === 'number') {
    if (schema.minimum !== undefined && instance < schema.minimum) fail(`${instance} is below minimum ${schema.minimum}`);
    if (schema.maximum !== undefined && instance > schema.maximum) fail(`${instance} is above maximum ${schema.maximum}`);
    if (schema.exclusiveMinimum !== undefined && instance <= schema.exclusiveMinimum) fail(`${instance} is not above exclusiveMinimum ${schema.exclusiveMinimum}`);
    if (schema.exclusiveMaximum !== undefined && instance >= schema.exclusiveMaximum) fail(`${instance} is not below exclusiveMaximum ${schema.exclusiveMaximum}`);
  }
  if (Array.isArray(instance)) {
    if (schema.minItems !== undefined && instance.length < schema.minItems) fail(`fewer than minItems ${schema.minItems}`);
    if (schema.maxItems !== undefined && instance.length > schema.maxItems) fail(`more than maxItems ${schema.maxItems}`);
    if (schema.uniqueItems && new Set(instance.map((v) => JSON.stringify(v))).size !== instance.length) fail('items are not unique');
    if (schema.items !== undefined) instance.forEach((v, i) => errors.push(...validate(schema.items, v, store, file, `${at}[${i}]`)));
  }
  if (typeOf(instance) === 'object') {
    for (const name of schema.required ?? []) if (!(name in instance)) fail(`missing required property "${name}"`);
    for (const [name, sub] of Object.entries(schema.properties ?? {})) {
      if (name in instance) errors.push(...validate(sub, instance[name], store, file, `${at}.${name}`));
    }
    if (schema.additionalProperties !== undefined && schema.additionalProperties !== true) {
      const known = new Set(Object.keys(schema.properties ?? {}));
      for (const name of Object.keys(instance)) {
        if (known.has(name)) continue;
        if (schema.additionalProperties === false) fail(`unexpected property "${name}"`);
        else errors.push(...validate(schema.additionalProperties, instance[name], store, file, `${at}.${name}`));
      }
    }
  }
  for (const sub of schema.allOf ?? []) errors.push(...validate(sub, instance, store, file, at));
  if (schema.anyOf && !schema.anyOf.some((sub) => validate(sub, instance, store, file, at).length === 0)) fail('matches none of anyOf');
  if (schema.oneOf) {
    const hits = schema.oneOf.filter((sub) => validate(sub, instance, store, file, at).length === 0).length;
    if (hits !== 1) fail(`matches ${hits} of oneOf, expected exactly 1`);
  }
  if (schema.not && validate(schema.not, instance, store, file, at).length === 0) fail('matches a schema it must not match');
  if (schema.if !== undefined) {
    const branch = validate(schema.if, instance, store, file, at).length === 0 ? schema.then : schema.else;
    if (branch !== undefined) errors.push(...validate(branch, instance, store, file, at));
  }
  return errors;
}

// Walks every subschema. visit(schema, pointer, { conditional }) is called for each.
export function walkSchema(schema, pointer, visit, ctx = { conditional: false }) {
  if (schema === null || typeof schema !== 'object' || Array.isArray(schema)) return;
  visit(schema, pointer, ctx);
  const sub = (s, p, conditional = ctx.conditional) => walkSchema(s, `${pointer}/${p}`, visit, { conditional });
  for (const [k, v] of Object.entries(schema.properties ?? {})) sub(v, `properties/${k}`);
  for (const [k, v] of Object.entries(schema.$defs ?? {})) sub(v, `$defs/${k}`, false);
  if (schema.items && typeof schema.items === 'object') sub(schema.items, 'items');
  if (schema.additionalProperties && typeof schema.additionalProperties === 'object') sub(schema.additionalProperties, 'additionalProperties');
  for (const k of ['anyOf', 'oneOf', 'allOf']) (schema[k] ?? []).forEach((s, i) => sub(s, `${k}/${i}`));
  for (const k of ['if', 'then', 'else', 'not']) if (schema[k] && typeof schema[k] === 'object') sub(schema[k], k, true);
}

// Static checks on one schema tree. Returns { errors, refs } where refs are the raw $ref strings found.
export function lintSchema(schema, file, pointer, store) {
  const errors = [];
  const refs = [];
  walkSchema(schema, pointer, (s, p, ctx) => {
    const where = `${file}#${p}`;
    for (const key of Object.keys(s)) {
      if (!ANNOTATIONS.has(key) && !VALIDATION.has(key)) errors.push(`${where}: unknown keyword "${key}"`);
    }
    if (s.type !== undefined) {
      for (const t of Array.isArray(s.type) ? s.type : [s.type]) if (!TYPES.has(t)) errors.push(`${where}: invalid type "${t}"`);
    }
    if (s.$ref !== undefined) {
      refs.push(s.$ref);
      try {
        store.resolve(s.$ref, file);
      } catch (e) {
        errors.push(`${where}: ${e.message}`);
      }
    }
    if (s.pattern !== undefined) {
      try {
        new RegExp(s.pattern, 'u');
      } catch {
        errors.push(`${where}: pattern is not a valid regular expression`);
      }
    }
    if (s.enum !== undefined && (!Array.isArray(s.enum) || s.enum.length === 0)) errors.push(`${where}: enum must be a non-empty array`);
    if (s.required !== undefined && s.properties !== undefined && !ctx.conditional) {
      for (const name of s.required) if (!(name in s.properties)) errors.push(`${where}: required property "${name}" is not declared in properties`);
    }
    // Type safety: outside if/then/else, every schema must constrain its value somehow.
    const constrains = ['type', 'enum', 'const', '$ref', 'anyOf', 'oneOf', 'allOf', 'if'].some((k) => k in s);
    if (!constrains && !ctx.conditional) errors.push(`${where}: schema accepts any value (add type, enum, const, or $ref)`);
    if (s.type === 'object' && s.properties !== undefined && s.additionalProperties === undefined && !ctx.conditional && !file.includes('/upstream/')) {
      errors.push(`${where}: object schema must state additionalProperties (contracts we own are closed by default)`);
    }
    if (s.type === 'array' && s.items === undefined) errors.push(`${where}: array schema must declare items`);
  });
  return { errors, refs };
}

// Detects cycles in a directed graph given as Map<node, Iterable<node>>. Returns one cycle as an array, or null.
export function findCycle(graph) {
  const state = new Map();
  const stack = [];
  const visit = (node) => {
    state.set(node, 1);
    stack.push(node);
    for (const next of graph.get(node) ?? []) {
      if (state.get(next) === 1) return [...stack.slice(stack.indexOf(next)), next];
      if (!state.has(next)) {
        const found = visit(next);
        if (found) return found;
      }
    }
    stack.pop();
    state.set(node, 2);
    return null;
  };
  for (const node of graph.keys()) {
    if (!state.has(node)) {
      const found = visit(node);
      if (found) return found;
    }
  }
  return null;
}
