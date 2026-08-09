// Fixture tests for the rls-coverage core.  Run with: vitest run
import { describe, it, expect } from 'vitest';
import { rlsTablesFromMigrations, coveredTablesFromTests, evaluateRlsCoverage } from './lib/rls.mjs';

const MIGRATIONS = `
create table posts (id uuid primary key);
alter table posts enable row level security;
ALTER TABLE public."reactions" ENABLE ROW LEVEL SECURITY;
alter table if exists group_members enable row level security;
create table audit_log (id uuid);  -- no RLS on purpose
`;

describe('rlsTablesFromMigrations', () => {
  it('finds every table with RLS enabled (schema-qualified/quoted/if-exists)', () => {
    const t = rlsTablesFromMigrations(MIGRATIONS);
    expect([...t].sort()).toEqual(['group_members', 'posts', 'reactions']);
    expect(t.has('audit_log')).toBe(false);
  });
});

describe('coveredTablesFromTests', () => {
  it('reads rlsDeny(...) calls and @rls-deny annotations', () => {
    const src = `rlsDeny('posts', async (as) => {}); \n // @rls-deny reactions \n rlsDeny("group_members", ...)`;
    expect([...coveredTablesFromTests(src)].sort()).toEqual(['group_members', 'posts', 'reactions']);
  });
});

describe('evaluateRlsCoverage', () => {
  const rls = rlsTablesFromMigrations(MIGRATIONS);
  it('passes when every RLS table has a deny-test', () => {
    const covered = coveredTablesFromTests(`rlsDeny('posts'); rlsDeny('reactions'); rlsDeny('group_members')`);
    expect(evaluateRlsCoverage(rls, covered).ok).toBe(true);
  });
  it('fails and names the uncovered table', () => {
    const covered = coveredTablesFromTests(`rlsDeny('posts'); rlsDeny('reactions')`); // group_members missing
    const r = evaluateRlsCoverage(rls, covered);
    expect(r.ok).toBe(false);
    expect(r.uncovered).toEqual(['group_members']);
  });
  it('honors an explicit exemption', () => {
    const covered = coveredTablesFromTests(`rlsDeny('posts'); rlsDeny('reactions')`);
    expect(evaluateRlsCoverage(rls, covered, { exempt: ['group_members'] }).ok).toBe(true);
  });

  it('F-006: a commented-out rlsDeny does NOT count as coverage, but @rls-deny does', () => {
    const cov = coveredTablesFromTests(`rlsDeny('posts', {});\n// rlsDeny('secrets', {});\n/* rlsDeny('audit', {}) */\n// @rls-deny reactions`);
    expect(cov.has('posts')).toBe(true);
    expect(cov.has('reactions')).toBe(true); // @rls-deny annotation is a deliberate comment form
    expect(cov.has('secrets')).toBe(false);  // commented-out call → unguarded
    expect(cov.has('audit')).toBe(false);
  });

  it('F-013: a skipped deny-test (it.skip / describe.skip) does not count as coverage', () => {
    const cov = coveredTablesFromTests(`rlsDeny('posts', {});\nit.skip('x', () => rlsDeny('secrets', {}));\ndescribe.skip('y', () => rlsDeny('audit', {}));`);
    expect(cov.has('posts')).toBe(true);
    expect(cov.has('secrets')).toBe(false);
    expect(cov.has('audit')).toBe(false);
  });
});
