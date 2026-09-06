/**
 * Fixture data for the game-detail tests.
 *
 * The model's numbers are realistic in *shape* and invented in value, for the same reason T-033's
 * contract grid is synthetic: `/models/` is gitignored so a model cannot enter git as if it were
 * source, and a fixture carrying the artifact's real parameters would be that model under another
 * name.
 */

import type { GameDetail, LinearModelResponse, TeamSummary } from "./types";

export const MODEL: LinearModelResponse = {
  model_version: "aaaaaaaaaaaa",
  feature_names: ["elo_diff", "home_b2b", "away_b2b", "avail_diff"],
  intercept: 0.25,
  coefficients: { elo_diff: 0.7, home_b2b: -0.16, away_b2b: 0.14, avail_diff: 0.12 },
  means: { elo_diff: 1.0, home_b2b: 0.14, away_b2b: 0.18, avail_diff: -0.001 },
  stds: { elo_diff: 90.0, home_b2b: 0.35, away_b2b: 0.38, avail_diff: 0.089 },
};

export const TEAMS: TeamSummary[] = [
  { external_id: "8", name: "Detroit Pistons", abbreviation: "DET" },
  { external_id: "2", name: "Boston Celtics", abbreviation: "BOS" },
];

/**
 * A `Lean` home call with contributions of both signs and one exactly zero.
 *
 * **The numbers are internally consistent, and that is not cosmetic.** `baseline + sum(factors)`
 * equals `logit` exactly, and `sigmoid(logit)` equals the probability -- the identity D-040's
 * waterfall rests on. Hand-written numbers that violated it would let the page render an
 * arithmetically impossible prediction and every test still pass; `waterfall.test.ts` asserts the
 * identity, and it caught exactly that when these were first written by hand.
 *
 * `avail_diff` sits exactly at its mean, which is what makes its contribution exactly zero -- the
 * "no effect" case, which is real (plenty of games have it) and which the page must not draw an
 * arrow for.
 */
export const DETAIL: GameDetail = {
  game: {
    game_id: "401909088",
    season: 2027,
    tip_off: "2026-10-20T19:00:00Z",
    home_id: "8",
    away_id: "2",
    neutral_site: false,
    status: "STATUS_SCHEDULED",
    home_score: null,
    away_score: null,
  },
  latest: {
    prediction: {
      game_id: "401909088",
      model_version: "aaaaaaaaaaaa",
      as_of: "2026-10-19T18:00:00Z",
      home_win_probability: 0.5761746390334859,
      favoured: "home",
      confidence: 0.5761746390334859,
      band: { slug: "lean", label: "Lean", lower: 0.55, upper: 0.65 },
    },
    baseline_logit: 0.25,
    logit: 0.3070893216374269,
    factors: [
      { name: "elo_diff", value: 8.6378, contribution: 0.05940511111111111 },
      { name: "home_b2b", value: 0.0, contribution: 0.06400000000000002 },
      { name: "away_b2b", value: 0.0, contribution: -0.06631578947368422 },
      { name: "avail_diff", value: -0.001, contribution: 0 },
    ],
    schedule_snapshot: "cdcf27de81b0",
  },
  history: [
    {
      game_id: "401909088",
      model_version: "aaaaaaaaaaaa",
      as_of: "2026-10-16T18:00:00Z",
      home_win_probability: 0.5210752248039963,
      favoured: "home",
      confidence: 0.5210752248039963,
      band: { slug: "toss-up", label: "Toss-up", lower: 0.5, upper: 0.55 },
    },
    {
      game_id: "401909088",
      model_version: "aaaaaaaaaaaa",
      as_of: "2026-10-19T18:00:00Z",
      home_win_probability: 0.5761746390334859,
      favoured: "home",
      confidence: 0.5761746390334859,
      band: { slug: "lean", label: "Lean", lower: 0.55, upper: 0.65 },
    },
  ],
  band_record: {
    band: { slug: "lean", label: "Lean", lower: 0.55, upper: 0.65 },
    games: 60,
    correct: 37,
    hit_rate: 37 / 60,
    hit_rate_low: 0.4923,
    hit_rate_high: 0.7351,
    mean_probability: 0.5981,
  },
};

/** The same game before any result exists at this confidence -- D-049's null hit rate. */
export const DETAIL_WITHOUT_A_RECORD: GameDetail = {
  ...DETAIL,
  band_record: {
    band: DETAIL.band_record.band,
    games: 0,
    correct: 0,
    hit_rate: null,
    hit_rate_low: null,
    hit_rate_high: null,
    mean_probability: null,
  },
};
