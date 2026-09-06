"use client";

import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { FavoriteButton } from "./FavoriteButton";

interface Team {
  id: number;
  name: string;
  logo_url: string | null;
}

interface Game {
  id: number;
  external_id: string;
  home_team: Team;
  away_team: Team;
  home_score: number | null;
  away_score: number | null;
  status: string;
}

function TeamRow({ team, score, favorited }: { team: Team; score: number | null; favorited: boolean }) {
  return (
    <div className="flex justify-between items-center">
      <div className="flex items-center gap-2">
        <FavoriteButton teamId={String(team.id)} initialFavorited={favorited} />
        <span>{team.name}</span>
      </div>
      <AnimatePresence mode="popLayout">
        <motion.span
          key={score}
          initial={{ y: -10, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 10, opacity: 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
          className="text-2xl font-bold"
        >
          {score ?? "-"}
        </motion.span>
      </AnimatePresence>
    </div>
  );
}

export function GameCard({ game, favoritedTeamIds }: { game: Game; favoritedTeamIds: Set<number> }) {
  return (
    <div className="border rounded-lg p-4 bg-gray-900 text-white">
      <TeamRow team={game.away_team} score={game.away_score} favorited={favoritedTeamIds.has(game.away_team.id)} />
      <TeamRow team={game.home_team} score={game.home_score} favorited={favoritedTeamIds.has(game.home_team.id)} />
      <div className="mt-2 flex items-center justify-between">
        <span className="text-xs text-gray-400 uppercase">{game.status}</span>
        {/* The app's games and the prediction API share the ESPN id space (no translation layer
            exists or is needed), so this is a direct link. A game the model has not scored renders
            its own "no prediction" state rather than an error. */}
        <Link
          href={`/games/${game.external_id}`}
          className="text-xs underline underline-offset-2 hover:text-sky-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:focus-visible:outline-sky-400"
        >
          Prediction &rarr;
        </Link>
      </div>
    </div>
  );
}
