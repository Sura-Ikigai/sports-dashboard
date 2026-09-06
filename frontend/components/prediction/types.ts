/** Re-exports so the prediction components import their shapes from one place. */
export type {
  Band,
  BandRecord,
  Factor,
  GameDetail,
  Prediction,
  PredictionDetail,
  ScheduledGame,
  TeamSummary,
} from "@/lib/api/predictions";

export interface UpcomingResponse {
  readonly as_of: string;
  readonly horizon_days: number;
  readonly model_version: string | null;
  readonly games: number;
  readonly predicted: number;
  readonly items: readonly {
    readonly game: import("@/lib/api/predictions").ScheduledGame;
    readonly prediction: import("@/lib/api/predictions").Prediction | null;
  }[];
}

/** `GET /predictions/model`, in the shape `lib/scoring/score.ts` consumes. */
export interface LinearModelResponse {
  readonly model_version: string;
  readonly feature_names: readonly string[];
  readonly intercept: number;
  readonly coefficients: Readonly<Record<string, number>>;
  readonly means: Readonly<Record<string, number>>;
  readonly stds: Readonly<Record<string, number>>;
}
