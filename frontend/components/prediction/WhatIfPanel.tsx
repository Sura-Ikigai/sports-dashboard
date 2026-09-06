"use client";

/**
 * The fenced what-if panel (T-034, D-040, stories 25-27).
 *
 * ## The fence has to survive a screenshot with no surrounding text
 *
 * That is the requirement, and it rules out the obvious design. The obvious design is to let the
 * visitor move a control and watch the *main* waterfall respond -- which is lovely, and which
 * produces a screenshot of what looks exactly like the model's real prediction. So the real
 * prediction on this page never moves. Everything hypothetical is computed and shown **inside this
 * panel**, and the panel is marked in four ways that all survive being cropped out of context:
 *
 *   1. a diagonal-stripe background, which no real panel on this surface has;
 *   2. a dashed border, likewise;
 *   3. a badge reading "HYPOTHETICAL — not recorded, not scored", adjacent to the numbers rather
 *      than in a heading far above them;
 *   4. the actual probability shown right beside the hypothetical one, so the pair is legible as a
 *      comparison rather than as a reading.
 *
 * ## Never persisted, never counted -- and that is structural, not a promise
 *
 * Hypothetical state lives in `useState` and nowhere else. This module contains no `fetch`, no
 * `localStorage`, no `sessionStorage`, and no router write, and `WhatIfPanel.test.tsx` asserts both
 * *behaviourally* -- it drives every control and fails if a single network request is made -- and by
 * scanning this file's source. The behavioural test is the one that would catch a persistence path
 * added three components deep, which a source scan would not.
 *
 * ## Why the scoring happens here at all
 *
 * `lib/scoring/score.ts` is a second implementation of the model's arithmetic, which D-011 forbids
 * and D-041 permits on the condition that T-033's contract gate holds it to the Python one. This
 * panel is the reason that exception was taken: a round trip per slider drag would be slow, and
 * would put a scoring request into a service whose whole security property is that it has no write
 * path. What is recomputed here is the arithmetic over changed **values** -- never the features
 * themselves, which stay a single implementation behind their property test.
 */

import { useMemo, useState } from "react";

import type { Factor, LinearModelResponse } from "./types";
import { decompose, withValues, type LinearModel } from "@/lib/scoring/score";
import { presentationFor, rangeFor } from "@/lib/scoring/presentation";
import { ContributionWaterfall } from "./ContributionWaterfall";

function baseValues(factors: readonly Factor[]): Record<string, number> {
  return Object.fromEntries(factors.map((f) => [f.name, f.value]));
}

function formatPercent(probability: number): string {
  return `${(probability * 100).toFixed(1)}%`;
}

export function WhatIfPanel({
  model,
  factors,
  actualProbability,
}: {
  model: LinearModelResponse;
  factors: readonly Factor[];
  actualProbability: number;
}) {
  const actual = useMemo(() => baseValues(factors), [factors]);
  const [values, setValues] = useState<Record<string, number>>(actual);

  const changed = useMemo(
    () => Object.keys(actual).filter((name) => values[name] !== actual[name]),
    [actual, values],
  );

  const scored = useMemo(() => {
    try {
      return decompose(model as LinearModel, values);
    } catch {
      // A model the API returned in a shape the scorer refuses. The page still renders the real
      // prediction; only the hypothetical half is unavailable, and saying so beats a blank panel.
      return null;
    }
  }, [model, values]);

  const set = (name: string, value: number) =>
    setValues((current) => withValues(current, { [name]: value }));

  const delta = scored ? scored.homeWinProbability - actualProbability : 0;

  return (
    <section
      aria-labelledby="what-if-heading"
      className="rounded-lg border-2 border-dashed border-fuchsia-500 p-4 dark:border-fuchsia-400"
      style={{
        // A repeating gradient rather than an image: it survives a screenshot, needs no asset, and
        // renders identically in both themes at this opacity.
        backgroundImage:
          "repeating-linear-gradient(45deg, rgba(217,70,239,0.07) 0 10px, transparent 10px 20px)",
      }}
    >
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h2 id="what-if-heading" className="text-lg font-semibold">
          What if…
        </h2>
        <span className="rounded bg-fuchsia-600 px-2 py-1 text-xs font-bold tracking-wide text-white">
          HYPOTHETICAL — NOT RECORDED, NOT SCORED
        </span>
      </div>

      <p className="mb-4 max-w-prose text-sm text-slate-700 dark:text-slate-300">
        Change a factor and watch the probability respond. Nothing here is saved, sent anywhere, or
        counted in the model&rsquo;s accuracy record — the prediction above is unaffected.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        {factors.map((factor) => {
          const presentation = presentationFor(factor.name, factor.value);
          const id = `what-if-${factor.name}`;
          const value = values[factor.name];
          const isChanged = value !== factor.value;

          return (
            <div key={factor.name} className="rounded border border-slate-300 bg-white/70 p-3 dark:border-slate-700 dark:bg-slate-900/70">
              <label htmlFor={id} className="block text-sm font-medium">
                {presentation.label}
                {isChanged && (
                  <span className="ml-2 rounded bg-fuchsia-100 px-1.5 py-0.5 text-xs font-semibold text-fuchsia-900 dark:bg-fuchsia-900 dark:text-fuchsia-100">
                    changed
                  </span>
                )}
              </label>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
                {presentation.description}
              </p>

              {presentation.control === "binary" ? (
                <div className="mt-2 flex items-center gap-2">
                  <input
                    id={id}
                    type="checkbox"
                    className="h-4 w-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:focus-visible:outline-sky-400"
                    checked={value >= 0.5}
                    onChange={(event) => set(factor.name, event.target.checked ? 1 : 0)}
                  />
                  <span className="text-sm tabular-nums">{presentation.format(value)}</span>
                </div>
              ) : (
                <div className="mt-2 flex items-center gap-3">
                  <input
                    id={id}
                    type="range"
                    className="w-full focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:focus-visible:outline-sky-400"
                    {...rangeFor(factor.name, factor.value)}
                    value={value}
                    onChange={(event) => set(factor.name, Number(event.target.value))}
                  />
                  <output htmlFor={id} className="w-20 shrink-0 text-right text-sm tabular-nums">
                    {presentation.format(value)}
                  </output>
                </div>
              )}
              <p className="mt-1 text-xs text-slate-500">
                actually <span className="tabular-nums">{presentation.format(factor.value)}</span>
              </p>
            </div>
          );
        })}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => setValues(actual)}
          disabled={changed.length === 0}
          className="rounded border border-slate-400 px-3 py-1.5 text-sm font-medium disabled:opacity-50 dark:border-slate-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:focus-visible:outline-sky-400"
        >
          Reset to the actual values
        </button>
        <span className="text-sm text-slate-600 dark:text-slate-400">
          {changed.length === 0
            ? "Nothing changed yet — this matches the real prediction."
            : `${changed.length} factor${changed.length === 1 ? "" : "s"} changed.`}
        </span>
      </div>

      <div
        aria-live="polite"
        className="mt-4 rounded border-2 border-dashed border-fuchsia-500 bg-white p-4 dark:border-fuchsia-400 dark:bg-slate-950"
      >
        {scored === null ? (
          <p className="text-sm">
            The model parameters could not be used to score a hypothetical. The real prediction above
            is unaffected.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-500">Actual</div>
                <div className="text-2xl font-semibold tabular-nums">
                  {formatPercent(actualProbability)}
                </div>
              </div>
              <div>
                <div className="text-xs font-bold uppercase tracking-wide text-fuchsia-700 dark:text-fuchsia-300">
                  Hypothetical
                </div>
                <div className="text-3xl font-bold tabular-nums text-fuchsia-700 dark:text-fuchsia-300">
                  {formatPercent(scored.homeWinProbability)}
                </div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-500">Difference</div>
                <div className="text-2xl font-semibold tabular-nums">
                  {delta > 0 ? "+" : delta < 0 ? "−" : ""}
                  {Math.abs(delta * 100).toFixed(1)} pts
                </div>
              </div>
            </div>
            <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
              Home win probability under the changed factors. The model never made this prediction.
            </p>

            {changed.length > 0 && (
              <div className="mt-4">
                <ContributionWaterfall
                  baselineLogit={scored.baselineLogit}
                  logit={scored.logit}
                  factors={factors.map((factor) => ({
                    name: factor.name,
                    value: values[factor.name],
                    contribution: scored.contributions[factor.name],
                  }))}
                  caption="How each factor would move a hypothetical prediction"
                />
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
