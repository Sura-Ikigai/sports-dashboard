/**
 * The fence (T-034, D-040, stories 26-27).
 *
 * Three claims, and the first two are the ones the plan is specific about:
 *
 *   1. **Hypothetical state never leaves the browser.** Asserted behaviourally -- every control is
 *      driven and the test fails if a single network request, storage write or history entry
 *      happens. A source scan would miss a persistence path added three components deep; this does
 *      not.
 *   2. **The real prediction does not move.** This is what makes the fence survive a screenshot: if
 *      the main waterfall responded to the sliders, a screenshot of it would be indistinguishable
 *      from the model's actual output.
 *   3. The panel is usable from a keyboard and labelled for a screen reader.
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GameDetailView } from "./GameDetail";
import { DETAIL, MODEL, TEAMS } from "./fixtures";

function renderSurface() {
  return render(<GameDetailView detail={DETAIL} model={MODEL} teams={TEAMS} />);
}

/** Every way this page could persist or transmit something, watched at once. */
function watchEscapeRoutes() {
  const fetchSpy = vi.fn(() => Promise.resolve(new Response("{}")));
  vi.stubGlobal("fetch", fetchSpy);
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  const pushState = vi.spyOn(window.history, "pushState");
  const replaceState = vi.spyOn(window.history, "replaceState");
  const beacon = vi.fn(() => true);
  vi.stubGlobal("navigator", { ...window.navigator, sendBeacon: beacon });
  return { fetchSpy, setItem, pushState, replaceState, beacon };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the what-if panel", () => {
  let routes: ReturnType<typeof watchEscapeRoutes>;

  beforeEach(() => {
    routes = watchEscapeRoutes();
  });

  it("sends nothing anywhere, no matter how much is changed", async () => {
    const user = userEvent.setup();
    renderSurface();

    await user.click(screen.getByLabelText(/home played last night/i));
    await user.click(screen.getByLabelText(/away played last night/i));
    fireEvent.change(screen.getByLabelText(/elo rating gap/i), { target: { value: "250" } });
    fireEvent.change(screen.getByLabelText(/availability gap/i), { target: { value: "0.4" } });
    await user.click(screen.getByRole("button", { name: /reset/i }));

    expect(routes.fetchSpy).not.toHaveBeenCalled();
    expect(routes.setItem).not.toHaveBeenCalled();
    expect(routes.pushState).not.toHaveBeenCalled();
    expect(routes.replaceState).not.toHaveBeenCalled();
    expect(routes.beacon).not.toHaveBeenCalled();
  });

  it("leaves the real prediction exactly where it was", async () => {
    // The screenshot property, as a test. If the headline moved, a crop of it would read as the
    // model's actual output while showing a number the model never produced.
    renderSurface();
    const explanation = screen.getByRole("region", { name: /why the model says that/i });
    const headline = screen.getByRole("region", { name: /boston celtics at detroit pistons/i });
    const before = { explanation: explanation.textContent, headline: headline.textContent };

    fireEvent.change(screen.getByLabelText(/elo rating gap/i), { target: { value: "400" } });

    expect(explanation.textContent).toBe(before.explanation);
    expect(headline.textContent).toBe(before.headline);
    expect(within(headline).getByText("57.6%")).toBeInTheDocument();
  });

  it("actually moves the hypothetical probability", async () => {
    // The control. Without it, a panel whose controls did nothing would pass every test above.
    renderSurface();
    const live = screen.getByText(/home win probability under the changed factors/i).parentElement!;
    const before = live.textContent;

    fireEvent.change(screen.getByLabelText(/elo rating gap/i), { target: { value: "400" } });

    expect(live.textContent).not.toBe(before);
  });

  it("marks itself hypothetical in words, next to the numbers", async () => {
    // The badge sits inside the fenced region rather than in a heading far above it, so a crop that
    // includes the numbers includes the warning.
    renderSurface();
    const panel = screen.getByRole("region", { name: /what if/i });
    expect(within(panel).getByText(/hypothetical — not recorded, not scored/i)).toBeInTheDocument();
    expect(within(panel).getByText(/^Hypothetical$/)).toBeInTheDocument();
    expect(within(panel).getByText(/^Actual$/)).toBeInTheDocument();
  });

  it("shows the actual value beside every control it lets you change", async () => {
    renderSurface();
    const panel = screen.getByRole("region", { name: /what if/i });
    expect(within(panel).getAllByText(/^actually/i)).toHaveLength(DETAIL.latest.factors.length);
  });

  it("resets to the actual values and says so", async () => {
    const user = userEvent.setup();
    renderSurface();
    const panel = screen.getByRole("region", { name: /what if/i });

    await user.click(screen.getByLabelText(/home played last night/i));
    expect(within(panel).getByText(/1 factor changed/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /reset/i }));
    expect(within(panel).getByText(/nothing changed yet/i)).toBeInTheDocument();
  });

  it("gives every control a real label a keyboard and a screen reader can reach", async () => {
    renderSurface();
    const panel = screen.getByRole("region", { name: /what if/i });
    const controls = [
      ...within(panel).getAllByRole("checkbox"),
      ...within(panel).getAllByRole("slider"),
    ];
    expect(controls).toHaveLength(DETAIL.latest.factors.length);
    for (const control of controls) {
      expect(control).toHaveAccessibleName();
      control.focus();
      expect(control).toHaveFocus();
    }
  });

  it("announces the hypothetical result politely rather than not at all", () => {
    renderSurface();
    const live = document.querySelector('[aria-live="polite"]');
    expect(live).not.toBeNull();
    expect(live!.textContent).toMatch(/hypothetical/i);
  });
});
