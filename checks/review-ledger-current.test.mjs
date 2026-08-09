// Fixture tests for the review-ledger-current core (SYSTEM.md §5.2 — test which outputs a fixture
// produces, not the parsing internals).  Run with: vitest run
import { describe, it, expect } from 'vitest';
import { parseTasks, parseReviewLedger, evaluateLedgerCurrency } from './lib/tracker.mjs';

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

  // F-018: DONE is frozen history. Gating it made the check unusable past the first phase --
  // every completed task went stale on the next commit anywhere in the repo.
  it('does NOT gate a DONE task whose ✅ is at an older SHA (frozen history)', () => {
    const r = evalFixture(tracker({ status: 'DONE', cells: [`✅ ${OLD}`, `✅ ${OLD}`, `✅ ${OLD}`] }));
    expect(r.ok).toBe(true);
    expect(r.failures).toEqual([]);
  });

  it('fails a ✅ with no SHA (cannot prove currency)', () => {
    const r = evalFixture(tracker({ status: 'REVIEWED', cells: ['✅', `✅ ${HEAD}`, `✅ ${HEAD}`] }));
    expect(r.ok).toBe(false);
    expect(r.failures[0].reason).toMatch(/no commit SHA/);
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
