// Fixture tests for the gate-completeness core.  Run with: vitest run
import { describe, it, expect } from 'vitest';
import { parseManifest, evaluateCompleteness, repoScriptFromRun } from './lib/manifest.mjs';

const STACK = `# Stack overlay — test
## Required gates (manifest)
\`\`\`yaml
required-gates:
  reviewers:
    - security-auditor
    - logic-reviewer
    - ui-ux-reviewer
    # - not-installed-reviewer   # commented out — must be ignored
  checks:
    - { id: typecheck,    run: "tsc --noEmit",                 blocking: true }
    - { id: rls-coverage, run: "node checks/rls-coverage.mjs", blocking: true }
    - { id: a11y,         run: "playwright test tests/a11y",   blocking: true }
\`\`\`
`;

const META = ['checks/review-ledger-current.mjs', 'checks/gate-completeness.mjs'];
const allPresent = (extra = []) => (p) => [...META, 'checks/rls-coverage.mjs', 'tests/a11y', ...extra].includes(p);

describe('parseManifest', () => {
  it('reads reviewers (ignoring commented items) and checks', () => {
    const m = parseManifest(STACK);
    expect(m.reviewers).toEqual(['security-auditor', 'logic-reviewer', 'ui-ux-reviewer']);
    expect(m.checks.map((c) => c.id)).toEqual(['typecheck', 'rls-coverage', 'a11y']);
    expect(m.checks[0]).toMatchObject({ id: 'typecheck', run: 'tsc --noEmit', blocking: true });
  });
});

describe('repoScriptFromRun', () => {
  it('extracts repo scripts and ignores bare tools', () => {
    expect(repoScriptFromRun('node checks/rls-coverage.mjs')).toBe('checks/rls-coverage.mjs');
    expect(repoScriptFromRun('playwright test tests/a11y')).toBe('tests/a11y');
    expect(repoScriptFromRun('tsc --noEmit')).toBe(null);
    expect(repoScriptFromRun('eslint .')).toBe(null);
  });
});

describe('evaluateCompleteness', () => {
  const manifest = parseManifest(STACK);

  it('passes when every reviewer + script is installed', () => {
    const r = evaluateCompleteness(manifest, {
      installedAgents: ['security-auditor', 'logic-reviewer', 'ui-ux-reviewer'],
      fileExists: allPresent(),
    });
    expect(r.ok).toBe(true);
  });

  it('fails when a declared reviewer has no agent file (the ui-ux-reviewer bug)', () => {
    const r = evaluateCompleteness(manifest, {
      installedAgents: ['security-auditor', 'logic-reviewer'], // ui-ux-reviewer NOT installed
      fileExists: allPresent(),
    });
    expect(r.ok).toBe(false);
    expect(r.missing).toContainEqual(expect.objectContaining({ kind: 'reviewer', name: 'ui-ux-reviewer' }));
  });

  it('fails when a check references a script that is not present', () => {
    const r = evaluateCompleteness(manifest, {
      installedAgents: ['security-auditor', 'logic-reviewer', 'ui-ux-reviewer'],
      fileExists: (p) => META.includes(p) || p === 'tests/a11y', // rls-coverage.mjs missing
    });
    expect(r.ok).toBe(false);
    expect(r.missing).toContainEqual(expect.objectContaining({ kind: 'check', name: 'rls-coverage' }));
  });

  it('fails when a universal meta-check script is missing', () => {
    const r = evaluateCompleteness(manifest, {
      installedAgents: ['security-auditor', 'logic-reviewer', 'ui-ux-reviewer'],
      fileExists: (p) => p !== 'checks/gate-completeness.mjs' && allPresent()(p),
    });
    expect(r.ok).toBe(false);
    expect(r.missing).toContainEqual(expect.objectContaining({ kind: 'meta-check' }));
  });

  it('F-008: a manifest that does not DECLARE a mandatory reviewer fails', () => {
    const m = parseManifest("```yaml\nrequired-gates:\n  reviewers:\n    - security-auditor\n  checks:\n    - { id: t, run: \"tsc\" }\n```");
    const r = evaluateCompleteness(m, { installedAgents: ['security-auditor', 'logic-reviewer'], fileExists: () => true });
    expect(r.ok).toBe(false);
    expect(r.missing).toContainEqual(expect.objectContaining({ name: 'logic-reviewer' }));
  });
});

describe('repoScriptFromRun — F-009 flags before the path', () => {
  it('skips leading flags / env tokens and broadens the node extension', () => {
    expect(repoScriptFromRun('pytest -q tests/authz')).toBe('tests/authz');
    expect(repoScriptFromRun('playwright test --project=ci tests/a11y')).toBe('tests/a11y');
    expect(repoScriptFromRun('vitest run --coverage tests/rls')).toBe('tests/rls');
    expect(repoScriptFromRun('node checks/x.js')).toBe('checks/x.js');
  });
  it('F-014: a space-separated flag value is not mistaken for the path', () => {
    expect(repoScriptFromRun('pytest -k "test_x" tests/authz')).toBe('tests/authz');
  });
});
