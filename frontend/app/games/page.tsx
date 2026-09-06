"use client";

/**
 * The upcoming-games index (T-034, added after the surface was first shown).
 *
 * ## The window has to be movable, and that is not a convenience
 *
 * `/predictions/upcoming` looks forward from a moment, and its horizon is capped at 30 days --
 * bounded on purpose, because D-047 leaves the endpoint unauthenticated and an open horizon is an
 * open read for anyone who asks. Today is 2026-09-06 and the season opens on 2026-10-20, so an index
 * that could only ask about *now* would be empty for the next month and a half, with no way for a
 * reader to tell that from the job having died.
 *
 * So the page carries a "from" date and a horizon. It is also the control anyone wants mid-season --
 * "what is on next Tuesday" is the same question -- and it keeps the empty state honest: it names
 * the window it just asked about instead of shrugging.
 *
 * The date lives in component state rather than the URL. It could reasonably live in the URL, and
 * the day this page is worth linking to it should; today that would be the only searchParams
 * dependency in the app for a control nobody has asked to share yet.
 */

import { useMemo, useState } from "react";

import { useFetch } from "@/hooks/useFetch";
import { teamNamer } from "@/components/prediction/GameDetail";
import { UpcomingList } from "@/components/prediction/UpcomingList";
import type { TeamSummary, UpcomingResponse } from "@/components/prediction/types";
import { MAX_HORIZON_DAYS, teamsUrl, upcomingUrl } from "@/lib/api/predictions";

const HORIZONS = [7, 14, MAX_HORIZON_DAYS];

/** Today as `YYYY-MM-DD`, in UTC to match every other date on this surface. */
function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function UpcomingPage() {
  const [from, setFrom] = useState(todayUtc);
  const [horizon, setHorizon] = useState(7);

  const url = useMemo(() => upcomingUrl(`${from}T00:00:00Z`, horizon), [from, horizon]);
  const upcoming = useFetch<UpcomingResponse>(url);
  const teams = useFetch<TeamSummary[]>(teamsUrl());
  const teamName = teamNamer(teams.data);

  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 p-4 sm:p-8">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold sm:text-3xl">Upcoming predictions</h1>
        <p className="max-w-prose text-sm text-slate-600 dark:text-slate-400">
          What the model expects, for games tipping off in the window below. Every probability is
          the <strong>home</strong>{" "}
          team&rsquo;s. Follow a game through to see which factors moved it.
        </p>
      </header>

      <form
        className="flex flex-wrap items-end gap-4 rounded border border-slate-300 p-3 dark:border-slate-700"
        onSubmit={(event) => event.preventDefault()}
      >
        <div>
          <label htmlFor="from" className="block text-xs font-medium uppercase tracking-wide text-slate-500">
            From
          </label>
          <input
            id="from"
            type="date"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
            className="mt-1 rounded border border-slate-400 px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600"
          />
        </div>
        <div>
          <label htmlFor="horizon" className="block text-xs font-medium uppercase tracking-wide text-slate-500">
            Looking ahead
          </label>
          <select
            id="horizon"
            value={horizon}
            onChange={(event) => setHorizon(Number(event.target.value))}
            className="mt-1 rounded border border-slate-400 px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600"
          >
            {HORIZONS.map((days) => (
              <option key={days} value={days}>
                {days} days
              </option>
            ))}
          </select>
        </div>
        {upcoming.data && (
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {upcoming.data.games} game{upcoming.data.games === 1 ? "" : "s"},{" "}
            {upcoming.data.predicted} scored
            {upcoming.data.model_version && (
              <>
                {" "}
                by model <code className="font-mono text-xs">{upcoming.data.model_version}</code>
              </>
            )}
            .
          </p>
        )}
      </form>

      {upcoming.loading && !upcoming.data && <p>Loading predictions…</p>}

      {upcoming.error && (
        <p className="text-sm">Predictions could not be loaded: {upcoming.error}.</p>
      )}

      {upcoming.data && upcoming.data.items.length === 0 && (
        // Naming the window it just asked about is the difference between "there is nothing on" and
        // "something is broken" -- a reader has no other way to tell those apart.
        <p className="max-w-prose text-sm text-slate-600 dark:text-slate-400">
          No games are scheduled between <strong>{from}</strong> and {horizon} days later. The model
          predicts over a seven-day horizon each day, so a window before the season opens will be
          empty — move the date forward to find the opener.
        </p>
      )}

      {upcoming.data && upcoming.data.items.length > 0 && (
        <UpcomingList items={upcoming.data.items} teamName={teamName} />
      )}
    </main>
  );
}
