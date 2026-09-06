"use client";

/**
 * The game detail surface (T-034) -- everything the page shows, assembled.
 *
 * Split from the route so it can be tested by handing it data rather than by intercepting three
 * fetches. The route does the fetching; this does the showing, and nothing here knows a URL.
 */

import Link from "next/link";

import type { GameDetail as GameDetailData, LinearModelResponse, TeamSummary } from "./types";
import { ContributionWaterfall } from "./ContributionWaterfall";
import { PredictionHistory } from "./PredictionHistory";
import { ProbabilityHeadline } from "./ProbabilityHeadline";
import { WhatIfPanel } from "./WhatIfPanel";

/**
 * Team ids to names, falling back to the id.
 *
 * The prediction API returns ids, deliberately: it serves model outputs, and a join to the app's
 * team table inside it would put corpus-adjacent data in the one module whose security property is
 * that it has none. The join is here instead, and it degrades to showing the id -- which is correct
 * rather than merely tolerable, because the id is what the model actually scored.
 */
export function teamNamer(teams: readonly TeamSummary[] | null) {
  const byId = new Map((teams ?? []).map((team) => [team.external_id, team.name]));
  return (id: string) => byId.get(id) ?? `Team ${id}`;
}

export function GameDetailView({
  detail,
  model,
  teams,
}: {
  detail: GameDetailData;
  model: LinearModelResponse | null;
  teams: readonly TeamSummary[] | null;
}) {
  const teamName = teamNamer(teams);
  const { latest } = detail;

  return (
    <main className="mx-auto w-full max-w-4xl space-y-8 p-4 sm:p-8">
      <Link
        href="/games"
        className="inline-block text-sm underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:focus-visible:outline-sky-400"
      >
        &larr; All upcoming predictions
      </Link>

      <ProbabilityHeadline
        game={detail.game}
        prediction={latest.prediction}
        record={detail.band_record}
        teamName={teamName}
      />

      <section aria-labelledby="waterfall-heading" className="space-y-2">
        <h2 id="waterfall-heading" className="text-lg font-semibold">
          Why the model says that
        </h2>
        <ContributionWaterfall
          baselineLogit={latest.baseline_logit}
          logit={latest.logit}
          factors={latest.factors}
        />
      </section>

      {model ? (
        <WhatIfPanel
          model={model}
          factors={latest.factors}
          actualProbability={latest.prediction.home_win_probability}
        />
      ) : (
        <p className="text-sm text-slate-600 dark:text-slate-400">
          The model parameters are unavailable, so the what-if panel is not shown. Everything above
          is the model&rsquo;s real prediction and is unaffected.
        </p>
      )}

      <PredictionHistory history={detail.history} standing={latest.prediction} />

      <footer className="border-t border-slate-200 pt-4 text-xs text-slate-500 dark:border-slate-800">
        Schedule snapshot{" "}
        {/* Truncated for reading, full value in the title -- D-048's provenance is only useful if
            somebody can copy it, and a 64-character hash in a footer is only ever skimmed. */}
        <code className="font-mono" title={latest.schedule_snapshot ?? undefined}>
          {latest.schedule_snapshot ? `${latest.schedule_snapshot.slice(0, 12)}…` : "not recorded"}
        </code>
        . Contributions are log-odds and sum to the total exactly.
      </footer>
    </main>
  );
}
