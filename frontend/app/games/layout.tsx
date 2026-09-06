import type { Metadata } from "next";

/**
 * Metadata for everything under `/games`. The pages themselves are client components and cannot
 * export it; the nested `[gameId]/layout.tsx` overrides the title for a single game.
 */
export const metadata: Metadata = {
  title: "Predictions · Sports Dashboard",
  description: "Upcoming NBA games and what the model expects.",
};

export default function GamesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
