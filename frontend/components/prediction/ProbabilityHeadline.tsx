"use client";

/**
 * The prediction itself: who the model favours, by how much, and how often it has been right saying
 * that (T-034, stories 19-21, 28).
 *
 * ## The hit rate is the part that is easy to render dishonestly
 *
 * D-049 makes `hit_rate` **null** rather than 0 when a band has nothing scored, precisely because
 * `0%` and "no track record" render identically once a formatter gets hold of them. So this branches
 * on null and says the true thing, and where there *is* a record it shows the Wilson interval beside
 * the point estimate -- one-from-one is 100% and means nothing, and a reader shown only the point
 * estimate has no way to know that.
 */

import type { BandRecord, Prediction, ScheduledGame } from "./types";

const percent = (value: number, digits = 1) => `${(value * 100).toFixed(digits)}%`;

export function BandRecordLine({ record }: { record: BandRecord }) {
  if (record.hit_rate === null || record.games === 0) {
    return (
      <p className="text-sm text-slate-600 dark:text-slate-400">
        No games have been scored at this confidence yet, so there is no track record to judge this
        against.
      </p>
    );
  }
  const interval =
    record.hit_rate_low !== null && record.hit_rate_high !== null
      ? ` (95% interval ${percent(record.hit_rate_low, 0)}–${percent(record.hit_rate_high, 0)})`
      : "";
  return (
    <p className="text-sm text-slate-600 dark:text-slate-400">
      At this confidence the model has been right{" "}
      <strong className="text-slate-900 dark:text-slate-100">{percent(record.hit_rate, 0)}</strong>{" "}
      of the time — {record.correct} of {record.games} games{interval}.
    </p>
  );
}

export function ProbabilityHeadline({
  game,
  prediction,
  record,
  teamName,
}: {
  game: ScheduledGame;
  prediction: Prediction;
  record: BandRecord;
  teamName: (id: string) => string;
}) {
  const favouredId = prediction.favoured === "home" ? game.home_id : game.away_id;
  const asOf = new Date(prediction.as_of);
  const tipOff = new Date(game.tip_off);

  return (
    <section aria-labelledby="prediction-heading" className="space-y-3">
      <h1 id="prediction-heading" className="text-2xl font-bold sm:text-3xl">
        {teamName(game.away_id)} at {teamName(game.home_id)}
      </h1>
      <p className="text-sm text-slate-600 dark:text-slate-400">
        <time dateTime={game.tip_off}>{tipOff.toUTCString()}</time>
        {game.neutral_site && " · neutral site"}
        {" · "}
        {game.status.replace("STATUS_", "").toLowerCase()}
        {game.home_score !== null && game.away_score !== null && (
          <> · final {game.away_score}–{game.home_score}</>
        )}
      </p>

      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-500">
            {teamName(game.home_id)} win probability
          </div>
          <div className="text-5xl font-bold tabular-nums">
            {percent(prediction.home_win_probability)}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-500">Confidence</div>
          <div className="text-2xl font-semibold">
            {prediction.band.label}
            <span className="ml-2 text-base font-normal text-slate-600 dark:text-slate-400">
              {percent(prediction.confidence, 0)} on {teamName(favouredId)}
            </span>
          </div>
        </div>
      </div>

      <BandRecordLine record={record} />

      <p className="text-xs text-slate-500">
        Model <code className="font-mono">{prediction.model_version}</code> · predicted{" "}
        <time dateTime={prediction.as_of}>{asOf.toUTCString()}</time>
      </p>
    </section>
  );
}
