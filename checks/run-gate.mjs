#!/usr/bin/env node
// run-gate — the host-agnostic gate runner (SYSTEM.md §6.3). Runs the deterministic half of the gate:
// the two universal meta-checks + every `checks:` entry in the stack manifest. It does NOT run the
// LLM reviewers (they are the local read-only lane; their ✅ is enforced by review-ledger-current).
//
// Host-agnostic on purpose: a GitHub Actions job, a git hook, or a human all invoke the same script,
// so "green" means the same thing everywhere. The GH Actions workflow (templates/ci/gate.yml) is one
// caller, not the definition of the gate.
//
// SECURITY: manifest `run:` commands are arbitrary trusted config (like package.json scripts) and are
// executed via a shell by design. The meta-checks we own are invoked WITHOUT a shell (execFileSync,
// argv array), and --stack is validated, so the runner adds no injection surface of its own.
//
// Usage:
//   node checks/run-gate.mjs --stack stacks/<name>.md          # full gate (in a project)
//   node checks/run-gate.mjs --stack stacks/<name>.md --only-meta   # meta-checks only (factory dogfood)
//   node checks/run-gate.mjs --stack stacks/<name>.md --skip a11y,perf   # advisory checks only
// Exit 0 = all blocking checks passed; exit 1 = at least one failed (prints a summary).

import { readFileSync } from 'node:fs';
import { execSync, execFileSync } from 'node:child_process';
import { parseManifest } from './lib/manifest.mjs';

const args = parseArgs(process.argv.slice(2));
const stack = args.stack;
if (!stack || stack === true) { console.error('run-gate: missing --stack <path to stack overlay .md>'); process.exit(1); }
if (!/^[\w./-]+\.md$/.test(stack) || stack.includes('..')) { console.error(`run-gate: refusing suspicious --stack path: ${stack}`); process.exit(1); }
const skip = new Set(String(args.skip === true ? '' : (args.skip ?? '')).split(',').map((s) => s.trim()).filter(Boolean));

const results = [];
// 1) universal meta-checks (always run, always blocking) — invoked via argv, NO shell
runArgv('gate-completeness', ['checks/gate-completeness.mjs', '--stack', stack], true);
runArgv('review-ledger-current', ['checks/review-ledger-current.mjs'], true);

// 2) the stack's declared checks (unless --only-meta)
if (!args['only-meta']) {
  const manifest = parseManifest(readFileSync(stack, 'utf8'));
  for (const c of manifest.checks) {
    const blocking = c.blocking !== false;
    if (skip.has(c.id)) {
      if (blocking) { console.error(`run-gate: refusing to --skip a blocking check: ${c.id}`); process.exit(1); }
      results.push({ id: c.id, status: 'skip', blocking });
      continue;
    }
    runShell(c.id, c.run, blocking); // manifest commands: trusted config, shell by design
  }
}

const failedBlocking = results.filter((r) => r.status === 'fail' && r.blocking);
console.log('\n── gate summary ──');
for (const r of results) console.log(`  ${icon(r.status)} ${r.id}${r.blocking ? '' : ' (advisory)'}`);
console.log(`──────────────────`);
if (failedBlocking.length) {
  console.error(`✗ gate FAILED — ${failedBlocking.length} blocking check(s): ${failedBlocking.map((r) => r.id).join(', ')}`);
  console.error(`  (reviewer ✅ currency is enforced by the review-ledger-current check above.)`);
  process.exit(1);
}
console.log('✓ gate PASSED (deterministic lane). Reviewers run locally; their ✅@SHA is checked above.');
process.exit(0);

function runArgv(id, argv, blocking) {
  process.stdout.write(`▶ ${id}: node ${argv.join(' ')}\n`);
  try { execFileSync('node', argv, { stdio: 'inherit' }); results.push({ id, status: 'pass', blocking }); }
  catch { results.push({ id, status: 'fail', blocking }); }
}
function runShell(id, cmd, blocking) {
  process.stdout.write(`▶ ${id}: ${cmd}\n`);
  try { execSync(cmd, { stdio: 'inherit' }); results.push({ id, status: 'pass', blocking }); }
  catch { results.push({ id, status: 'fail', blocking }); }
}
function icon(s) { return s === 'pass' ? '✓' : s === 'fail' ? '✗' : '–'; }
function parseArgs(argv) {
  const o = {};
  for (let i = 0; i < argv.length; i++) if (argv[i].startsWith('--')) o[argv[i].slice(2)] = (argv[i + 1] && !argv[i + 1].startsWith('--')) ? argv[++i] : true;
  return o;
}
