"use client";

/**
 * The decomposition waterfall (T-034, D-040).
 *
 * ## It is a table that draws bars, not a chart with a table bolted on
 *
 * The usual way to make a chart accessible is to render it and then add a visually-hidden table
 * saying the same thing. Two representations of one dataset drift -- one gets a new row, a rounding
 * change, a reordering -- and the one that drifts is always the one nobody looks at.
 *
 * So there is one structure. Every number lives in a real `<td>`; the bar is a decorative layer
 * inside a cell marked `aria-hidden`, and removing it would cost the visual reading and nothing
 * else. A screen reader gets a proper table with a caption and row headers because that is what the
 * DOM actually is.
 *
 * ## Sign is carried by four channels, only one of which is colour
 *
 * The UI/UX intent says colour must not be the only channel carrying sign, and that direction must
 * read pre-attentively:
 *
 *   1. **Position** -- which side of the zero line the bar sits on. The pre-attentive one.
 *   2. **A direction column** in words: "toward home" / "toward away". What a screen reader reads.
 *   3. **A glyph**, an arrow, in the same cell.
 *   4. **Colour**, last and redundant. Blue and amber rather than green and red, which are the two
 *      the most common colour vision deficiency collapses together.
 *
 * ## The zero line is drawn, labelled, and load-bearing
 *
 * "The chart must carry a zero baseline." Contributions are signed log-odds, so a bar chart without
 * a marked zero is unreadable -- there is no length that means "no effect" unless zero is visible.
 * The line is rendered, given a `0` label, and the bars are positioned from it.
 *
 * ## What the rows are
 *
 * The baseline and the total are rows, not annotations. `baseline + sum(factors) = logit` exactly
 * (D-023 kept the model linear for this), so showing all three makes the arithmetic checkable by a
 * reader rather than asserted at them.
 */

import type { Factor } from "@/lib/api/predictions";
import { presentationFor } from "@/lib/scoring/presentation";

export interface WaterfallRow {
  readonly key: string;
  readonly label: string;
  /** The raw feature value, already formatted. `null` for the baseline and total rows. */
  readonly value: string | null;
  readonly contribution: number;
  readonly kind: "baseline" | "factor" | "total";
}

/**
 * Build the rows. Exported and pure so the ordering and the arithmetic can be tested without
 * rendering anything.
 */
export function waterfallRows(
  baselineLogit: number,
  factors: readonly Factor[],
  logit: number,
): WaterfallRow[] {
  return [
    {
      key: "__baseline",
      label: "Baseline",
      value: null,
      contribution: baselineLogit,
      kind: "baseline",
    },
    ...factors.map((factor): WaterfallRow => {
      const presentation = presentationFor(factor.name, factor.value);
      return {
        key: factor.name,
        label: presentation.label,
        value: presentation.format(factor.value),
        contribution: factor.contribution,
        kind: "factor",
      };
    }),
    { key: "__total", label: "Total", value: null, contribution: logit, kind: "total" },
  ];
}

/** Direction in words. `null` at exactly zero, which is a real state and not a third direction. */
export function directionOf(contribution: number): "home" | "away" | null {
  if (contribution > 0) return "home";
  if (contribution < 0) return "away";
  return null;
}

/**
 * What the Direction column says, which is **not the same claim** for all three kinds of row.
 *
 * A factor pushed the prediction one way. The baseline did not push anything -- it is where the
 * prediction starts before any factor is read, and labelling it "toward home" would invite a reader
 * to count it as a fifth factor about this game when it is a property of every game. The total is a
 * conclusion, not a movement.
 *
 * Rendering all three with one vocabulary is the kind of small dishonesty that survives review
 * because it reads fluently.
 */
function directionLabel(kind: WaterfallRow["kind"], direction: "home" | "away" | null): string {
  if (kind === "baseline") return "starting point";
  if (direction === null) return kind === "total" ? "even" : "no effect";
  if (kind === "total") return direction === "home" ? "favours home" : "favours away";
  return direction === "home" ? "toward home" : "toward away";
}

function formatLogOdds(value: number): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(3)}`;
}

function Bar({ contribution, scale }: { contribution: number; scale: number }) {
  // Percentage of the half-width. `scale` is the largest magnitude in the table, so the longest bar
  // fills its half exactly and every other bar is readable against it.
  const width = scale === 0 ? 0 : (Math.abs(contribution) / scale) * 50;
  const direction = directionOf(contribution);
  return (
    <div className="relative h-4 w-full min-w-[8rem]" aria-hidden="true">
      <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-slate-400 dark:bg-slate-500" />
      {direction && (
        <div
          className={`absolute inset-y-1 rounded-sm ${
            direction === "home"
              ? "left-1/2 bg-sky-600 dark:bg-sky-400"
              : "right-1/2 bg-amber-600 dark:bg-amber-400"
          }`}
          style={{ width: `${width}%` }}
        />
      )}
    </div>
  );
}

export function ContributionWaterfall({
  baselineLogit,
  factors,
  logit,
  caption = "How each factor moved the prediction",
}: {
  baselineLogit: number;
  factors: readonly Factor[];
  logit: number;
  caption?: string;
}) {
  const rows = waterfallRows(baselineLogit, factors, logit);
  const scale = Math.max(...rows.map((r) => Math.abs(r.contribution)), Number.MIN_VALUE);

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="mb-3 text-left text-sm text-slate-600 dark:text-slate-400">
          {caption}. Contributions are in <strong>log-odds</strong>, not percentage points; they sum
          to the total exactly.
        </caption>
        <thead>
          <tr className="border-b border-slate-300 text-left dark:border-slate-700">
            <th scope="col" className="py-2 pr-4 font-semibold">
              Factor
            </th>
            <th scope="col" className="py-2 pr-4 font-semibold">
              Value
            </th>
            <th scope="col" className="py-2 pr-4 font-semibold">
              Direction
            </th>
            <th scope="col" className="py-2 pr-4 text-right font-semibold">
              Contribution
            </th>
            <th scope="col" className="hidden py-2 sm:table-cell">
              <span className="flex justify-between text-xs font-normal text-slate-500">
                <span>&larr; away</span>
                <span aria-hidden="true">0</span>
                <span>home &rarr;</span>
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const direction = directionOf(row.contribution);
            const isTotal = row.kind === "total";
            return (
              <tr
                key={row.key}
                className={
                  isTotal
                    ? "border-t-2 border-slate-400 font-semibold dark:border-slate-600"
                    : "border-b border-slate-200 dark:border-slate-800"
                }
              >
                <th scope="row" className="py-2 pr-4 text-left font-normal">
                  {row.label}
                  {row.kind === "baseline" && (
                    <span className="block text-xs text-slate-500">
                      every factor at its average
                    </span>
                  )}
                </th>
                <td className="py-2 pr-4 tabular-nums">{row.value ?? "—"}</td>
                <td className="py-2 pr-4">
                  {direction && row.kind !== "baseline" ? (
                    <span
                      className={
                        direction === "home"
                          ? "text-sky-700 dark:text-sky-300"
                          : "text-amber-700 dark:text-amber-300"
                      }
                    >
                      <span aria-hidden="true">{direction === "home" ? "▶ " : "◀ "}</span>
                      {directionLabel(row.kind, direction)}
                    </span>
                  ) : (
                    <span className="text-slate-500">{directionLabel(row.kind, direction)}</span>
                  )}
                </td>
                <td className="py-2 pr-4 text-right tabular-nums">
                  {formatLogOdds(row.contribution)}
                </td>
                <td className="hidden py-2 sm:table-cell">
                  <Bar contribution={row.contribution} scale={scale} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
