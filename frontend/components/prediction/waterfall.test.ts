/**
 * The waterfall's arithmetic and ordering, without rendering anything (T-034).
 *
 * Kept separate from the component test because these are the properties that must hold for *any*
 * model, including one whose features this code has never heard of -- and a rendering test can only
 * check the fixture in front of it.
 */

import { describe, expect, it } from "vitest";

import { directionOf, waterfallRows } from "./ContributionWaterfall";
import { DETAIL } from "./fixtures";

describe("waterfallRows", () => {
  const rows = waterfallRows(
    DETAIL.latest.baseline_logit,
    DETAIL.latest.factors,
    DETAIL.latest.logit,
  );

  it("brackets the factors with a baseline and a total", () => {
    expect(rows[0].kind).toBe("baseline");
    expect(rows[rows.length - 1].kind).toBe("total");
    expect(rows.filter((r) => r.kind === "factor")).toHaveLength(DETAIL.latest.factors.length);
  });

  it("keeps the model's own feature order rather than sorting by magnitude", () => {
    // A waterfall whose bars reorder per game is a waterfall nobody can compare across games.
    expect(rows.filter((r) => r.kind === "factor").map((r) => r.key)).toEqual(
      DETAIL.latest.factors.map((f) => f.name),
    );
  });

  it("shows a total that the baseline and the factors actually add up to", () => {
    // D-040's decomposition is exact by construction (D-023 kept the model linear for it). If this
    // ever needed a tolerance, a term would have gone missing.
    const baseline = rows[0].contribution;
    const factors = rows.filter((r) => r.kind === "factor").reduce((a, r) => a + r.contribution, 0);
    expect(baseline + factors).toBeCloseTo(rows[rows.length - 1].contribution, 12);
  });

  it("renders a factor this code has no description for rather than dropping it", () => {
    // The failure this guards is a model update that silently removes a bar: an incomplete
    // decomposition that still looks complete, which is the one thing exactness must not lose.
    const rowsWithUnknown = waterfallRows(0.1, [{ name: "moon_phase", value: 0.5, contribution: 0.2 }], 0.3);
    const factor = rowsWithUnknown.find((r) => r.kind === "factor");
    expect(factor?.key).toBe("moon_phase");
    expect(factor?.label).toBe("moon_phase");
    expect(factor?.value).toBe("0.500");
  });
});

describe("directionOf", () => {
  it("reads the sign", () => {
    expect(directionOf(0.4)).toBe("home");
    expect(directionOf(-0.4)).toBe("away");
  });

  it("treats exactly zero as no direction rather than as a third one", () => {
    // A zero contribution is a real state -- `avail_diff` is exactly 0 on plenty of games -- and
    // giving it an arrow would claim an effect the model did not have.
    expect(directionOf(0)).toBeNull();
    expect(directionOf(-0)).toBeNull();
  });
});
