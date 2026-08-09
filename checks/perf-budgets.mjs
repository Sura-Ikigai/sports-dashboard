#!/usr/bin/env node
// perf-budgets — a required CI check (SYSTEM.md §5.4, grill-me Q8). Performance is a deterministic
// budget, not a reviewer's vibe: it fails the merge when a measured metric exceeds its budget.
//
// Usage: node checks/perf-budgets.mjs --metrics <lighthouse.json|metrics.json> [--budgets checks/reference/perf-budgets.json]
//   --metrics accepts either a Lighthouse result (has `.audits`) or a flat { lcp, cls, tbt, ... } map.
// Exit 0 = within budget; exit 1 = a budget was exceeded (or a budgeted metric wasn't measured).

import { readFileSync } from 'node:fs';
import { evaluateBudgets, metricsFromLighthouse } from './lib/perf.mjs';

const args = parseArgs(process.argv.slice(2));
const budgetsPath = args.budgets ?? 'checks/reference/perf-budgets.json';
if (!args.metrics) fail('missing --metrics <lighthouse.json | flat metrics.json> (produce it from your Lighthouse/Playwright run)');

let budgets, raw;
try { budgets = JSON.parse(readFileSync(budgetsPath, 'utf8')); } catch { fail(`cannot read budgets at ${budgetsPath}`); }
// ignore the "_comment" documentation key, then refuse to run on an empty budget set (a no-op that
// always "passes" is worse than no check — it advertises a perf gate that enforces nothing).
delete budgets._comment;
if (Object.keys(budgets).length === 0) fail(`budgets file ${budgetsPath} declares no budgets — nothing would be enforced`);
try { raw = JSON.parse(readFileSync(args.metrics, 'utf8')); } catch { fail(`cannot read metrics at ${args.metrics}`); }

const metrics = raw && raw.audits ? metricsFromLighthouse(raw) : raw;
const { ok, violations } = evaluateBudgets(budgets, metrics);

if (ok) { console.log(`✓ perf-budgets: all ${Object.keys(budgets).length} budget(s) within limits.`); process.exit(0); }
console.error(`✗ perf-budgets: ${violations.length} budget violation(s):`);
for (const v of violations) console.error(`  - ${v.metric}: ${v.reason}`);
process.exit(1);

function parseArgs(argv) { const o = {}; for (let i = 0; i < argv.length; i++) if (argv[i].startsWith('--')) o[argv[i].slice(2)] = (argv[i + 1] && !argv[i + 1].startsWith('--')) ? argv[++i] : true; return o; }
function fail(m) { console.error(`✗ perf-budgets: ${m}`); process.exit(1); }
