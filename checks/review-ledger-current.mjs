#!/usr/bin/env node
// review-ledger-current — a required CI check (SYSTEM.md §5.4, grill-me Q7).
//
// Makes the LLM reviewer gate falsifiable WITHOUT running an LLM in CI: it does not judge review
// quality, it enforces that a review of the CURRENT code exists. For every task at REVIEWED/DONE,
// each mandatory reviewer must be ✅ and every ✅ must reference the current code tip. A ✅ at an
// older SHA means the code changed after the review → stale → fail (re-review).
//
// Usage:  node checks/review-ledger-current.mjs [--tracker docs/IMPLEMENTATION.md] [--code-head <sha>]
// Exit 0 = clean; exit 1 = one or more stale/missing reviews (prints them).

import { readFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { parseTasks, parseReviewLedger, evaluateLedgerCurrency } from './lib/tracker.mjs';

const args = parseArgs(process.argv.slice(2));
const trackerPath = args.tracker ?? 'docs/IMPLEMENTATION.md';

let md;
try { md = readFileSync(trackerPath, 'utf8'); }
catch { fail(`cannot read tracker at ${trackerPath}`); }

// The "code tip": latest commit touching anything OUTSIDE docs/ (tracker/plans/log/learning-notes are
// process artifacts — a docs-only commit must not invalidate a code review). Override with --code-head.
const overridden = args['code-head'] && args['code-head'] !== true ? args['code-head'] : null;
const codeHead = overridden ?? gitCodeHead();

// Fail CLOSED (grill-me Q7): if we cannot determine the code tip — not a git repo, a shallow clone
// whose tip is docs-only, a brand-new repo, or any git error — we CANNOT prove reviews are current,
// so the gate must not pass. (The GH Actions template checks out with fetch-depth: 0 for this reason.)
if (!codeHead) fail('cannot determine the current code tip (not a git repo / shallow clone / no non-docs commit). Failing closed — check out full history, or pass --code-head <sha>.');

const tasks = parseTasks(md);
const ledger = parseReviewLedger(md);
const { ok, failures } = evaluateLedgerCurrency(tasks, ledger, codeHead);

if (ok) {
  console.log(`✓ review-ledger-current: all REVIEWED/DONE tasks reviewed at current code tip ${codeHead ?? '(none)'}.`);
  process.exit(0);
}
console.error(`✗ review-ledger-current: ${failures.length} stale/missing review(s) (code tip ${codeHead ?? '(none)'}):`);
for (const f of failures) console.error(`  - ${f.task} · ${f.reviewer}: ${f.reason}`);
process.exit(1);

function gitCodeHead() {
  try {
    const out = execSync(`git log -1 --format=%h -- . ':(exclude)docs/'`, { encoding: 'utf8' }).trim();
    return out || null;
  } catch { return null; }
}
function parseArgs(argv) {
  const o = {};
  for (let i = 0; i < argv.length; i++) if (argv[i].startsWith('--')) o[argv[i].slice(2)] = (argv[i + 1] && !argv[i + 1].startsWith('--')) ? argv[++i] : true;
  return o;
}
function fail(msg) { console.error(`✗ review-ledger-current: ${msg}`); process.exit(1); }
