// Fixture tests for the review-ledger-current core (SYSTEM.md §5.2 — test which outputs a fixture
// produces, not the parsing internals).  Run with: vitest run
import { describe, it, expect } from 'vitest';
import { parseTasks, parseReviewLedger, evaluateLedgerCurrency, taskIdFromSubject } from './lib/tracker.mjs';

const HEAD = 'a1b2c3d';
const OLD = '9999999';

function tracker({ status, cells }) {
  return `## Tasks
### Epic
- [ ] **T-001** Do the thing — \`${status}\` — owner: \`backend-engineer\`
      - acceptance: it works

## Review ledger

| Task  | security-auditor | logic-reviewer | ui-ux-reviewer | notes |
|-------|------------------|----------------|----------------|-------|
| T-001 | ${cells[0]} | ${cells[1]} | ${cells[2]} | |

## Findings
- none
`;
}

function evalFixture(md, codeHead = HEAD) {
  return evaluateLedgerCurrency(parseTasks(md), parseReviewLedger(md), codeHead);
}

describe('review-ledger-current', () => {
  it('passes when every applicable reviewer is ✅ at the current code tip', () => {
    const r = evalFixture(tracker({ status: 'REVIEWED', cells: [`✅ ${HEAD}`, `✅ ${HEAD}`, `✅ ${HEAD}`] }));
    expect(r.ok).toBe(true);
    expect(r.failures).toEqual([]);
  });

  it('fails a ✅ that references a stale SHA (code changed after review)', () => {
    const r = evalFixture(tracker({ status: 'REVIEWED', cells: [`✅ ${HEAD}`, `✅ ${OLD}`, `✅ ${HEAD}`] }));
    expect(r.ok).toBe(false);
    expect(r.failures).toHaveLength(1);
    expect(r.failures[0]).toMatchObject({ task: 'T-001', reviewer: 'logic-reviewer' });
    expect(r.failures[0].reason).toMatch(/code changed after review/);
  });

  it('fails a mandatory reviewer left pending on a REVIEWED task', () => {
    const r = evalFixture(tracker({ status: 'REVIEWED', cells: [`✅ ${HEAD}`, 'pending', `✅ ${HEAD}`] }));
    expect(r.ok).toBe(false);
    expect(r.failures[0]).toMatchObject({ task: 'T-001', reviewer: 'logic-reviewer' });
  });

  it('fails a mandatory reviewer marked n/a', () => {
    const r = evalFixture(tracker({ status: 'REVIEWED', cells: ['n/a', `✅ ${HEAD}`, `✅ ${HEAD}`] }));
    expect(r.ok).toBe(false);
    expect(r.failures[0]).toMatchObject({ task: 'T-001', reviewer: 'security-auditor' });
    expect(r.failures[0].reason).toMatch(/marked n\/a/);
  });

  it('allows a non-mandatory reviewer (ui-ux) to be n/a', () => {
    const r = evalFixture(tracker({ status: 'REVIEWED', cells: [`✅ ${HEAD}`, `✅ ${HEAD}`, 'n/a'] }));
    expect(r.ok).toBe(true);
  });

  it('ignores tasks not yet at REVIEWED (BUILT can have pending reviews)', () => {
    const r = evalFixture(tracker({ status: 'BUILT', cells: ['pending', 'pending', 'pending'] }));
    expect(r.ok).toBe(true);
  });

  // F-018: CURRENCY is not checked for DONE -- it is frozen history. Gating it made the check
  // unusable past the first phase: every completed task went stale on the next commit anywhere.
  it('does NOT flag a DONE task whose ✅ is at an older SHA (frozen history)', () => {
    const r = evalFixture(tracker({ status: 'DONE', cells: [`✅ ${OLD}`, `✅ ${OLD}`, `✅ ${OLD}`] }));
    expect(r.ok).toBe(true);
    expect(r.failures).toEqual([]);
  });

  // F-019: but VERDICT is still enforced for DONE. Dropping DONE from the gate entirely left
  // "never DONE until REVIEWED" unenforced, and made `DONE` a one-word escape from a stale-review
  // failure. These three must fail regardless of SHA currency.
  it('fails a DONE task with a mandatory reviewer still pending', () => {
    const r = evalFixture(tracker({ status: 'DONE', cells: ['pending', `✅ ${OLD}`, `✅ ${OLD}`] }));
    expect(r.ok).toBe(false);
    expect(r.failures[0]).toMatchObject({ task: 'T-001', reviewer: 'security-auditor' });
  });

  it('fails a DONE task carrying a ⛔ verdict', () => {
    const r = evalFixture(tracker({ status: 'DONE', cells: [`✅ ${OLD}`, '⛔ authz hole', `✅ ${OLD}`] }));
    expect(r.ok).toBe(false);
    expect(r.failures[0]).toMatchObject({ task: 'T-001', reviewer: 'logic-reviewer' });
  });

  it('fails a DONE task with no Review-ledger row at all', () => {
    const md = `## Tasks\n- [ ] **T-77** shipped somehow — \`DONE\`\n\n## Review ledger\n\n| Task | security-auditor | logic-reviewer | notes |\n|------|---|---|---|\n\n## Findings\n`;
    const r = evalFixture(md);
    expect(r.ok).toBe(false);
    expect(r.failures[0]).toMatchObject({ task: 'T-77', reviewer: '(ledger)' });
  });

  it('fails a DONE task whose ✅ carries no SHA at all', () => {
    const r = evalFixture(tracker({ status: 'DONE', cells: ['✅', `✅ ${OLD}`, `✅ ${OLD}`] }));
    expect(r.ok).toBe(false);
    expect(r.failures[0].reason).toMatch(/no commit SHA/);
  });

  it('fails a ✅ with no SHA (cannot prove currency)', () => {
    const r = evalFixture(tracker({ status: 'REVIEWED', cells: ['✅', `✅ ${HEAD}`, `✅ ${HEAD}`] }));
    expect(r.ok).toBe(false);
    expect(r.failures[0].reason).toMatch(/no commit SHA/);
  });

  // F-035: a bold cross-reference in prose must not open a phantom task block. Before the anchor fix
  // this produced {id:'T-001', status:null}, which F-025's fail-closed rule turned into a hard gate
  // failure on a perfectly valid tracker.
  it('does not treat a bold **T-NNN** cross-reference in prose as a task header', () => {
    const md = `## Tasks
- [ ] **T-042** real task — \`BUILT\` — owner: \`backend-engineer\`
      - acceptance: works
      - blocked on: **T-001** landing first

## Review ledger

| Task | security-auditor | logic-reviewer | notes |
|------|---|---|---|

## Findings
`;
    const ids = parseTasks(md).map((t) => t.id);
    expect(ids).toEqual(['T-042']);
    expect(evaluateLedgerCurrency(parseTasks(md), parseReviewLedger(md), 'a1b2c3d').ok).toBe(true);
  });

  // F-025: an unrecognized status must not be a way to make a stale review vanish.
  it('fails CLOSED on a status that does not parse to a known value', () => {
    for (const bad of ['reviewed', 'Reviewed', 'SHIPPED']) {
      const r = evalFixture(tracker({ status: bad, cells: [`✅ ${OLD}`, `✅ ${OLD}`, `✅ ${OLD}`] }));
      expect(r.ok, `status \`${bad}\` should fail closed`).toBe(false);
      expect(r.failures[0].reason).toMatch(/unrecognized task status/);
    }
  });

  it('treats a BLOCKED-only task as known and ungated, not as unparseable', () => {
    const r = evalFixture(tracker({ status: 'BLOCKED', cells: ['pending', 'pending', 'pending'] }));
    expect(r.ok).toBe(true);
  });

  it('still reads the real lifecycle status when BLOCKED is also present', () => {
    const md = tracker({ status: 'REVIEWED', cells: [`✅ ${OLD}`, `✅ ${OLD}`, 'n/a'] })
      .replace('`REVIEWED`', '`REVIEWED` `BLOCKED`');
    expect(parseTasks(md)[0].status).toBe('REVIEWED');
    expect(evalFixture(md).ok).toBe(false); // stale SHA on a REVIEWED task still caught
  });

  it('parseTasks reads the status enum off the task block', () => {
    const tasks = parseTasks(tracker({ status: 'IN_PROGRESS', cells: ['pending', 'pending', 'pending'] }));
    expect(tasks).toEqual([{ id: 'T-001', status: 'IN_PROGRESS' }]);
  });
});

// Regressions for reviewer findings — the happy-path suite above missed these.
describe('review-ledger-current — reviewer-finding regressions', () => {
  const led = (header, row) => `## Tasks\n- [ ] **T-1** x — \`REVIEWED\`\n## Review ledger\n| ${header} |\n|${'--|'.repeat(header.split('|').length)}\n| ${row} |\n## Findings`;
  const ev = (md, head = HEAD) => evaluateLedgerCurrency(parseTasks(md), parseReviewLedger(md), head);

  it('F-001: a dropped mandatory-reviewer column fails (not silently exempt)', () => {
    const r = ev(led('Task | security-auditor | notes', `T-1 | ✅ ${HEAD} | `));
    expect(r.ok).toBe(false);
    expect(r.failures.some((f) => f.reviewer === 'logic-reviewer')).toBe(true);
  });
  it('F-003: a ledger with no notes column keeps its last reviewer', () => {
    expect(parseReviewLedger(led('Task | security-auditor | logic-reviewer', `T-1 | ✅ ${HEAD} | pending`)).reviewers)
      .toEqual(['security-auditor', 'logic-reviewer']);
  });
  it('F-004: back-ticked status wins over prose/acceptance mentions', () => {
    const md = `## Tasks\n### Epic\n- [ ] **T-50** Fix the BUILT-vs-DONE bug — \`DONE\`\n      - acceptance: advance to \`REVIEWED\`\n## Decisions\n`;
    expect(parseTasks(md)[0].status).toBe('DONE');
  });
  it('F-005: SHA compared by prefix (git may abbreviate %h to >7 chars)', () => {
    expect(ev(led('Task | security-auditor | logic-reviewer | notes', `T-1 | ✅ a1b2c3d99 | ✅ a1b2c3d99 |`), 'a1b2c3d99').ok).toBe(true);
    expect(ev(led('Task | security-auditor | logic-reviewer | notes', `T-1 | ✅ a1b2c3d99 | ✅ a1b2c3d99 |`), 'a1b2c3d').ok).toBe(true);
  });
  it('F-011: an un-back-ticked status on the header is still detected (not a gate bypass)', () => {
    expect(parseTasks('## Tasks\n- [ ] **T-9** do it — DONE — owner: x\n## Decisions')[0].status).toBe('DONE');
    // but a prose status word is not enough to promote when back-ticked wins
    expect(parseTasks('## Tasks\n- [ ] **T-50** the BUILT-vs-DONE bug — `DONE`\n## Decisions')[0].status).toBe('DONE');
  });
  it('F-012: a decoy "## Subtasks" heading before "## Tasks" does not hijack the slice', () => {
    const md = '## Subtasks legend\n- [ ] **T-7** decoy — `BACKLOG`\n## Tasks\n- [ ] **T-8** real — `DONE`\n## Decisions';
    expect(parseTasks(md).find((t) => t.id === 'T-8')?.status).toBe('DONE');
  });
});

// F-043: currency asked PER TASK — has anything touched *this task's* files since its ✅ — instead
// of against a repo-wide moving tip. The evaluator stays pure: `staleAt` is injected, so these
// fixtures describe git's answer without running git.
describe('review-ledger-current — F-043 per-task currency', () => {
  const two = (s1, sha1, s2, sha2) =>
    `## Tasks
- [ ] **T-005** loader — \`${s1}\` — owner: \`backend-engineer\`
- [ ] **T-006** features — \`${s2}\` — owner: \`backend-engineer\`

## Review ledger

| Task  | security-auditor | logic-reviewer | notes |
|-------|------------------|----------------|-------|
| T-005 | ✅ ${sha1} | ✅ ${sha1} | |
| T-006 | ✅ ${sha2} | ✅ ${sha2} | |

## Findings
`;
  const ev = (md, staleAt) =>
    evaluateLedgerCurrency(parseTasks(md), parseReviewLedger(md), HEAD, { staleAt });

  it('a commit touching only ANOTHER task’s files leaves this review current', () => {
    const md = two('REVIEWED', OLD, 'REVIEWED', HEAD);
    // git says: nothing has touched T-005's files since OLD; T-006 is at the tip.
    const r = ev(md, () => null);
    expect(r.ok).toBe(true);
    // ...whereas the repo-wide rule fails T-005 purely because the tip moved. This is F-043.
    const repoWide = evaluateLedgerCurrency(parseTasks(md), parseReviewLedger(md), HEAD);
    expect(repoWide.ok).toBe(false);
    expect(repoWide.failures.every((f) => f.task === 'T-005')).toBe(true);
  });

  it('a commit touching THIS task’s files does make the review stale', () => {
    const r = ev(two('REVIEWED', OLD, 'BUILT', HEAD), (taskId) => (taskId === 'T-005' ? HEAD : null));
    expect(r.ok).toBe(false);
    expect(r.failures).toHaveLength(2); // both reviewers on T-005
    expect(r.failures[0].reason).toMatch(/touched this task's files afterwards/);
    expect(r.failures[0].reason).toMatch(/code changed after review/);
  });

  it('does not compound: two REVIEWED tasks stay green when neither is touched', () => {
    const r = ev(two('REVIEWED', OLD, 'REVIEWED', OLD), () => null);
    expect(r.ok).toBe(true);
  });

  it('still exempts DONE from currency, and still enforces its verdict', () => {
    expect(ev(two('DONE', OLD, 'BUILT', HEAD), () => HEAD).ok).toBe(true);
    const noRow = `## Tasks\n- [ ] **T-005** loader — \`DONE\`\n## Review ledger\n\n| Task | security-auditor | logic-reviewer |\n|--|--|--|\n| T-009 | ✅ ${HEAD} | ✅ ${HEAD} |\n## Findings`;
    expect(ev(noRow, () => null).ok).toBe(false);
  });

  it('falls back to the repo-wide rule when staleAt is not supplied', () => {
    const r = evaluateLedgerCurrency(parseTasks(two('REVIEWED', OLD, 'BUILT', HEAD)), parseReviewLedger(two('REVIEWED', OLD, 'BUILT', HEAD)), HEAD);
    expect(r.ok).toBe(false);
    expect(r.failures[0].reason).toMatch(/code tip is/);
  });
});

// The attribution rule that decides which files a task owns. Pure, so it is testable without git.
describe('taskIdFromSubject — commit-subject attribution (F-043)', () => {
  it('claims a task from a build or fix subject', () => {
    expect(taskIdFromSubject('T-006: features deep module')).toBe('T-006');
    expect(taskIdFromSubject('fix(T-005): correct the redirect allowlist')).toBe('T-005');
    expect(taskIdFromSubject('feat(T-012): add the thing')).toBe('T-012');
  });

  it('does NOT claim from process commits, which record process rather than product', () => {
    // Evidenced by this repo: `review(T-005): record gate verdicts; fix F-035/F-036` also touched
    // checks/lib/tracker.mjs, so attributing it would make T-005 own the gate's own source.
    expect(taskIdFromSubject('review(T-005): record gate verdicts (security ✅, logic ⛔)')).toBeNull();
    expect(taskIdFromSubject('docs(T-005): tidy the tracker')).toBeNull();
    expect(taskIdFromSubject('docs(review): T-001 REVIEWED @ 763101e')).toBeNull();
  });

  it('does not claim from a subject that merely mentions a task', () => {
    expect(taskIdFromSubject('chore: prepare for T-006')).toBeNull();
    expect(taskIdFromSubject('fix(canon): fail closed on unrecognized status (F-025)')).toBeNull();
    expect(taskIdFromSubject('Merge phase: Dev-System instantiation + PLAN-v1')).toBeNull();
  });

  it('is robust to empty, missing and non-string input', () => {
    expect(taskIdFromSubject('')).toBeNull();
    expect(taskIdFromSubject(undefined)).toBeNull();
    expect(taskIdFromSubject(null)).toBeNull();
  });
});
