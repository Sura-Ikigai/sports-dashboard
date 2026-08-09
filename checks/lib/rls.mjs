// Pure core for the RLS coverage gate (SYSTEM.md §5.4, grill-me Q10–Q11). The gap's premise is that a
// missing policy is invisible in a diff — so a table that ships without a deny-test reproduces the
// failure one layer up. This enumerates RLS tables from migrations and the tables a deny-test covers,
// and reports the gap. No I/O — fixture-testable.

/** Tables that have RLS enabled, parsed from migration SQL (`ALTER TABLE x ENABLE ROW LEVEL SECURITY`). */
export function rlsTablesFromMigrations(sql) {
  const set = new Set();
  const re = /alter\s+table\s+(?:only\s+)?(?:if\s+exists\s+)?["'`]?([A-Za-z0-9_."]+?)["'`]?\s+enable\s+row\s+level\s+security/gi;
  let m;
  while ((m = re.exec(sql)) !== null) set.add(bareTable(m[1]));
  return set;
}

/**
 * Tables a deny-test covers. Convention (checks/reference/rls.deny.spec.ts): each table's deny-test is
 * declared with `rlsDeny('<table>', …)` or an `// @rls-deny <table>` annotation — explicit + greppable,
 * so coverage never depends on guessing which `.from()` call was the security-relevant one.
 * A COMMENTED-OUT `rlsDeny('x')` must NOT count (that table is unguarded); the executable-call scan runs
 * on comment-stripped source. The `@rls-deny` form is a deliberate comment annotation, so it scans the
 * raw source.
 */
export function coveredTablesFromTests(src) {
  const set = new Set();
  // F-013: drop lines carrying a skip/todo marker (`it.skip`, `describe.todo`, `xit`, `xdescribe`) —
  // a skipped deny-test does not run, so its table is not actually guarded and must not count.
  const code = stripComments(src)
    .split('\n')
    .filter((l) => !/\b(?:it|describe|test)\.(?:skip|todo)\b|\bx(?:it|describe)\s*\(/.test(l))
    .join('\n');
  let m;
  const call = /rlsDeny\(\s*['"`]([A-Za-z0-9_]+)['"`]/g;
  while ((m = call.exec(code)) !== null) set.add(m[1]);
  const ann = /@rls-deny\s+([A-Za-z0-9_]+)/g;
  while ((m = ann.exec(src)) !== null) set.add(m[1]);
  return set;
}

/** Remove /* *​/ block comments and // line comments (leaving `://` in URLs intact). */
function stripComments(s) {
  return s
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

/**
 * Coverage rule: every RLS table must have a deny-test, unless explicitly exempted.
 * Returns { ok, uncovered: string[] }.
 */
export function evaluateRlsCoverage(rlsTables, coveredTables, { exempt = [] } = {}) {
  const ex = new Set(exempt);
  const uncovered = [...rlsTables].filter((t) => !coveredTables.has(t) && !ex.has(t)).sort();
  return { ok: uncovered.length === 0, uncovered };
}

function bareTable(name) {
  // strip schema qualifier and quotes: public."posts" -> posts
  return name.replace(/["'`]/g, '').split('.').pop();
}
