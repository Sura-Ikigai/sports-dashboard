/**
 * T-034's security note, made mechanical.
 *
 * > no direct calls to anything but this project's API. Hypothetical state must never reach a write
 * > endpoint.
 *
 * `WhatIfPanel.test.tsx` covers the second sentence behaviourally, by driving every control and
 * failing on any request. This file covers the first, and covers the second again from the other
 * direction -- by reading the source, which catches a call on a path no test happens to drive.
 *
 * Both are here rather than one, because they fail on different things: a source scan cannot see a
 * request made through a helper it does not recognise, and a behavioural test cannot see a request
 * on a branch it never renders.
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { decompose } from "@/lib/scoring/score";
import { DETAIL, MODEL } from "./fixtures";

const HERE = __dirname;
const ROOT = path.resolve(HERE, "..", "..");

const sourcesIn = (dir: string) =>
  readdirSync(dir)
    .filter((f) => (f.endsWith(".ts") || f.endsWith(".tsx")) && !f.includes(".test."))
    .map((f) => ({ name: f, text: readFileSync(path.join(dir, f), "utf8") }));

const PREDICTION_SOURCES = [
  ...sourcesIn(HERE),
  ...sourcesIn(path.join(ROOT, "app", "games", "[gameId]")),
  ...sourcesIn(path.join(ROOT, "lib", "api")),
];

/** An absolute URL literal. `http://` or `https://` anywhere in the code. */
const ABSOLUTE_URL = /https?:\/\/[^\s"'`]+/g;

/** Anything that leaves the page: a request, a storage write, a history entry, a beacon. */
const ESCAPE_ROUTE = /\b(fetch|sendBeacon|XMLHttpRequest|localStorage|sessionStorage|pushState|replaceState)\b/;

function findAll(pattern: RegExp, sources: typeof PREDICTION_SOURCES) {
  return sources.flatMap(({ name, text }) =>
    text
      .split("\n")
      .map((line, index) => ({ name, line: index + 1, text: line }))
      // Comments may discuss what code may not do -- these files explain the rule they are held to,
      // and an explanation that gets deleted to appease a checker stops explaining anything.
      .filter((entry) => !/^\s*(\*|\/\/)/.test(entry.text))
      .filter((entry) => pattern.test(entry.text))
      .map((entry) => `${entry.name}:${entry.line} ${entry.text.trim()}`),
  );
}

describe("this page talks only to this project's API", () => {
  it("finds the files it means to check", () => {
    // A scanner pointed at an empty directory passes forever. This is the floor.
    expect(PREDICTION_SOURCES.length).toBeGreaterThanOrEqual(6);
    expect(PREDICTION_SOURCES.map((s) => s.name)).toContain("page.tsx");
    expect(PREDICTION_SOURCES.map((s) => s.name)).toContain("predictions.ts");
  });

  it("names no absolute URL anywhere", () => {
    // Every URL is built from NEXT_PUBLIC_API_URL, per the project invariant. A literal host here
    // would be either a hardcoded localhost or a third party, and both are the thing the note
    // forbids.
    expect(findAll(ABSOLUTE_URL, PREDICTION_SOURCES)).toEqual([]);
  });

  it("would notice one", () => {
    const planted = [{ name: "rogue.ts", text: 'const u = "https://api.example.com/x";' }];
    expect(findAll(ABSOLUTE_URL, planted)).not.toEqual([]);
  });

  it("has no way out of the page in the components themselves", () => {
    // The route uses `useFetch`, which is where requests belong and where they are already tested.
    // Nothing under `components/prediction/` may request, store, or navigate.
    expect(findAll(ESCAPE_ROUTE, sourcesIn(HERE))).toEqual([]);
  });

  it("would notice one of those too", () => {
    const planted = [
      { name: "rogue.tsx", text: "  localStorage.setItem('whatif', JSON.stringify(values));" },
      { name: "rogue2.tsx", text: "  void fetch(url, { method: 'POST' });" },
    ];
    expect(findAll(ESCAPE_ROUTE, planted)).toHaveLength(2);
  });
});

describe("the fixture is arithmetically possible", () => {
  it("satisfies the identity the whole surface rests on", () => {
    // A fixture that violated `baseline + sum(factors) = logit` would let every rendering test pass
    // while the page displayed an impossible prediction. This caught exactly that once already.
    const { baseline_logit, factors, logit, prediction } = DETAIL.latest;
    const summed = factors.reduce((total, f) => total + f.contribution, baseline_logit);
    expect(summed).toBeCloseTo(logit, 15);
    expect(1 / (1 + Math.exp(-logit))).toBeCloseTo(prediction.home_win_probability, 15);
  });

  it("is what the scorer actually produces from those feature values", () => {
    // Stronger than the identity: the contributions are the ones the shipped arithmetic yields for
    // this model and these values, so the fixture is a realistic API response rather than a set of
    // numbers that merely add up.
    const features = Object.fromEntries(DETAIL.latest.factors.map((f) => [f.name, f.value]));
    const scored = decompose(MODEL, features);
    for (const factor of DETAIL.latest.factors) {
      expect(scored.contributions[factor.name]).toBe(factor.contribution);
    }
    expect(scored.homeWinProbability).toBe(DETAIL.latest.prediction.home_win_probability);
  });
});
