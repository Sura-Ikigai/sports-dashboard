/**
 * The game detail surface (T-034) -- what it shows and, more importantly, what it refuses to show.
 *
 * The rendering is mostly plumbing. Three things here are not:
 *
 *   1. **A band with no record says so** rather than rendering `0%`. D-049 makes `hit_rate` null for
 *      exactly this reason, and a formatter that reached it would turn "we have never tested this"
 *      into "the model is never right at this confidence".
 *   2. **The waterfall's arithmetic is visible and checkable** -- baseline plus factors equals the
 *      total, shown as rows rather than asserted in prose.
 *   3. **Sign is carried by more than colour.** Colour cannot be asserted meaningfully in jsdom,
 *      which is precisely why the direction is also a word in a cell: the thing that makes the chart
 *      accessible is the same thing that makes it testable.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { GameDetailView, teamNamer } from "./GameDetail";
import { DETAIL, DETAIL_WITHOUT_A_RECORD, MODEL, TEAMS } from "./fixtures";

afterEach(cleanup);

describe("the prediction headline", () => {
  it("shows the probability, the band, the model version and the moment it was made", () => {
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    const headline = screen.getByRole("region", { name: /boston celtics at detroit pistons/i });

    expect(within(headline).getByText("57.6%")).toBeInTheDocument();
    expect(within(headline).getByText(/lean/i)).toBeInTheDocument();
    expect(within(headline).getByText("aaaaaaaaaaaa")).toBeInTheDocument();
    // Story 28: a prediction has to be traceable to the exact model and moment that made it.
    expect(headline.querySelector('time[datetime="2026-10-19T18:00:00Z"]')).not.toBeNull();
  });

  it("grounds the confidence in a hit rate with an interval, not a bare number", () => {
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    expect(screen.getByText(/37 of 60 games/)).toBeInTheDocument();
    expect(screen.getByText(/95% interval 49%–74%/)).toBeInTheDocument();
  });

  it("says there is no track record rather than showing a hit rate of zero", () => {
    // D-049's null, rendered. `0%` and "never tested" are different claims.
    render(<GameDetailView detail={DETAIL_WITHOUT_A_RECORD} model={MODEL} teams={TEAMS} />);
    expect(screen.getByText(/no games have been scored at this confidence yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/has been right/i)).not.toBeInTheDocument();
  });
});

describe("the waterfall", () => {
  function mainWaterfall() {
    return within(screen.getByRole("region", { name: /why the model says that/i })).getByRole(
      "table",
    );
  }

  it("is a real table with a caption and row headers, not a picture", () => {
    // The accessible form is the *only* form: there is no second, visually-hidden copy to drift.
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    const table = mainWaterfall();
    expect(within(table).getByText(/how each factor moved the prediction/i)).toBeInTheDocument();
    expect(within(table).getAllByRole("rowheader")).toHaveLength(
      DETAIL.latest.factors.length + 2, // baseline and total
    );
  });

  it("shows a baseline row, one row per factor, and a total", () => {
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    const table = mainWaterfall();
    expect(within(table).getByRole("rowheader", { name: /baseline/i })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: /total/i })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: /elo rating gap/i })).toBeInTheDocument();
  });

  it("shows each factor's underlying value beside its contribution", () => {
    // Story 23: the point is seeing *why* a factor contributed what it did, not only that it did.
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    const table = mainWaterfall();
    expect(within(table).getByText("+8.6")).toBeInTheDocument();
    expect(within(table).getByText("+0.059")).toBeInTheDocument();
  });

  it("carries direction in words as well as position and colour", () => {
    // jsdom cannot see colour, which is the point: the channel that makes this testable is the same
    // channel that makes it legible to a screen reader.
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    const table = mainWaterfall();
    expect(within(table).getAllByText(/toward home/).length).toBeGreaterThan(0);
    expect(within(table).getAllByText(/toward away/).length).toBeGreaterThan(0);
  });

  it("calls a zero contribution no effect rather than giving it a direction", () => {
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    expect(within(mainWaterfall()).getByText(/no effect/i)).toBeInTheDocument();
  });

  it("does not describe the baseline as a factor pushing one way", () => {
    // The baseline is where the prediction starts before any factor is read -- a property of every
    // game, not of this one. Calling it "toward home" invites a reader to count it as a fifth
    // factor, which is the kind of small dishonesty that survives review because it reads fluently.
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    const table = mainWaterfall();
    const baselineRow = within(table).getByRole("rowheader", { name: /baseline/i }).closest("tr")!;
    expect(within(baselineRow as HTMLElement).getByText(/starting point/i)).toBeInTheDocument();
    expect(within(baselineRow as HTMLElement).queryByText(/toward/i)).not.toBeInTheDocument();
  });

  it("describes the total as a conclusion rather than as a movement", () => {
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    const table = mainWaterfall();
    const totalRow = within(table).getByRole("rowheader", { name: /total/i }).closest("tr")!;
    expect(within(totalRow as HTMLElement).getByText(/favours home/i)).toBeInTheDocument();
  });

  it("labels the zero baseline the bars are measured from", () => {
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    const table = mainWaterfall();
    expect(within(table).getByText(/← away/)).toBeInTheDocument();
    expect(within(table).getByText(/home →/)).toBeInTheDocument();
  });

  it("says contributions are log-odds, because the magnitudes invite the wrong reading", () => {
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    expect(within(mainWaterfall()).getByText(/log-odds/)).toBeInTheDocument();
  });
});

describe("the prediction history", () => {
  it("shows every prediction and marks the one that is scored", () => {
    // D-012: seven daily predictions are seven statements, and exactly one of them is the model's
    // final word. A reader cannot guess which row that is.
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
    const table = within(
      screen.getByRole("region", { name: /what the model said as the game approached/i }),
    ).getByRole("table");
    const rows = within(table).getAllByRole("rowheader");
    expect(rows).toHaveLength(DETAIL.history.length);
    // Exactly one row carries the badge -- "the last prediction before tip-off is the one scored"
    // names a single row, and a page marking two would be describing a rule that does not exist.
    const badged = rows.filter((row) => row.textContent?.includes("scored"));
    expect(badged).toHaveLength(1);
    expect(badged[0].textContent).toContain("Mon, 19 Oct 2026");
  });

  it("is omitted when there is only one prediction to show", () => {
    const single = { ...DETAIL, history: [DETAIL.latest.prediction] };
    render(<GameDetailView detail={single} model={MODEL} teams={TEAMS} />);
    expect(
      screen.queryByRole("region", { name: /what the model said as the game approached/i }),
    ).not.toBeInTheDocument();
  });
});

describe("degrading without the optional calls", () => {
  it("still shows the real prediction when the model parameters are unavailable", () => {
    // The decomposition comes from the prediction row, not from the model, so losing the model costs
    // the what-if panel and nothing else.
    render(<GameDetailView detail={DETAIL} model={null} teams={TEAMS} />);
    const headline = screen.getByRole("region", { name: /boston celtics at detroit pistons/i });
    expect(within(headline).getByText("57.6%")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /what if/i })).not.toBeInTheDocument();
    expect(screen.getByText(/what-if panel is not shown/i)).toBeInTheDocument();
  });

  it("falls back to the team id when names are unavailable", () => {
    // The prediction API returns ids by design. The id is what the model scored, so showing it is
    // correct rather than merely tolerable.
    render(<GameDetailView detail={DETAIL} model={MODEL} teams={null} />);
    expect(screen.getByRole("heading", { name: /team 2 at team 8/i })).toBeInTheDocument();
  });
});

describe("teamNamer", () => {
  it("maps known ids and falls back for unknown ones", () => {
    const name = teamNamer(TEAMS);
    expect(name("8")).toBe("Detroit Pistons");
    expect(name("999")).toBe("Team 999");
    expect(teamNamer(null)("8")).toBe("Team 8");
  });
});
