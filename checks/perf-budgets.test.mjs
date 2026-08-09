// Fixture tests for the perf-budgets core.  Run with: vitest run
import { describe, it, expect } from 'vitest';
import { evaluateBudgets, metricsFromLighthouse } from './lib/perf.mjs';

const budgets = { lcp: 2500, cls: 0.1, tbt: 300 };

describe('evaluateBudgets', () => {
  it('passes when every metric is within budget', () => {
    expect(evaluateBudgets(budgets, { lcp: 2000, cls: 0.05, tbt: 150 }).ok).toBe(true);
  });
  it('flags a metric over budget', () => {
    const r = evaluateBudgets(budgets, { lcp: 3200, cls: 0.05, tbt: 150 });
    expect(r.ok).toBe(false);
    expect(r.violations).toContainEqual(expect.objectContaining({ metric: 'lcp', budget: 2500, actual: 3200 }));
  });
  it('treats an unmeasured budgeted metric as a violation', () => {
    const r = evaluateBudgets(budgets, { lcp: 2000, cls: 0.05 }); // tbt missing
    expect(r.ok).toBe(false);
    expect(r.violations).toContainEqual(expect.objectContaining({ metric: 'tbt', reason: 'not measured' }));
  });

  it('F-010: a non-finite (NaN/Infinity) metric is "not measured", not a silent pass', () => {
    const r = evaluateBudgets(budgets, { lcp: NaN, cls: 0.05, tbt: 150 });
    expect(r.ok).toBe(false);
    expect(r.violations).toContainEqual(expect.objectContaining({ metric: 'lcp', reason: 'not measured' }));
  });
});

describe('metricsFromLighthouse', () => {
  it('extracts numericValues from an lhr', () => {
    const lhr = { audits: { 'largest-contentful-paint': { numericValue: 2100 }, 'cumulative-layout-shift': { numericValue: 0.03 } } };
    const m = metricsFromLighthouse(lhr);
    expect(m.lcp).toBe(2100);
    expect(m.cls).toBe(0.03);
  });
});
