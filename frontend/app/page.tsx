"use client";

import { useFetch } from "@/hooks/useFetch";
import { usePolling } from "@/hooks/usePolling";
import { GameCard } from "@/components/GameCard";
import { TeamSearch } from "@/components/TeamSearch";

interface Team {
  id: number;
  name: string;
  logo_url: string | null;
}

interface Game {
  id: number;
  home_team: Team;
  away_team: Team;
  home_score: number | null;
  away_score: number | null;
  status: string;
}

interface Favorite {
  team_id: number;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL;

export default function Dashboard() {
  const { data: games, loading: gamesLoading, error, refetch } = useFetch<Game[]>(`${API_URL}/nba/games`);
  const { data: favorites, loading: favoritesLoading } = useFetch<Favorite[]>(`${API_URL}/nba/favorites`);

  usePolling(refetch, 30000);

  if ((gamesLoading && !games) || (favoritesLoading && !favorites)) return <div className="p-8">Loading games...</div>;
  if (error) return <div className="p-8">Error: {error}</div>;

  const favoritedTeamIds = new Set(favorites?.map((f) => f.team_id) ?? []);

  return (
    <main className="p-8 grid grid-cols-1 md:grid-cols-3 gap-4">
      <TeamSearch />
      {games?.map((game) => (
        <GameCard key={game.id} game={game} favoritedTeamIds={favoritedTeamIds} />
      ))}
    </main>
  );
}
