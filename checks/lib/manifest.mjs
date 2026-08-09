// Pure parser + evaluator for a stack overlay's `required-gates` manifest (the ```yaml block).
// No I/O — fixture-testable. Deliberately parses only the small, fixed shape the template defines
// (reviewers: block list; checks: flow-map list) rather than pulling a full YAML dependency.

/**
 * Extract and parse the required-gates manifest from a stack overlay's markdown.
 * Returns { reviewers: string[], checks: [{ id, run, blocking }] }.
 * Commented-out list items (`# - ui-ux-reviewer`) are ignored, as are inline `# ...` trailers.
 */
export function parseManifest(md) {
  const block = (md.match(/```ya?ml\s*([\s\S]*?)```/g) || [])
    .map((b) => b.replace(/```ya?ml\s*/, '').replace(/```$/, ''))
    .find((b) => /required-gates\s*:/.test(b));
  if (!block) return { reviewers: [], checks: [] };

  const lines = block.split('\n');
  const reviewers = [];
  const checks = [];
  let mode = null; // 'reviewers' | 'checks'
  for (const raw of lines) {
    const line = stripComment(raw);
    if (!line.trim()) continue;
    if (/^\s*reviewers\s*:/.test(line)) { mode = 'reviewers'; continue; }
    if (/^\s*checks\s*:/.test(line)) { mode = 'checks'; continue; }
    if (/^\s{0,2}[a-z-]+\s*:/.test(line) && !/^\s*-/.test(line)) { mode = null; continue; } // some other key

    if (mode === 'reviewers') {
      const m = line.match(/^\s*-\s*([a-z][a-z0-9-]*)\s*$/i);
      if (m) reviewers.push(m[1]);
    } else if (mode === 'checks') {
      const m = line.match(/^\s*-\s*\{(.+)\}\s*$/);
      if (m) checks.push(parseInlineMap(m[1]));
    }
  }
  return { reviewers, checks };
}

function parseInlineMap(s) {
  const obj = {};
  // split on commas that are not inside quotes
  for (const pair of splitTopLevel(s)) {
    const i = pair.indexOf(':');
    if (i === -1) continue;
    const k = pair.slice(0, i).trim();
    let v = pair.slice(i + 1).trim().replace(/^["']|["']$/g, '');
    if (v === 'true') v = true; else if (v === 'false') v = false;
    obj[k] = v;
  }
  if (obj.blocking === undefined) obj.blocking = true; // default per the schema
  return obj;
}

function splitTopLevel(s) {
  const out = []; let cur = ''; let q = null;
  for (const ch of s) {
    if (q) { if (ch === q) q = null; cur += ch; }
    else if (ch === '"' || ch === "'") { q = ch; cur += ch; }
    else if (ch === ',') { out.push(cur); cur = ''; }
    else cur += ch;
  }
  if (cur.trim()) out.push(cur);
  return out;
}
function stripComment(line) {
  // drop a trailing/whole-line comment, but not a '#' inside quotes
  let q = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (q) { if (ch === q) q = null; }
    else if (ch === '"' || ch === "'") q = ch;
    else if (ch === '#') return line.slice(0, i);
  }
  return line;
}

/**
 * Completeness rule (SYSTEM.md §5.4, grill-me Q9). Given a parsed manifest, the set of installed
 * agent ids, and a fileExists(path) predicate, assert:
 *   - every `reviewers` entry has an installed agent, and
 *   - every `checks` entry whose `run` invokes a repo script (checks/*.mjs or a local test path) has
 *     that file present (catches "manifest references rls-coverage but the script is missing").
 * The two universal meta-checks must also be present as scripts.
 * Returns { ok, missing: [{ kind, name, reason }] }.
 */
export function evaluateCompleteness(manifest, { installedAgents, fileExists, mandatoryReviewers = ['security-auditor', 'logic-reviewer'] }) {
  const missing = [];
  const installed = new Set(installedAgents.map((a) => a.toLowerCase()));
  const declared = new Set(manifest.reviewers.map((r) => r.toLowerCase()));

  // canon-mandatory reviewers must be DECLARED by the manifest (not just installed if declared) —
  // otherwise a stack can silently drop logic-reviewer/security-auditor and pass completeness.
  for (const m of mandatoryReviewers) {
    if (!declared.has(m.toLowerCase())) missing.push({ kind: 'reviewer', name: m, reason: `canon-mandatory reviewer '${m}' is not declared in the stack manifest` });
  }
  for (const rev of manifest.reviewers) {
    if (!installed.has(rev.toLowerCase())) missing.push({ kind: 'reviewer', name: rev, reason: `manifest requires reviewer '${rev}' but no agent file is installed` });
  }
  for (const meta of ['checks/review-ledger-current.mjs', 'checks/gate-completeness.mjs']) {
    if (!fileExists(meta)) missing.push({ kind: 'meta-check', name: meta, reason: `universal meta-check script missing` });
  }
  for (const c of manifest.checks) {
    const script = repoScriptFromRun(c.run);
    if (script && !fileExists(script)) missing.push({ kind: 'check', name: c.id, reason: `check '${c.id}' runs '${c.run}' but '${script}' is not present` });
  }
  return { ok: missing.length === 0, missing };
}

/** If a check's `run` command references a repo script/path we can existence-check, return it.
 *  Skips leading flags (`-q`, `--project=ci`) and env-style `KEY=val` tokens before the path. */
export function repoScriptFromRun(run) {
  if (!run || typeof run !== 'string') return null;
  const node = run.match(/\bnode\s+((?:--?[\w-]+(?:=\S+)?\s+)*)(\S+\.(?:mjs|cjs|js))\b/);
  if (node) return node[2];
  for (const tool of [/\bplaywright\s+test\b/, /\bpytest\b/, /\bvitest\s+run\b/]) {
    const m = run.match(new RegExp(tool.source + '\\s+(.*)$'));
    if (m) return firstPositional(m[1]);
  }
  return null; // e.g. bare `tsc --noEmit`, `eslint .` — nothing repo-specific to existence-check
}

/** First token that looks like a repo PATH: not a flag (`-x`, `--x`, `--x=y`), not an env-style
 *  `KEY=val`, not a quoted flag value (`"test_x"`), and either containing `/` or ending in a file
 *  extension. F-014: a value-taking option's space-separated value (`-k "test_x"`) is thus not
 *  mistaken for the path (over-strict = fail-closed: breaks a legit manifest, never passes a bad one). */
function firstPositional(rest) {
  for (const t of rest.split(/\s+/).filter(Boolean)) {
    if (t.startsWith('-') || /^[\w.]+=/.test(t) || /^["']/.test(t)) continue;
    if (t.includes('/') || /\.[A-Za-z0-9]+$/.test(t)) return t;
  }
  return null;
}
