/**
 * The upcoming-games index (T-034).
 *
 * Two claims carry this file:
 *
 *   1. **A game the job has not scored is listed, not hidden.** Hiding it makes "the job stopped
 *      running" and "there are no games" look identical, and the first is an outage that would
 *      otherwise go unnoticed until the accuracy record went quiet.
 *   2. **The probability column is unambiguous.** Every number is the home team's, and the favoured
 *      side is named. A column where some percentages mean "home" and others mean "away" reads fine
 *      until someone acts on it.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { groupByDay, UpcomingList, type UpcomingItem } from "./UpcomingList";
import { DETAIL, TEAMS } from "./fixtures";

afterEach(cleanup);

const teamName = (id: string) =>
  TEAMS.find((t) => t.external_id === id)?.name ?? `Team ${id}`;

function item(gameId: string, tipOff: string, probability: number | null): UpcomingItem {
  return {
    game: { ...DETAIL.game, game_id: gameId, tip_off: tipOff },
    prediction:
      probability === null
        ? null
        : {
            ...DETAIL.latest.prediction,
            game_id: gameId,
            home_win_probability: probability,
            favoured: probability > 0.5 ? "home" : "away",
            confidence: Math.max(probability, 1 - probability),
          },
  };
}

const ITEMS: UpcomingItem[] = [
  item("late", "2026-10-21T02:30:00Z", 0.81),
  item("early", "2026-10-20T19:00:00Z", 0.34),
  item("unscored", "2026-10-20T23:00:00Z", null),
];

describe("groupByDay", () => {
  it("groups on the UTC calendar day and orders by tip-off", () => {
    // UTC everywhere, matching the corpus, the schedule feed and every as-of moment. Switching to
    // local time here alone would put two definitions of "Tuesday" on one page.
    const days = groupByDay(ITEMS);
    expect(days.map(([key]) => key)).toEqual(["2026-10-20", "2026-10-21"]);
    expect(days[0][1].map((i) => i.game.game_id)).toEqual(["early", "unscored"]);
  });
});

describe("the list", () => {
  it("shows one table per day", () => {
    render(<UpcomingList items={ITEMS} teamName={teamName} />);
    expect(screen.getAllByRole("table")).toHaveLength(2);
  });

  it("lists a game the job has not scored rather than dropping it", () => {
    render(<UpcomingList items={ITEMS} teamName={teamName} />);
    expect(screen.getByText(/not scored yet/i)).toBeInTheDocument();
  });

  it("labels every probability as the home team's and names who is favoured", () => {
    render(<UpcomingList items={ITEMS} teamName={teamName} />);
    const tables = screen.getAllByRole("table");
    expect(
      within(tables[0]).getByRole("columnheader", { name: /home win probability/i }),
    ).toBeInTheDocument();
    // 0.34 home probability favours away, and the away team must be the one named.
    const row = within(tables[0]).getByText("34.0%").closest("tr")!;
    expect(within(row as HTMLElement).getByText("Boston Celtics")).toBeInTheDocument();
  });

  it("links each game to its explanation with a name a screen reader can tell apart", () => {
    // Three links all reading "Why →" would be useless out of context, which is how a screen reader
    // presents a link list.
    render(<UpcomingList items={ITEMS} teamName={teamName} />);
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(3);
    expect(links[0]).toHaveAttribute("href", "/games/early");
    expect(links[0]).toHaveAccessibleName(/boston celtics at detroit pistons/i);
  });
});
