// Pure parsers + evaluators over the Implementation Tracker (docs/IMPLEMENTATION.md).
// No I/O, no git, no process — so the CI wrappers stay thin and this core is fixture-testable
// (SYSTEM.md §5.2 deep-module principle: test which outputs a fixture produces, not the parsing).

const STATUS_ENUM = ['BACKLOG', 'PLANNED', 'IN_PROGRESS', 'BUILT', 'REVIEWED', 'DONE'];
const STATUS_RANK = Object.fromEntries(STATUS_ENUM.map((s, i) => [s, i]));

/**
 * Parse the "## Tasks" section into [{ id, status }].
 * A task starts at `**T-NNN**`; its status is the FIRST back-ticked STATUS_ENUM token in the task
 * block (the header carries it). Requiring back-ticks avoids prose collisions ("the BUILT-vs-DONE
 * bug"); taking the first avoids acceptance-line mentions ("advance to `REVIEWED`") masking it.
 */
export function parseTasks(md) {
  const section = sliceSection(md, 'Tasks') ?? md; // scope to the Tasks section, not the whole doc
  const lines = section.split('\n');
  const tasks = [];
  let current = null, block = [];
  const flush = () => {
    if (current) { current.status = statusFromBlock(block.join('\n')); tasks.push(current); }
    current = null; block = [];
  };
  for (const line of lines) {
    // F-035: anchor to the START of a checkbox list item. Matching `**T-NNN**` anywhere made a bold
    // cross-reference in prose ("depends on **T-001** landing first") open a phantom task block with a
    // null status — harmless while unknown statuses were ignored, but a hard gate failure once F-025
    // made them fail closed. The header must BE a task line, not merely mention one.
    const idMatch = line.match(/^\s*-\s*\[[ xX]\]\s*\*\*T-(\d+[a-z]?)\*\*/);
    if (idMatch) { flush(); current = { id: `T-${idMatch[1]}`, status: null }; }
    if (current) block.push(line);
  }
  flush();
  return tasks;
}

function statusFromBlock(text) {
  // 1) prefer a back-ticked status anywhere in the block (the header carries it; avoids prose).
  for (const m of text.matchAll(/`([A-Z_]+)`/g)) if (m[1] in STATUS_RANK) return m[1];
  // 2) F-011 fallback: no back-ticks → scan the HEADER line only (the `**T-NNN**` line, not the
  //    acceptance continuations) for a bare status word, taking the most-advanced if several appear
  //    (so "BUILT-vs-DONE … DONE" resolves to DONE). Recovers un-back-ticked statuses without
  //    reopening the acceptance-line collision F-004 closed.
  const header = text.split('\n').find((l) => /\*\*T-\d/.test(l)) ?? '';
  let best = null;
  for (const s of STATUS_ENUM) if (new RegExp(`\\b${s}\\b`).test(header) && (best === null || STATUS_RANK[s] > STATUS_RANK[best])) best = s;
  if (best) return best;
  // 3) F-025: BLOCKED is an orthogonal FLAG, not a lifecycle status (SYSTEM.md §3.1) — normally a task
  //    reads e.g. `IN_PROGRESS` `BLOCKED`, and the passes above already found the real status. A
  //    BLOCKED-ONLY task would otherwise parse to null, which evaluateLedgerCurrency now fails closed.
  //    Recognize it so it resolves to a known, deliberately ungated status instead.
  if (/`BLOCKED`/.test(text)) return 'BLOCKED';
  return null;
}

/**
 * Parse the "## Review ledger" markdown table into
 *   { reviewers, rows: [{ task, cells: { <reviewer>: { mark, sha } } }] }
 * mark ∈ 'pass' | 'fail' | 'na' | 'pending'. sha is the full short SHA a ✅ references (or null).
 * Reviewer columns = every header column after "Task", dropping a trailing "notes" column ONLY when
 * it is literally named notes (a ledger without a notes column keeps all its reviewer columns).
 */
export function parseReviewLedger(md) {
  const section = sliceSection(md, 'Review ledger');
  if (!section) return { reviewers: [], rows: [] };
  const rows = section.split('\n').filter((l) => l.trim().startsWith('|'));
  if (rows.length < 2) return { reviewers: [], rows: [] };
  const header = splitRow(rows[0]).map((h) => h.trim());
  const lastIsNotes = header[header.length - 1].toLowerCase() === 'notes';
  const reviewers = header.slice(1, lastIsNotes ? header.length - 1 : header.length).filter(Boolean);
  const out = [];
  for (const r of rows.slice(2)) { // skip header + separator
    const cols = splitRow(r);
    const task = cols[0].trim().replace(/\*\*/g, '');
    if (!/^T-/.test(task)) continue; // skip grouped/non-task rows
    const cells = {};
    reviewers.forEach((rev, i) => { cells[rev] = parseCell(cols[i + 1] ?? ''); });
    out.push({ task, cells });
  }
  return { reviewers, rows: out };
}

function parseCell(raw) {
  const v = raw.trim();
  if (v.includes('✅')) {
    const sha = (v.match(/[0-9a-f]{7,40}/i) || [null])[0];
    return { mark: 'pass', sha: sha ? sha.toLowerCase() : null };
  }
  if (v.includes('⛔')) return { mark: 'fail', sha: null };
  if (/^n\/?a$/i.test(v)) return { mark: 'na', sha: null };
  return { mark: 'pending', sha: null };
}

/**
 * Core rule (SYSTEM.md §5.4, grill-me Q7). For every task at REVIEWED or DONE:
 *   - each MANDATORY reviewer must be PRESENT in the ledger and `pass` (never absent/na/pending/fail),
 *   - and for REVIEWED ONLY (F-018/F-024), every applicable reviewer's ✅ must still be current.
 *     DONE is frozen history: exempt from currency, never from the verdict.
 *
 * Currency is measured one of two ways (F-043):
 *   - PER TASK, when `opts.staleAt` is supplied: has anything touched *this task's own files* since
 *     its ✅? That is the question the check means to ask, and the only one that survives a branch
 *     carrying several tasks — see the CLI, which derives file ownership from git.
 *   - REPO-WIDE fallback, comparing against `codeHead` by SHA prefix (git may abbreviate %h to more
 *     than 7 chars in larger repos). Stricter, and what runs when ownership cannot be established.
 * A task whose status does not parse to a known value fails CLOSED (F-025) — an unrecognized status
 * must never be a way to make a stale review disappear.
 * Reviewer-name matching is case-insensitive. Returns { ok, failures: [{ task, reviewer, reason }] }.
 * NOTE: a null `codeHead` is handled fail-CLOSED by the CLI (review-ledger-current.mjs), not here.
 */
export function evaluateLedgerCurrency(tasks, ledger, codeHead, opts = {}) {
  const mandatory = (opts.mandatory ?? ['security-auditor', 'logic-reviewer']).map((s) => s.toLowerCase());
  // Two independent rules, deliberately scoped differently (F-018 + F-019).
  //
  // VERDICT applies to REVIEWED *and* DONE: a ledger row must exist, every mandatory reviewer must be
  // present as a column, and every applicable cell must be a ✅ carrying some SHA. None of that
  // depends on the code tip — a DONE task holding a ⛔, a pending cell, or no row at all is a broken
  // invariant at any commit.
  //
  // CURRENCY (has the reviewed code changed since) applies to REVIEWED only. REVIEWED means
  // "passed review, awaiting merge", so its ✅ must reflect the code about to merge — the stale-review
  // case this check exists to catch. DONE means "merged/shipped": frozen history that later unrelated
  // work must not retroactively invalidate. (F-018: gating DONE on currency made the check unusable
  // past a project's first phase — every completed task's ✅ expired on the next commit anywhere, so N
  // done tasks meant N re-reviews per commit. F-019: but dropping DONE from the gate *entirely* went
  // too far — it left canon's "never DONE until REVIEWED" with no mechanical enforcement at all, and
  // created a one-word bypass, since a REVIEWED task failing on a stale review could be cleared by
  // simply advancing it to DONE.)
  //
  // F-043: comparing against a repo-wide tip made currency compound across a multi-task branch —
  // Phase 1 lands five tasks on one branch, so T-006's first commit invalidated T-005's ✅ despite
  // changing nothing T-005 owns, and once T-006 was REVIEWED, T-007 would have invalidated both.
  // That is F-018's failure shape one layer over, and it has the same root cause both times: a
  // moving repo-wide tip standing in for per-task provenance. `opts.staleAt(taskId, sha)` supplies
  // the real answer (null = still current, otherwise the SHA that touched this task's files after
  // the review). Injected, not computed here, so this core stays free of git — the CLI owns I/O.
  const staleAt = typeof opts.staleAt === 'function' ? opts.staleAt : null;
  const gated = new Set(['REVIEWED', 'DONE']);
  const currencyGated = new Set(['REVIEWED']);
  const byTask = Object.fromEntries(ledger.rows.map((r) => [r.task, r.cells]));
  const head = codeHead ? String(codeHead).toLowerCase() : null;
  // F-025: fail CLOSED on a status we cannot recognize. `gated` and this set are exact uppercase
  // matches, so without this a stale-review failure disappears the moment the status is lowercased,
  // misspelled, or deleted — the cheapest possible way to turn the gate green. If we cannot tell
  // whether a review is required, we must not assume it isn't.
  // F-036: derived, not hand-listed — a status added to STATUS_ENUM without editing a literal here
  // would hard-fail every task at that status. 'BLOCKED' is appended because it is an orthogonal flag
  // that lives outside the lifecycle enum (SYSTEM.md §3.1).
  const knownUngated = new Set([...STATUS_ENUM.filter((s) => !gated.has(s)), 'BLOCKED']);
  const failures = [];
  for (const t of tasks) {
    if (!gated.has(t.status) && !knownUngated.has(t.status)) {
      failures.push({
        task: t.id,
        reviewer: '(status)',
        reason: `unrecognized task status ${t.status === null ? '(missing or unparseable)' : `"${t.status}"`} — cannot determine whether a review is required; failing closed`,
      });
      continue;
    }
    if (!gated.has(t.status)) continue;
    const cells = byTask[t.id];
    if (!cells) { failures.push({ task: t.id, reviewer: '(ledger)', reason: `no Review-ledger row for a ${t.status} task` }); continue; }
    const present = {};
    for (const [k, v] of Object.entries(cells)) present[k.toLowerCase()] = { name: k, cell: v };
    // mandatory reviewers must be PRESENT as columns (a dropped column can't silently exempt one)
    for (const m of mandatory) {
      if (!(m in present)) failures.push({ task: t.id, reviewer: m, reason: `mandatory reviewer not present in the Review ledger for a ${t.status} task` });
    }
    for (const { name, cell } of Object.values(present)) {
      const isMandatory = mandatory.includes(name.toLowerCase());
      if (cell.mark === 'na') { if (isMandatory) failures.push({ task: t.id, reviewer: name, reason: `mandatory reviewer marked n/a on a ${t.status} task` }); continue; }
      if (cell.mark !== 'pass') { failures.push({ task: t.id, reviewer: name, reason: `${cell.mark} on a ${t.status} task (must be ✅)` }); continue; }
      if (!cell.sha) { failures.push({ task: t.id, reviewer: name, reason: '✅ carries no commit SHA (cannot prove the review is current)' }); continue; }
      if (!currencyGated.has(t.status)) continue;
      if (staleAt) {
        // Per-task (F-043): only a commit touching THIS task's files makes its review stale.
        const offender = staleAt(t.id, cell.sha);
        if (offender) {
          failures.push({ task: t.id, reviewer: name, reason: `✅ at ${cell.sha} but ${offender} touched this task's files afterwards — code changed after review, re-review needed` });
        }
      } else if (head && !(cell.sha.startsWith(head) || head.startsWith(cell.sha))) {
        failures.push({ task: t.id, reviewer: name, reason: `✅ at ${cell.sha} but code tip is ${head} — code changed after review, re-review needed` });
      }
    }
  }
  return { ok: failures.length === 0, failures };
}

/**
 * Which task, if any, does a commit SUBJECT claim ownership of (F-043)? Pure, so the attribution
 * rule is fixture-testable while git stays in the CLI.
 *
 *   "T-006: features deep module"        -> 'T-006'
 *   "fix(T-005): correct the allowlist"  -> 'T-005'
 *   "review(T-005): record verdicts"     -> null   (process, not product — see below)
 *   "docs(review): T-001 REVIEWED"       -> null
 *   "chore: instantiate the Dev-System"  -> null
 *
 * Subject only, never the body: commit bodies routinely discuss other tasks, so body matching would
 * hand a task ownership of files it never touched.
 *
 * PROCESS_TYPES record process rather than product, and are excluded on evidence, not taste: a
 * `review(T-005):` commit in this repo's history also carried gate-tooling fixes, so attributing it
 * would have made T-005 "own" checks/lib/tracker.mjs — reintroducing the false-positive class F-043
 * exists to remove. It also matches the convention already documented in log.md: a review commit
 * must be docs/-only.
 */
export const PROCESS_TYPES = new Set(['review', 'docs']);

export function taskIdFromSubject(subject) {
  const m = String(subject ?? '').match(/^(?:([a-z]+)\()?(T-\d{3})\)?\s*:/i);
  if (!m) return null;
  if (m[1] && PROCESS_TYPES.has(m[1].toLowerCase())) return null;
  return m[2].toUpperCase();
}

// ---- small markdown helpers ----
/** Slice a section body by heading level: ends at the next heading of level <= the section's own.
 *  F-012: prefer an EXACT (trimmed, case-insensitive) heading match so a "## Subtasks" before
 *  "## Tasks" can't hijack the slice; fall back to a substring match only if no exact one exists
 *  (which still tolerates a trailing `<!-- comment -->` on the heading). */
export function sliceSection(md, heading) {
  const lines = md.split('\n');
  const want = heading.trim().toLowerCase();
  const headings = [];
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^(#{2,6})\s+(.*)$/);
    if (m) headings.push({ i, level: m[1].length, text: m[2].trim().toLowerCase() });
  }
  const hit = headings.find((h) => h.text === want) ?? headings.find((h) => h.text.includes(want));
  if (!hit) return null;
  let end = lines.length;
  for (let i = hit.i + 1; i < lines.length; i++) {
    const m = lines[i].match(/^(#{1,6})\s/);
    if (m && m[1].length <= hit.level) { end = i; break; }
  }
  return lines.slice(hit.i + 1, end).join('\n');
}
function splitRow(row) {
  return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|');
}
