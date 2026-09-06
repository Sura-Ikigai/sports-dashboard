"use client";

/**
 * The upcoming-games index (T-034).
 *
 * ## Grouped by day, because a flat list of 60 games is a wall
 *
 * Tip-off times are what a reader scans by, so the day is the heading and the time is the leading
 * column. Grouping happens on the **UTC calendar day**, matching every other date on this surface --
 * the corpus, the schedule feed and the as-of moments are all UTC, and switching to local time here
 * alone would put two definitions of "Tuesday" on one page.
 *
 * ## A game with no prediction is listed, not hidden
 *
 * The same rule the API follows. If the job has not scored a game yet, it appears saying so. Hiding
 * it would make "the job stopped running" and "there are no games" look identical, and the first of
 * those is an outage nobody would notice until the accuracy record went quiet.
 *
 * ## Direction reads the same way it does on the detail page
 *
 * The favoured side is named in words and the probability is always the **home** team's, labelled as
 * such. A column of bare percentages where some mean "home" and others mean "away" is the sort of
 * thing that reads fine until someone acts on it.
 */

import Link from "next/link";

import type { Prediction, ScheduledGame } from "./types";

export interface UpcomingItem {
  readonly game: ScheduledGame;
  readonly prediction: Prediction | null;
}

const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:focus-visible:outline-sky-400";

/** The UTC calendar day, as a stable key. */
export function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

export function groupByDay(items: readonly UpcomingItem[]): [string, UpcomingItem[]][] {
  const groups = new Map<string, UpcomingItem[]>();
  for (const item of [...items].sort((a, b) => a.game.tip_off.localeCompare(b.game.tip_off))) {
    const key = dayKey(item.game.tip_off);
    const existing = groups.get(key);
    if (existing) existing.push(item);
    else groups.set(key, [item]);
  }
  return [...groups.entries()];
}

const dayLabel = (key: string) =>
  new Date(`${key}T00:00:00Z`).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });

const timeLabel = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    hour12: false,
  });

export function UpcomingList({
  items,
  teamName,
}: {
  items: readonly UpcomingItem[];
  teamName: (id: string) => string;
}) {
  const days = groupByDay(items);

  return (
    <div className="space-y-8">
      {days.map(([key, games]) => (
        <section key={key} aria-labelledby={`day-${key}`}>
          <h2 id={`day-${key}`} className="mb-2 text-lg font-semibold">
            {dayLabel(key)}
            <span className="ml-2 text-sm font-normal text-slate-500">
              {games.length} game{games.length === 1 ? "" : "s"}
            </span>
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">
                Games on {dayLabel(key)}, with the model&rsquo;s predicted home win probability.
              </caption>
              <thead>
                <tr className="border-b border-slate-300 text-left dark:border-slate-700">
                  <th scope="col" className="py-2 pr-4 font-semibold">
                    <span title="Coordinated Universal Time">Tip-off (UTC)</span>
                  </th>
                  <th scope="col" className="py-2 pr-4 font-semibold">Game</th>
                  <th scope="col" className="py-2 pr-4 text-right font-semibold">
                    Home win probability
                  </th>
                  <th scope="col" className="py-2 pr-4 font-semibold">Model favours</th>
                  <th scope="col" className="py-2 font-semibold">
                    <span className="sr-only">Detail</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {games.map(({ game, prediction }) => (
                  <tr key={game.game_id} className="border-b border-slate-200 dark:border-slate-800">
                    <td className="py-2 pr-4 tabular-nums">
                      <time dateTime={game.tip_off}>{timeLabel(game.tip_off)}</time>
                    </td>
                    <th scope="row" className="py-2 pr-4 text-left font-normal">
                      {teamName(game.away_id)}{" "}
                      <span className="text-slate-500">at</span> {teamName(game.home_id)}
                      {game.neutral_site && (
                        <span className="ml-2 text-xs text-slate-500">neutral site</span>
                      )}
                    </th>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      {prediction ? (
                        `${(prediction.home_win_probability * 100).toFixed(1)}%`
                      ) : (
                        <span className="text-slate-500">not scored yet</span>
                      )}
                    </td>
                    <td className="py-2 pr-4">
                      {prediction ? (
                        <>
                          <span
                            className={
                              prediction.favoured === "home"
                                ? "text-sky-700 dark:text-sky-300"
                                : "text-amber-700 dark:text-amber-300"
                            }
                          >
                            {teamName(
                              prediction.favoured === "home" ? game.home_id : game.away_id,
                            )}
                          </span>
                          <span className="ml-2 text-xs text-slate-500">
                            {prediction.band.label}
                          </span>
                        </>
                      ) : (
                        <span className="text-slate-500">—</span>
                      )}
                    </td>
                    <td className="py-2">
                      <Link
                        href={`/games/${game.game_id}`}
                        className={`underline underline-offset-2 ${FOCUS}`}
                      >
                        Why<span className="sr-only">
                          {" "}
                          the model says that, for {teamName(game.away_id)} at{" "}
                          {teamName(game.home_id)}
                        </span>{" "}
                        &rarr;
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}
