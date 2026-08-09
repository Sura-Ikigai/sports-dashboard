#!/usr/bin/env node
// rls-coverage — a required CI check (SYSTEM.md §5.4, grill-me Q11). Fails when any RLS-enabled table
// lacks a two-JWT deny-test, so a newly added table cannot silently ship unguarded.
//
// Usage: node checks/rls-coverage.mjs [--migrations supabase/migrations] [--tests tests/rls] [--exempt a,b]
// Exit 0 = every RLS table has a deny-test; exit 1 = one or more uncovered (prints them).

import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { rlsTablesFromMigrations, coveredTablesFromTests, evaluateRlsCoverage } from './lib/rls.mjs';

const args = parseArgs(process.argv.slice(2));
const root = args.root ?? process.cwd();
const migrationsDir = args.migrations ?? 'supabase/migrations';
const testsDir = args.tests ?? 'tests/rls';
const exempt = (args.exempt ?? '').split(',').filter(Boolean);

const sql = readAll(join(root, migrationsDir), /\.sql$/);
const tests = readAll(join(root, testsDir), /\.(ts|mjs|js|tsx)$/);
if (sql === null) fail(`no migrations dir at ${migrationsDir} (pass --migrations)`);

const rlsTables = rlsTablesFromMigrations(sql);
const covered = coveredTablesFromTests(tests ?? '');
const { ok, uncovered } = evaluateRlsCoverage(rlsTables, covered, { exempt });

if (ok) { console.log(`✓ rls-coverage: all ${rlsTables.size} RLS table(s) have a deny-test${exempt.length ? ` (exempt: ${exempt.join(', ')})` : ''}.`); process.exit(0); }
console.error(`✗ rls-coverage: ${uncovered.length} RLS table(s) with no deny-test:`);
for (const t of uncovered) console.error(`  - ${t}  (add an rlsDeny('${t}', …) test under ${testsDir})`);
process.exit(1);

function readAll(dir, pattern) {
  if (!existsSync(dir) || !statSync(dir).isDirectory()) return null;
  let out = '';
  for (const name of readdirSync(dir, { recursive: true })) {
    const p = join(dir, name.toString());
    if (pattern.test(p) && existsSync(p) && statSync(p).isFile()) out += readFileSync(p, 'utf8') + '\n';
  }
  return out;
}
function parseArgs(argv) { const o = {}; for (let i = 0; i < argv.length; i++) if (argv[i].startsWith('--')) o[argv[i].slice(2)] = (argv[i + 1] && !argv[i + 1].startsWith('--')) ? argv[++i] : true; return o; }
function fail(m) { console.error(`✗ rls-coverage: ${m}`); process.exit(1); }
