#!/usr/bin/env node
// review-ledger-current — a required CI check (SYSTEM.md §5.4, grill-me Q7).
//
// Makes the LLM reviewer gate falsifiable WITHOUT running an LLM in CI: it does not judge review
// quality, it enforces that a review exists and, where it still can, that it covers the current code.
// For every task at REVIEWED or DONE, each mandatory reviewer must be present and ✅ with a SHA.
// Additionally, for REVIEWED only, that ✅ must still cover the code — if anything has changed since,
// the review is stale → fail (re-review). DONE is frozen history and is exempt from the currency
// comparison, but not from the verdict (F-018, F-019).
//
// "Has anything changed since" is asked PER TASK (F-043): has any commit touched *this task's own
// files* since its ✅? File ownership is derived from git by commit subject — see taskOwnedFiles
// below. The older repo-wide comparison (any non-docs commit anywhere invalidates every REVIEWED
// task) is still the fallback wherever ownership cannot be established, and `--repo-wide` forces it.
//
// Usage:  node checks/review-ledger-current.mjs [--tracker docs/IMPLEMENTATION.md]
//                                               [--code-head <sha>] [--repo-wide]
// Exit 0 = clean; exit 1 = one or more stale/missing reviews (prints them).

import { readFileSync } from 'node:fs';
import { execSync, execFileSync } from 'node:child_process';
import { parseTasks, parseReviewLedger, evaluateLedgerCurrency, taskIdFromSubject } from './lib/tracker.mjs';

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
// F-043: prefer per-task currency. `--repo-wide` forces the old behavior (and it is what runs
// automatically for any task whose file ownership cannot be established — see taskOwnedFiles).
const ownership = args['repo-wide'] ? null : taskOwnedFiles();
const { ok, failures } = evaluateLedgerCurrency(tasks, ledger, codeHead, {
  staleAt: ownership ? (taskId, sha) => staleAt(ownership, taskId, sha) : undefined,
});

if (ok) {
  const how = ownership ? "no commit has touched a REVIEWED task's own files since its ✅" : `every REVIEWED ✅ is at the code tip ${codeHead}`;
  console.log(`✓ review-ledger-current: ${how}; DONE tasks carry a complete ✅ verdict.`);
  process.exit(0);
}
console.error(`✗ review-ledger-current: ${failures.length} stale/missing review(s) (code tip ${codeHead ?? '(none)'}):`);
for (const f of failures) console.error(`  - ${f.task} · ${f.reviewer}: ${f.reason}`);
process.exit(1);

// --- per-task file ownership (F-043) -------------------------------------------------------------
//
// Which files does a task own? Derived from git, not declared in the tracker: a hand-maintained
// `files:` field would be a new bypass, since narrowing it narrows the gate.
//
// Attribution comes from the commit SUBJECT ("T-006: …", "fix(T-006): …"), and deliberately NOT the
// body: this project's commit bodies routinely discuss other tasks — the very commit that opened
// F-043 names T-005, T-006, T-007 and T-009 — so body matching would hand a task ownership of files
// it never touched.
//
// The attribution rule itself lives in lib/tracker.mjs as `taskIdFromSubject` (pure, fixture-tested,
// and where the reasoning for excluding process commits is written down). This file owns only the
// git I/O around it.
//
// Note what this does NOT depend on: later commits do not need to be attributed to anything. The
// task's own commits establish its file set; the staleness query then asks git whether ANY commit
// since the review touched those files, however it was labelled.
function git(args) {
  return execFileSync('git', args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
}

function taskOwnedFiles() {
  let log;
  try { log = git(['log', '--format=%H%x1f%s']); } catch { return null; }

  const shasByTask = new Map();
  for (const line of log.split('\n').filter(Boolean)) {
    const [sha, subject = ''] = line.split('\x1f');
    const id = taskIdFromSubject(subject);
    if (!id) continue;
    if (!shasByTask.has(id)) shasByTask.set(id, []);
    shasByTask.get(id).push(sha);
  }

  const filesByTask = new Map();
  for (const [id, shas] of shasByTask) {
    const files = new Set();
    for (const sha of shas) {
      let out;
      try { out = git(['show', '--name-only', '--format=', '--no-renames', sha]); } catch { continue; }
      // docs/ is excluded for the same reason gitCodeHead excludes it: the tracker, plans and log
      // are process artifacts, and a docs-only commit must not invalidate a code review.
      for (const f of out.split('\n').map((s) => s.trim()).filter(Boolean)) {
        if (!f.startsWith('docs/')) files.add(f);
      }
    }
    if (files.size) filesByTask.set(id, [...files]);
  }
  return filesByTask;
}

/** null = the review is still current; otherwise the short SHA that invalidated it. */
function staleAt(filesByTask, taskId, sha) {
  const files = filesByTask.get(taskId);
  if (!files) {
    // Fail CLOSED, to the stricter repo-wide rule. No attributable commits means we cannot say what
    // this task owns — which is not evidence that nothing changed. Reaches here for a task whose
    // commits predate the naming convention, or a docs-only task.
    return codeHead && !(sha.startsWith(codeHead) || codeHead.startsWith(sha)) ? `${codeHead} (repo-wide fallback: no commit subject claims ${taskId})` : null;
  }
  let out;
  try { out = git(['log', '--format=%h', `${sha}..HEAD`, '--', ...files]); }
  catch { return `(unresolvable revision ${sha} — failing closed)`; }
  return out.split('\n').filter(Boolean)[0] ?? null;
}

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
