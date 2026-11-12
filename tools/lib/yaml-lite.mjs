// Zero-dependency parser for the YAML subset used by contracts/planner/openapi.yaml:
// block mappings, block sequences, quoted and plain scalars, flow sequences of
// scalars ([a, "b"]), {} and [], and # comments. No anchors, tags, multi-line
// scalars, or flow mappings with content. Anything outside the subset throws,
// so the contract cannot silently drift into YAML this parser misreads.

export function parseYaml(text) {
  const lines = [];
  text.split(/\r?\n/).forEach((raw, i) => {
    if (/\t/.test(raw.match(/^\s*/)[0])) throw err(i, 'tab indentation is not allowed');
    const content = stripComment(raw).trimEnd();
    if (content.trim() === '') return;
    const indent = content.length - content.trimStart().length;
    lines.push({ indent, content: content.trim(), n: i + 1 });
  });
  const state = { lines, pos: 0 };
  if (lines.length === 0) return null;
  const value = parseBlock(state, lines[0].indent);
  if (state.pos < lines.length) throw err(lines[state.pos].n - 1, 'unexpected content (bad indentation?)');
  return value;
}

function err(lineIndex, msg) {
  return new Error(`yaml-lite: line ${lineIndex + 1}: ${msg}`);
}

function stripComment(line) {
  let quote = null;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quote) {
      if (c === '\\' && quote === '"') i++;
      else if (c === quote) quote = null;
    } else if (c === '"' || c === "'") {
      quote = c;
    } else if (c === '#' && (i === 0 || /\s/.test(line[i - 1]))) {
      return line.slice(0, i);
    }
  }
  return line;
}

const isSeqItem = (content) => content === '-' || content.startsWith('- ');

function parseBlock(state, indent) {
  const line = state.lines[state.pos];
  return isSeqItem(line.content) ? parseSequence(state, indent) : parseMapping(state, indent);
}

// Returns { key, rest } if content is a mapping entry, else null.
function splitKey(content) {
  if (content[0] === '"' || content[0] === "'") {
    const q = content[0];
    let i = 1;
    for (; i < content.length; i++) {
      if (content[i] === '\\' && q === '"') i++;
      else if (content[i] === q) break;
    }
    const after = content.slice(i + 1);
    if (after === ':' || after.startsWith(': ')) {
      return { key: parseScalar(content.slice(0, i + 1)), rest: after.slice(1).trim() };
    }
    return null;
  }
  if (content[0] === '[' || content[0] === '{') return null;
  if (content.endsWith(':') && !content.slice(0, -1).includes(': ')) {
    return { key: content.slice(0, -1), rest: '' };
  }
  const idx = content.indexOf(': ');
  if (idx > 0) return { key: content.slice(0, idx), rest: content.slice(idx + 2).trim() };
  return null;
}

function parseMapping(state, indent) {
  const out = {};
  while (state.pos < state.lines.length) {
    const line = state.lines[state.pos];
    if (line.indent < indent) break;
    if (line.indent > indent) throw err(line.n - 1, 'unexpected indentation');
    if (isSeqItem(line.content)) break;
    const kv = splitKey(line.content);
    if (!kv) throw err(line.n - 1, `expected "key: value", got: ${line.content}`);
    const key = String(kv.key);
    if (Object.prototype.hasOwnProperty.call(out, key)) throw err(line.n - 1, `duplicate key "${key}"`);
    state.pos++;
    if (kv.rest !== '') {
      out[key] = parseScalar(kv.rest, line.n);
    } else {
      const next = state.lines[state.pos];
      if (next && next.indent > indent) out[key] = parseBlock(state, next.indent);
      else if (next && next.indent === indent && isSeqItem(next.content)) out[key] = parseSequence(state, indent);
      else out[key] = null;
    }
  }
  return out;
}

function parseSequence(state, indent) {
  const out = [];
  while (state.pos < state.lines.length) {
    const line = state.lines[state.pos];
    if (line.indent !== indent || !isSeqItem(line.content)) {
      if (line.indent > indent) throw err(line.n - 1, 'unexpected indentation');
      break;
    }
    const item = line.content === '-' ? '' : line.content.slice(2).trim();
    if (item === '') {
      state.pos++;
      const next = state.lines[state.pos];
      out.push(next && next.indent > indent ? parseBlock(state, next.indent) : null);
    } else if (splitKey(item)) {
      // "- key: value" starts a mapping whose keys sit two columns in.
      const offset = line.content.length - item.length;
      state.lines[state.pos] = { indent: indent + offset, content: item, n: line.n };
      out.push(parseMapping(state, indent + offset));
    } else {
      state.pos++;
      out.push(parseScalar(item, line.n));
    }
  }
  return out;
}

function parseScalar(s, n = 0) {
  s = s.trim();
  if (s === '') return null;
  if (s[0] === '"') {
    if (s[s.length - 1] !== '"') throw err(n - 1, `unterminated double-quoted string: ${s}`);
    return JSON.parse(s);
  }
  if (s[0] === "'") {
    if (s[s.length - 1] !== "'") throw err(n - 1, `unterminated single-quoted string: ${s}`);
    return s.slice(1, -1).replace(/''/g, "'");
  }
  if (s === '{}') return {};
  if (s[0] === '{') throw err(n - 1, 'flow mappings with content are outside the supported subset');
  if (s[0] === '[') {
    if (s[s.length - 1] !== ']') throw err(n - 1, `unterminated flow sequence: ${s}`);
    const inner = s.slice(1, -1).trim();
    if (inner === '') return [];
    return splitFlow(inner, n).map((part) => parseScalar(part, n));
  }
  if (/^[|>&*!%@`]/.test(s)) throw err(n - 1, `unsupported YAML construct: ${s}`);
  if (s === 'true') return true;
  if (s === 'false') return false;
  if (s === 'null' || s === '~') return null;
  if (/^-?(0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?$/.test(s)) return Number(s);
  return s;
}

function splitFlow(inner, n) {
  const parts = [];
  let quote = null;
  let cur = '';
  for (let i = 0; i < inner.length; i++) {
    const c = inner[i];
    if (quote) {
      cur += c;
      if (c === '\\' && quote === '"') cur += inner[++i];
      else if (c === quote) quote = null;
    } else if (c === '"' || c === "'") {
      quote = c;
      cur += c;
    } else if (c === '[' || c === '{') {
      throw err(n - 1, 'nested flow collections are outside the supported subset');
    } else if (c === ',') {
      parts.push(cur);
      cur = '';
    } else {
      cur += c;
    }
  }
  parts.push(cur);
  return parts;
}
