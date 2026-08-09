#!/usr/bin/env node
// gate-completeness — runs at instantiation AND as a required CI check (SYSTEM.md §5.4, grill-me Q9).
//
// Asserts the stack's required-gates manifest is fully installed: every declared reviewer has an agent
// file, the universal meta-check scripts exist, and every check that runs a repo script has that script
// present. This is what makes a missing gate (e.g. the ui-ux-reviewer that canon promised but never
// shipped) impossible to sail past — at birth AND on later drift.
//
// Usage: node checks/gate-completeness.mjs --stack stacks/<name>.md [--agents-dir .claude/agents]
// Exit 0 = complete; exit 1 = something declared-but-missing (prints it).

import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { parseManifest, evaluateCompleteness } from './lib/manifest.mjs';

const args = parseArgs(process.argv.slice(2));
const root = args.root ?? process.cwd();
const stackPath = args.stack;
if (!stackPath) fail('missing --stack <path to stack overlay .md>');

let stackMd;
try { stackMd = readFileSync(join(root, stackPath), 'utf8'); }
catch { fail(`cannot read stack overlay at ${stackPath}`); }

const agentsDir = args['agents-dir'] ?? (existsSync(join(root, '.claude/agents')) ? '.claude/agents' : 'agents');
const installedAgents = existsSync(join(root, agentsDir))
  ? readdirSync(join(root, agentsDir)).filter((f) => f.endsWith('.md')).map((f) => f.replace(/\.md$/, ''))
  : [];

const manifest = parseManifest(stackMd);
const { ok, missing } = evaluateCompleteness(manifest, {
  installedAgents,
  fileExists: (p) => existsSync(join(root, p)),
});

if (ok) {
  console.log(`✓ gate-completeness: ${stackPath} — ${manifest.reviewers.length} reviewer(s) + ${manifest.checks.length} check(s) all installed (agents: ${agentsDir}).`);
  process.exit(0);
}
console.error(`✗ gate-completeness: ${stackPath} — ${missing.length} gate(s) declared but not installed:`);
for (const m of missing) console.error(`  - [${m.kind}] ${m.name}: ${m.reason}`);
process.exit(1);

function parseArgs(argv) {
  const o = {};
  for (let i = 0; i < argv.length; i++) if (argv[i].startsWith('--')) o[argv[i].slice(2)] = (argv[i + 1] && !argv[i + 1].startsWith('--')) ? argv[++i] : true;
  return o;
}
function fail(msg) { console.error(`✗ gate-completeness: ${msg}`); process.exit(1); }
