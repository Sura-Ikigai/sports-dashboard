/**
 * Unit tests for the browser scorer (T-033).
 *
 * `contract.test.ts` proves this agrees with Python on the arithmetic. This file covers what the
 * contract cannot: what happens at the boundary, where a model arrives as JSON over HTTP and a
 * feature vector arrives from a visitor moving a control.
 */

import { describe, expect, it } from "vitest";

import { assertUsableModel, decompose, ScoringError, withValues, type LinearModel } from "./score";

const MODEL: LinearModel = {
  model_version: "test12345678",
  feature_names: ["elo_diff", "home_b2b"],
  intercept: 0.25,
  coefficients: { elo_diff: 0.7, home_b2b: -0.16 },
  means: { elo_diff: 1.0, home_b2b: 0.14 },
  stds: { elo_diff: 90.0, home_b2b: 0.35 },
};

const AT_THE_MEAN = { elo_diff: 1.0, home_b2b: 0.14 };

describe("decompose", () => {
  it("returns the intercept's probability when every feature sits at its mean", () => {
    const result = decompose(MODEL, AT_THE_MEAN);
    expect(result.contributions.elo_diff).toBe(0);
    expect(result.contributions.home_b2b).toBe(-0);
    expect(result.logit).toBe(0.25);
    expect(result.homeWinProbability).toBeCloseTo(1 / (1 + Math.exp(-0.25)), 15);
  });

  it("sums the contributions to the logit exactly", () => {
    // D-040's waterfall is a decomposition, not an attribution: the terms and the intercept sum to
    // the logit with no residual. That is the whole reason D-023 kept the model class linear.
    const result = decompose(MODEL, { elo_diff: 145.0, home_b2b: 1.0 });
    const total =
      result.baselineLogit + Object.values(result.contributions).reduce((a, b) => a + b, 0);
    expect(total).toBe(result.logit);
  });

  it("moves the probability the way each coefficient's sign says it should", () => {
    const base = decompose(MODEL, AT_THE_MEAN).homeWinProbability;
    // Positive coefficient, value above the mean -> probability up.
    expect(decompose(MODEL, { ...AT_THE_MEAN, elo_diff: 100 }).homeWinProbability).toBeGreaterThan(base);
    // Negative coefficient (the home team on a back-to-back), value up -> probability down.
    expect(decompose(MODEL, { ...AT_THE_MEAN, home_b2b: 1 }).homeWinProbability).toBeLessThan(base);
  });

  it("stays inside [0, 1] at logits that would overflow the naive sigmoid", () => {
    const extreme: LinearModel = { ...MODEL, coefficients: { elo_diff: 400, home_b2b: 0 } };
    const high = decompose(extreme, { elo_diff: 1000, home_b2b: 0.14 }).homeWinProbability;
    const low = decompose(extreme, { elo_diff: -1000, home_b2b: 0.14 }).homeWinProbability;
    expect(high).toBeLessThanOrEqual(1);
    expect(low).toBeGreaterThanOrEqual(0);
    expect(Number.isFinite(high) && Number.isFinite(low)).toBe(true);
    expect(high).toBe(1);
    expect(low).toBe(0);
  });

  it("returns a representable probability where the naive sigmoid returns zero", () => {
    // The stable form's actual payoff, and it is narrower than it first looks. Below z = -709 the
    // naive `1/(1+exp(-z))` overflows: `exp(709)` is near the largest double, `exp(710)` is
    // Infinity, and `1/Infinity` is 0. The stable form computes `exp(z)` instead, which stays
    // representable down to about z = -745. In the band between them one form has an answer and
    // the other has zero.
    const oneFeature: LinearModel = {
      model_version: "test12345678",
      feature_names: ["z"],
      intercept: 0,
      coefficients: { z: 1 },
      means: { z: 0 },
      stds: { z: 1 },
    };
    const stable = decompose(oneFeature, { z: -730 }).homeWinProbability;
    const naive = 1 / (1 + Math.exp(730));
    expect(naive).toBe(0);
    expect(stable).toBeGreaterThan(0);
    expect(stable).toBeLessThan(1e-300);
  });

  it("refuses a missing feature rather than treating it as zero", () => {
    // A silently-zero feature shows up as a mildly disappointing number and nothing else -- the
    // same reason `to_vector` refuses one on the Python side.
    expect(() => decompose(MODEL, { elo_diff: 1.0 })).toThrow(ScoringError);
    expect(() => decompose(MODEL, { elo_diff: 1.0 })).toThrow(/missing=\[home_b2b\]/);
  });

  it("refuses a feature the model does not score", () => {
    expect(() => decompose(MODEL, { ...AT_THE_MEAN, travel_diff: 900 })).toThrow(
      /unexpected=\[travel_diff\]/,
    );
  });

  it("refuses a non-finite feature value", () => {
    expect(() => decompose(MODEL, { ...AT_THE_MEAN, elo_diff: NaN })).toThrow(ScoringError);
    expect(() => decompose(MODEL, { ...AT_THE_MEAN, elo_diff: Infinity })).toThrow(ScoringError);
  });
});

describe("assertUsableModel", () => {
  it("accepts the model the API actually returns", () => {
    expect(() => assertUsableModel(MODEL)).not.toThrow();
  });

  it("refuses a zero standard deviation", () => {
    // The one place the two languages would genuinely disagree if this were let through: Python
    // raises ZeroDivisionError, JavaScript returns Infinity and carries on. The fitter substitutes
    // 1.0 for a degenerate feature, so a zero here means the model is wrong, not the data.
    const broken: LinearModel = { ...MODEL, stds: { ...MODEL.stds, elo_diff: 0 } };
    expect(() => assertUsableModel(broken)).toThrow(/non-positive std/);
  });

  it("refuses a model with no features", () => {
    expect(() => assertUsableModel({ ...MODEL, feature_names: [] })).toThrow(/no feature_names/);
  });

  it("refuses a coefficient that did not survive the network", () => {
    // What a truncated or error-page response looks like by the time it reaches here.
    const broken = { ...MODEL, coefficients: { elo_diff: 0.7 } } as LinearModel;
    expect(() => assertUsableModel(broken)).toThrow(/coefficient for home_b2b/);
  });
});

describe("withValues", () => {
  it("returns a new object and leaves the original untouched", () => {
    // The fence between real and hypothetical (story 26) is easier to hold in the UI when the data
    // structures behind it were never shared to begin with.
    const original = { ...AT_THE_MEAN };
    const changed = withValues(original, { home_b2b: 1 });
    expect(changed).not.toBe(original);
    expect(original.home_b2b).toBe(0.14);
    expect(changed.home_b2b).toBe(1);
  });

  it("refuses to invent a feature this prediction does not have", () => {
    expect(() => withValues(AT_THE_MEAN, { travel_diff: 900 })).toThrow(/not one of this/);
  });

  it("refuses a non-finite hypothetical", () => {
    expect(() => withValues(AT_THE_MEAN, { home_b2b: NaN })).toThrow(ScoringError);
  });
});
