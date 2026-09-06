"use client";

/**
 * A route per game (T-034).
 *
 * Client-rendered with `useFetch`, matching `app/page.tsx` rather than introducing a second
 * data-fetching pattern alongside it. A server component would paint sooner and would put the
 * accessible table in the initial HTML, which is worth doing the day this surface has a reader who
 * is not the owner; today it would be the only server-fetching page in the app.
 *
 * The three requests are the whole network surface of this page: this project's prediction API for
 * the game and the model, and this project's own `/nba/teams` for the id-to-name join. T-034's
 * security note is "no direct calls to anything but this project's API", and a test scans this
 * directory for absolute URLs to keep that true.
 */

import { use } from "react";

import { useFetch } from "@/hooks/useFetch";
import { GameDetailView } from "@/components/prediction/GameDetail";
import type { GameDetail, LinearModelResponse, TeamSummary } from "@/components/prediction/types";
import { frozenModelUrl, gameDetailUrl, teamsUrl } from "@/lib/api/predictions";

export default function GameDetailPage({ params }: { params: Promise<{ gameId: string }> }) {
  const { gameId } = use(params);
  const detail = useFetch<GameDetail>(gameDetailUrl(gameId));
  const model = useFetch<LinearModelResponse>(frozenModelUrl());
  const teams = useFetch<TeamSummary[]>(teamsUrl());

  if (detail.loading && !detail.data) {
    return <main className="p-8">Loading the prediction…</main>;
  }
  if (detail.error || !detail.data) {
    return (
      <main className="mx-auto max-w-4xl p-8">
        <h1 className="text-xl font-semibold">No prediction for this game</h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          {detail.error === "HTTP 404"
            ? "The model has not scored this game. Predictions are appended daily over a seven-day horizon, so a game further out than that has none yet."
            : `The prediction could not be loaded: ${detail.error ?? "unknown error"}.`}
        </p>
      </main>
    );
  }

  // `model` and `teams` are allowed to fail without taking the page with them: the real prediction
  // and its decomposition come from `detail` alone. A missing model costs the what-if panel; missing
  // teams cost the names and nothing else.
  return <GameDetailView detail={detail.data} model={model.data} teams={teams.data} />;
}
