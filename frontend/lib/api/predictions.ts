/**
 * Types and URLs for this project's prediction API (T-034).
 *
 * ## Every call goes through here, and that is the security note in executable form
 *
 * T-034's note is "no direct calls to anything but this project's API". The way that is kept true is
 * that the components below never name a URL: they take data as props, or call a builder here. A
 * test scans the prediction components for absolute URLs and fails on one, with a control proving
 * the scanner can see one when it is there.
 *
 * `NEXT_PUBLIC_API_URL` is the only base, per the project invariant. Nothing here hardcodes
 * `localhost:8000`.
 *
 * The shapes mirror `backend/schemas.py`. They are hand-written rather than generated because the
 * generator would be a build step nobody runs, and the contract that actually matters -- the
 * *arithmetic* -- is pinned by T-033's grid rather than by these types.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL;

/** A confidence band, on the probability the model gave the side it favoured (D-049). */
export interface Band {
  readonly slug: string;
  readonly label: string;
  readonly lower: number;
  readonly upper: number;
}

export interface ScheduledGame {
  readonly game_id: string;
  readonly season: number;
  readonly tip_off: string;
  readonly home_id: string;
  readonly away_id: string;
  readonly neutral_site: boolean;
  readonly status: string;
  readonly home_score: number | null;
  readonly away_score: number | null;
}

export interface Prediction {
  readonly game_id: string;
  readonly model_version: string;
  readonly as_of: string;
  readonly home_win_probability: number;
  readonly favoured: "home" | "away";
  readonly confidence: number;
  readonly band: Band;
}

/** One feature's contribution to the log-odds, with the value that produced it (story 23). */
export interface Factor {
  readonly name: string;
  readonly value: number;
  readonly contribution: number;
}

/**
 * How the model has done in one band.
 *
 * `hit_rate` is **null**, not 0, when nothing has been scored yet (D-049). Anything rendering this
 * has to branch on that rather than formatting it -- `0%` and "no track record" are different
 * claims and only one of them is true.
 */
export interface BandRecord {
  readonly band: Band;
  readonly games: number;
  readonly correct: number;
  readonly hit_rate: number | null;
  readonly hit_rate_low: number | null;
  readonly hit_rate_high: number | null;
  readonly mean_probability: number | null;
}

export interface PredictionDetail {
  readonly prediction: Prediction;
  readonly baseline_logit: number;
  readonly logit: number;
  readonly factors: readonly Factor[];
  readonly schedule_snapshot: string | null;
}

export interface GameDetail {
  readonly game: ScheduledGame;
  readonly latest: PredictionDetail;
  readonly history: readonly Prediction[];
  readonly band_record: BandRecord;
}

export interface TeamSummary {
  readonly external_id: string;
  readonly name: string;
  readonly abbreviation: string | null;
}

export const gameDetailUrl = (gameId: string) =>
  `${API_URL}/predictions/games/${encodeURIComponent(gameId)}`;

export const frozenModelUrl = () => `${API_URL}/predictions/model`;

export const teamsUrl = () => `${API_URL}/nba/teams`;
