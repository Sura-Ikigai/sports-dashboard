"use client";

/**
 * What the model said as the game approached (T-034, D-012).
 *
 * Seven daily predictions for one game are seven different statements, not six stale ones and a
 * current one -- a rest value computed six days out is *wrong* rather than merely old. Showing the
 * sequence is what makes D-012's append-only design visible instead of merely stored, and it is the
 * only place on this page where the reader can see the model change its mind.
 *
 * The row that stands is marked, because "the last prediction before tip-off is the one scored" is
 * the rule the accuracy record uses and a reader has no way to guess which row that is.
 */

import type { Prediction } from "./types";

export function PredictionHistory({
  history,
  standing,
}: {
  history: readonly Prediction[];
  standing: Prediction;
}) {
  if (history.length <= 1) return null;

  return (
    <section aria-labelledby="history-heading" className="space-y-2">
      <h2 id="history-heading" className="text-lg font-semibold">
        What the model said as the game approached
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="mb-2 text-left text-sm text-slate-600 dark:text-slate-400">
            Every prediction made for this game. The one marked <strong>scored</strong> is the last
            before tip-off, and the only one counted in the accuracy record.
          </caption>
          <thead>
            <tr className="border-b border-slate-300 text-left dark:border-slate-700">
              <th scope="col" className="py-2 pr-4 font-semibold">Made at</th>
              <th scope="col" className="py-2 pr-4 text-right font-semibold">Home win probability</th>
              <th scope="col" className="py-2 font-semibold">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {history.map((entry) => {
              const isStanding = entry.as_of === standing.as_of;
              return (
                <tr key={entry.as_of} className="border-b border-slate-200 dark:border-slate-800">
                  <th scope="row" className="py-2 pr-4 text-left font-normal">
                    <time dateTime={entry.as_of}>{new Date(entry.as_of).toUTCString()}</time>
                    {isStanding && (
                      <span className="ml-2 rounded bg-slate-800 px-1.5 py-0.5 text-xs font-semibold text-white dark:bg-slate-200 dark:text-slate-900">
                        scored
                      </span>
                    )}
                  </th>
                  <td className="py-2 pr-4 text-right tabular-nums">
                    {(entry.home_win_probability * 100).toFixed(1)}%
                  </td>
                  <td className="py-2">{entry.band.label}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
