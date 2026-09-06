/**
 * Feature presentation (T-034).
 *
 * The map is deliberately allowed to be incomplete -- the model's feature set is decided in Python
 * and this file cannot be the thing that decides it. What must never happen is a feature the map has
 * not heard of vanishing from the page, so the fallback is the part with tests.
 */

import { describe, expect, it } from "vitest";

import { isKnownFeature, presentationFor, rangeFor } from "./presentation";

describe("presentationFor", () => {
  it("describes the features the shipped model actually has", () => {
    for (const name of ["elo_diff", "home_b2b", "away_b2b", "avail_diff"]) {
      expect(isKnownFeature(name)).toBe(true);
      expect(presentationFor(name, 0).label).not.toBe(name);
    }
  });

  it("still returns something usable for a feature it has never heard of", () => {
    expect(isKnownFeature("moon_phase")).toBe(false);
    const fallback = presentationFor("moon_phase", 3);
    expect(fallback.label).toBe("moon_phase");
    expect(fallback.control).toBe("continuous");
    expect(fallback.format(3)).toBe("3.000");
  });

  it("renders binary features as words rather than as 1 and 0", () => {
    expect(presentationFor("home_b2b", 1).format(1)).toBe("Yes");
    expect(presentationFor("home_b2b", 0).format(0)).toBe("No");
  });
});

describe("rangeFor", () => {
  it("uses the hand-written range when the value sits inside it", () => {
    expect(rangeFor("elo_diff", 8.6)).toEqual({ min: -400, max: 400, step: 1 });
  });

  it("widens rather than clamps when the real value sits outside", () => {
    // Clamping would silently redraw the actual value as something it is not, which on a page whose
    // whole job is explaining a specific prediction is the one unforgivable bug.
    expect(rangeFor("elo_diff", 620).max).toBe(620);
    expect(rangeFor("elo_diff", -620).min).toBe(-620);
  });

  it("builds a range around the value for an unknown feature", () => {
    const range = rangeFor("moon_phase", 7);
    expect(range.min).toBeLessThanOrEqual(7);
    expect(range.max).toBeGreaterThanOrEqual(7);
    expect(range.step).toBeGreaterThan(0);
  });
});
