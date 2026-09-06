import type { Metadata } from "next";

/**
 * A title for the route. The page itself is a client component and cannot export metadata, so the
 * segment layout carries it -- otherwise every prediction page inherits the root title, which until
 * this commit was still "Create Next App".
 */
export const metadata: Metadata = {
  title: "Game prediction · Sports Dashboard",
  description: "What the model predicts for this game, and which factors moved it.",
};

export default function GameLayout({ children }: { children: React.ReactNode }) {
  return children;
}
