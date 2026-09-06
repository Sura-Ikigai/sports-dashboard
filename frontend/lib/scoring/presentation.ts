/**
 * How to *show* a feature, and how to let someone change it (T-034).
 *
 * ## This map is presentation knowledge, and it is allowed to be incomplete
 *
 * The model's feature set is decided in Python and can change; this file cannot be the thing that
 * decides it. So every lookup has a fallback: an unknown feature still renders, still gets a
 * control, and still says its raw name. `presentation.test.ts` pins that, because the failure mode
 * otherwise is a model update that silently drops a factor from the waterfall -- an *incomplete*
 * decomposition that still looks complete, which is the one thing D-040's exactness must not lose.
 *
 * The fallback's range is derived from the value in hand rather than guessed, so a feature nobody
 * anticipated still gets a slider that can actually move.
 */

export interface FeaturePresentation {
  /** What a person calls it. */
  readonly label: string;
  /** One line on what it measures, shown under the control. */
  readonly description: string;
  /** `binary` renders a checkbox; `continuous` a slider. */
  readonly control: "binary" | "continuous";
  /** How to render the raw value. */
  readonly format: (value: number) => string;
  /** Slider bounds and step. Ignored for binary features. */
  readonly range?: { readonly min: number; readonly max: number; readonly step: number };
}

const decimals = (n: number) => (value: number) =>
  value.toLocaleString(undefined, { minimumFractionDigits: n, maximumFractionDigits: n });

const KNOWN: Record<string, FeaturePresentation> = {
  elo_diff: {
    label: "Elo rating gap",
    description:
      "Home rating minus away rating, carried across seasons. Positive favours the home team.",
    control: "continuous",
    format: (v) => `${v > 0 ? "+" : ""}${decimals(1)(v)}`,
    range: { min: -400, max: 400, step: 1 },
  },
  home_b2b: {
    label: "Home played last night",
    description: "The home team is on the second night of a back-to-back.",
    control: "binary",
    format: (v) => (v >= 0.5 ? "Yes" : "No"),
  },
  away_b2b: {
    label: "Away played last night",
    description: "The away team is on the second night of a back-to-back.",
    control: "binary",
    format: (v) => (v >= 0.5 ? "Yes" : "No"),
  },
  avail_diff: {
    label: "Availability gap",
    description:
      "How much more of its usual rotation the home team has than the away team, as a share.",
    control: "continuous",
    format: (v) => `${v > 0 ? "+" : ""}${decimals(3)(v)}`,
    range: { min: -1, max: 1, step: 0.01 },
  },
};

/**
 * Presentation for a feature, falling back to something usable for one this file has never heard of.
 *
 * `value` is used only by the fallback, to build a range that actually contains the current value --
 * a slider whose bounds exclude where it already sits is worse than no slider.
 */
export function presentationFor(name: string, value: number): FeaturePresentation {
  const known = KNOWN[name];
  if (known) return known;

  const magnitude = Math.max(Math.abs(value) * 2, 1);
  return {
    label: name,
    description: "This model reports a factor this page has no description for.",
    control: "continuous",
    format: (v) => decimals(3)(v),
    range: { min: -magnitude, max: magnitude, step: magnitude / 100 },
  };
}

/** Whether a feature has a hand-written description. Exported so a test can prove the fallback runs. */
export const isKnownFeature = (name: string): boolean => name in KNOWN;

/** The bounds a slider should use, guaranteed to contain `value`. */
export function rangeFor(name: string, value: number): { min: number; max: number; step: number } {
  const presentation = presentationFor(name, value);
  const range = presentation.range ?? { min: 0, max: 1, step: 1 };
  // A real prediction can sit outside a hand-written range -- an Elo gap wider than 400 is unusual
  // but not impossible. Widening beats clamping: clamping would silently redraw the actual value as
  // something it is not, which on this page is the one unforgivable bug.
  return {
    min: Math.min(range.min, value),
    max: Math.max(range.max, value),
    step: range.step,
  };
}
