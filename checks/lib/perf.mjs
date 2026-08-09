// Pure perf-budget evaluator (SYSTEM.md §5.4 — perf is a deterministic budget CHECK, not a reviewer's
// opinion; grill-me Q8). No I/O — fixture-testable.

/**
 * Compare measured metrics against budgets (upper bounds; smaller-is-better).
 * budgets/metrics are flat maps, e.g. { lcp: 2500, cls: 0.1, tbt: 300, bundleKb: 250 }.
 * A metric that is unmeasured (undefined) is a violation — you cannot pass a budget you didn't measure.
 * Returns { ok, violations: [{ metric, budget, actual, reason }] }.
 */
export function evaluateBudgets(budgets, metrics) {
  const violations = [];
  for (const [metric, budget] of Object.entries(budgets)) {
    const actual = metrics[metric];
    // undefined/null/NaN/Infinity all mean "you cannot pass a budget you didn't measure"
    if (!Number.isFinite(actual)) { violations.push({ metric, budget, actual: actual ?? null, reason: 'not measured' }); continue; }
    if (actual > budget) violations.push({ metric, budget, actual, reason: `over budget (${actual} > ${budget})` });
  }
  return { ok: violations.length === 0, violations };
}

/** Normalize a Lighthouse result object (lhr) into the flat metric map evaluateBudgets expects. */
export function metricsFromLighthouse(lhr) {
  const a = (lhr && lhr.audits) || {};
  const n = (id) => (a[id] ? a[id].numericValue : undefined);
  return {
    lcp: n('largest-contentful-paint'),
    fcp: n('first-contentful-paint'),
    tbt: n('total-blocking-time'),
    cls: n('cumulative-layout-shift'),
    tti: n('interactive'),
  };
}
