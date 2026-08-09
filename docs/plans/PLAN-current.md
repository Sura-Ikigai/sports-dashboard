# Plan v1 — NBA Game Prediction: Analytical Core

> **Status:** ACTIVE   ·   **Version:** v1   ·   **Date:** 2026-08-09
> **Supersedes:** none   ·   **Tracker:** docs/IMPLEMENTATION.md
> **Issue:** not filed — the versioned file is the durable output (SYSTEM.md §5.1)

## Problem Statement

The dashboard shows NBA games as they happen but says nothing about what will happen. The goal is a
model that predicts game winners with an honest, defensible accuracy number, and a front end that
surfaces those predictions and tracks whether they were right.

Two things stood in the way, both discovered during planning rather than assumed:

1. **The baseline everyone quotes is wrong for the current era.** Home teams are widely said to win
   ~58% of NBA games. Measured across the last 6,615 completed games, the real figure is **55.56%**,
   and only one of the last five seasons reached 58%. Sampling further back shows why: pre-2020
   seasons averaged **59.3%** home wins, 2021-onward average **55.2%** — a ~4-point structural break
   at the COVID no-crowd seasons that never reverted. A project targeting "beat 58%" would set a bar
   that home-court advantage alone fails to clear in four of five recent seasons, and could discard a
   working model for missing a number that no longer describes the sport.

2. **Prediction systems fail silently, and this one has three specific ways to do it.** Leakage (using
   information that did not exist before tip-off) makes a model look excellent in testing and fail on
   real games. Train/serve skew (features computed one way in training and another in production)
   produces the same outcome with no visible symptom. And a single held-out season of ~1,326 games
   carries roughly ±2.6 points of noise at 95% confidence, so a ship/no-ship decision made on one
   season's accuracy is substantially a coin flip. None of these announce themselves; each has to be
   designed out.

## Solution

A pre-game NBA win-probability model, built as a pure analytical core first and wired into the
existing application second.

**Phase 1 — this plan — builds and evaluates the core, offline, with no changes to the running
application.** It answers the only question that can invalidate everything downstream: can four
pre-game features clear the bar? If they cannot, no migration, scheduled job, or UI has been built for
a model that will not ship.

The model predicts *who wins*, not the spread, and emits a calibrated probability rather than a binary
pick. Success is a paired criterion: **≥62% accuracy on the sealed 2026 fold, and log loss beating a
constant 55.6% predictor.** Calibration is a ship gate, not a nice-to-have, because the product's
central promise is that a 55% call and an 80% call mean genuinely different things.

Later phases (not this plan) persist predictions to the database, serve them through the existing API,
and extend the shipped dashboard with an upcoming-games board, confidence display, live accuracy
tracker, backtest view, and explanation panel.

## User Stories

1. As the model owner, I want a single function that produces a game's features as of a point in time,
   so that training and production cannot diverge.
2. As the model owner, I want that function to physically refuse information dated at or after the
   prediction moment, so that leakage is impossible rather than merely avoided.
3. As the model owner, I want a test proving that adding future games to the input changes nothing, so
   that the leakage guarantee is enforced by CI and not by memory.
4. As the model owner, I want to train only on seasons from one home-court regime, so that the model's
   probabilities are not systematically shifted by an era that no longer exists.
5. As the model owner, I want evaluation folds that each train only on their own past, so that no
   reported number is contaminated by the future.
6. As the model owner, I want performance measured across ~3,900 games rather than 1,326, so that I can
   tell a real result from sampling noise.
7. As the model owner, I want a fold-to-fold spread reported alongside the headline number, so that I
   know how stable the result is before I trust it.
8. As the model owner, I want accuracy compared against the measured 55.56% baseline rather than a
   remembered 58%, so that "beat the baseline" means something true.
9. As the model owner, I want log loss compared against a constant-baseline predictor, so that I can
   tell whether the probabilities carry information or merely the base rate.
10. As the model owner, I want a calibration curve, so that I can see whether a 70% call is right about
    70% of the time.
11. As the model owner, I want AUC reported, so that I know how well the model separates winners from
    losers independent of any threshold.
12. As the model owner, I want early-season games predicted rather than dropped, so that neither the
    evaluation nor the eventual product goes dark for the first three weeks of every season.
13. As the model owner, I want early-season features shrunk toward the league mean, so that a team with
    two games played is not treated as confidently as one with forty.
14. As the model owner, I want a written analysis of which features mattered and where the model failed,
    so that the result is interpretable rather than a number with no story.
15. As the model owner, I want logistic regression first, so that I can read the coefficients and sanity
    check that the model learned something plausible.
16. As the model owner, I want the trained artifact to carry a version identifier, so that a later
    prediction can be traced to the exact model that made it.
17. As a future maintainer, I want the feature logic in one module with one interface, so that adding a
    feature does not mean finding every place features are computed.
18. As a future maintainer, I want fold generation isolated and tested, so that the guarantee about
    training only on the past survives changes to the evaluation harness.
19. As a future maintainer, I want metric computation tested against hand-computed values, so that a
    disappointing headline number is trustworthy rather than possibly a metric bug.
20. As a future maintainer, I want the historical data loaded from a pinned source release, so that
    re-running the evaluation months later reproduces the same numbers.
21. As a reviewer, I want the analytical core reviewable on its own, so that the correctness-critical
    code is gated before any database migration depends on it.
22. As a reviewer, I want the leakage guarantee expressed as a test I can read, so that I can verify the
    claim without re-deriving the feature maths.
23. As the operator, I want the production image to stay free of training-only dependencies, so that
    offline analysis tooling is not deployed to a running service.
24. As the operator, I want the multi-gigabyte historical data to live outside the repository, so that
    it can never be committed.
25. As a visitor (later phase), I want to see this week's matchups with a predicted winner, so that I
    know what the model expects.
26. As a visitor (later phase), I want to see the win probability rather than only a pick, so that I can
    tell a coin-flip game from a likely blowout.
27. As a visitor (later phase), I want to see how the prediction moved as the game approached, so that I
    can see the model reacting to rest and form.
28. As a visitor (later phase), I want a running record of predicted versus actual, so that I can judge
    whether the model is any good.
29. As a visitor (later phase), I want to see backtest performance, so that the model reads as tested
    rather than asserted.
30. As a visitor (later phase), I want an explanation of why a team was favored, so that the prediction
    is a story rather than a black box.

## Implementation Decisions

**Scope of this plan.** Phase 1 only: the analytical core and its offline evaluation. No schema change,
no API change, no UI change, no new runtime dependency in the served image.

**Modules.** One deep module and four thin ones.

- **`features`** — the deep module. Interface: given the completed-game history, a target game, and an
  as-of moment, return the feature mapping. It internally restricts the history to games completed
  strictly before the as-of moment; computes rolling form, rest days, and season-to-date point
  differential; applies shrinkage; expresses every feature as a home-minus-away difference; and emits
  the home indicator. All leakage-relevant logic lives behind this one signature, and it is pure — no
  I/O, no clock, no database.
- **`splits`** — pure generator of expanding-window folds, each pairing a set of training seasons with
  a single later test season.
- **`evaluate`** — pure metric computation: accuracy, log loss, AUC, calibration curve, and comparison
  against a constant base-rate predictor.
- **`loader`** — thin I/O shell converting the pinned upstream season files into the normalized
  completed-game collection the other modules consume.
- **`estimator`** — thin wrapper over the logistic-regression implementation: fit, predict
  probabilities, and serialize a versioned artifact.

**The anti-skew mechanism.** At training time the as-of moment is the target game's own tip-off; at
inference time it is the current moment. The same function is called in both cases. Train/serve skew is
therefore structurally impossible rather than something a test samples for.

**Training window.** Seasons 2022, 2023 and 2024 for training; 2025 for validation and model selection;
2026 sealed as the final fold. This window is chosen to sit entirely within the post-2020 home-court
regime and to exclude the two COVID-affected seasons whose crowd conditions were abnormal. Older
seasons are available back to 2002 and are deliberately not used: training on a 59.3% home-court era
would shift every probability the model emits, which fails the calibration criterion even where
accuracy survives.

**Evaluation protocol.** Three expanding-window folds — train 22-23 test 24, train 22-24 test 25, train
22-25 test 26 — each training only on seasons preceding its test season. This yields roughly 3,900
evaluation games instead of 1,326 and produces a fold-to-fold spread that distinguishes a real result
from noise. The final fold is reported as the headline. Random k-fold cross-validation is explicitly
rejected: it places future games in the training set for past games, which is precisely the leakage
this plan exists to prevent, and it would produce the best-looking and least real numbers available.

**Cold start.** Features are computed from whatever prior games exist and blended toward the
league-average difference with a weight rising as games accumulate — approximately `n/(n+k)` with `k`
around 5, tuned on validation. Opening night therefore falls back to home-court advantage alone rather
than being dropped or imputed with noise. All games are retained.

**Ship criterion.** Accuracy at or above 62% on the sealed fold, *and* log loss beating a constant
55.56% predictor. Both must hold. The measured baseline of 55.56% replaces the 58% figure the original
brief carried.

**Model progression.** Logistic regression only in this plan, chosen for interpretable coefficients.
Gradient boosting is a later upgrade and is out of scope here.

**Dependency placement.** The module lives inside the backend package so that the later scheduled
inference job imports it directly, with no move and no second copy. Dependencies are split: the served
requirements gain only what inference needs, and only when the later phase requires it; a separate
training requirements file carries the analysis and plotting stack and never enters the deployed image.
Phase 1 touches only the training requirements, so the container is genuinely unchanged.

**Data location.** Historical season files are downloaded from a pinned upstream release into an
ignored data directory outside version control. The repository never carries the corpus.

**Decisions carried from planning that constrain later phases** (recorded here so they are not
re-litigated): predictions will be persisted append-only, keyed by game, model version and as-of moment,
written by a scheduled job inside the API service so that the only-DB-writer invariant holds; the
prediction horizon is seven days with a daily append, so the board can show a week while the accuracy
tracker still evaluates the last prediction made before tip-off; and the shipping model will be
retrained on all five seasons before the 2026-27 season opens, making that season the true live
out-of-sample test.

## Testing Decisions

**What makes a good test here.** Assert external behavior — the values a fixture produces and the
properties that must hold — never the internal formula. A test that restates the arithmetic of the
feature computation will pass whether or not the computation is correct, and will have to be rewritten
every time the implementation changes. The tests that matter are the ones that would fail if the module
were subtly wrong in the ways this domain fails.

**`features` — the priority.** Three kinds:
- *Golden fixtures*: a hand-built game history with known values, asserting the feature mapping the
  module produces for specific target games.
- *The leakage property test*: adding any game dated at or after the as-of moment to the input must not
  change the output. This is the single most important test in the plan — it expresses the no-leakage
  guarantee as an executable property rather than a claim, and it catches leakage introduced by any
  future change, not only the ones anticipated today.
- *Shrinkage boundaries*: zero, one, and `k` prior games, confirming the blend behaves at the ends.

**`evaluate`** — metrics asserted against hand-computed values on a small labelled set, plus correct
behavior of the constant-baseline comparator at its boundary. This makes a disappointing headline number
trustworthy rather than possibly a metric bug.

**`splits`** — assertions that every fold's training seasons precede its test season and that the three
expected folds are produced. Small, but it is the second place leakage could enter, and unlike the
feature logic it is not catchable by inspection once the harness is running.

**`loader` and `estimator` — deliberately untested in Phase 1.** This is a recorded choice, not an
oversight. Testing the estimator largely means testing the third-party library's own behavior, and the
loader's real risk is upstream format drift, which a committed fixture cannot detect by construction.
The accepted exposure: a loader defect that silently drops or mis-parses games would corrupt every
downstream number while all three tested modules stay green. That risk is mitigated by asserting
expected game counts per season during the evaluation run — 2026 should yield 1,326 completed games,
2022 should yield 1,324 — which is a cheap and specific tripwire, and by revisiting when the loader is
next modified.

**Prior art.** The existing backend tests establish the pattern: pytest with fixtures, in-memory
isolation, and no dependency on a running database. The gate's own checks are built the same way — pure
fixture-tested cores with thin command-line shells — and are the closest model for the pure/impure split
this plan uses. New tests under the backend package are auto-discovered by the existing gate with no
configuration change.

## Out of Scope

- Any schema change, migration, or new database table. Phase 2.
- The scheduled inference job, prediction persistence, and API routes. Phase 2.
- All front-end work — the upcoming-games board, confidence display, accuracy tracker, backtest view,
  explanation panel, and league switcher. Phase 3.
- Gradient boosting, and any model beyond logistic regression.
- Any league other than the NBA. NFL, college football and college basketball follow only after the NBA
  pipeline is proven end to end, reusing this skeleton.
- Predicting the spread, the total, or anything other than the winner. Beating the betting line is a
  materially harder and separate problem.
- Odds or betting-market data of any kind, including as an evaluation yardstick.
- Player-level features, injury data, and roster information. Team-level pre-game features only.
- Retraining automation and model registries. The Phase 1 artifact is produced by a script that is run
  deliberately.
- Resolving the deferred findings F-014, F-015, F-016 and F-017, which belong to their own triggers.

## Tracker tasks (decomposition — SYSTEM.md §5.1 step 3-4)

- **T-005** Historical data loader — owner: `backend-engineer`
  - acceptance: downloads the 2022-2026 season schedule files from a pinned upstream release tag into
    the ignored data directory; converts them to the normalized completed-game collection; per-season
    completed-game counts match the expected values (2022 → 1,324; 2026 → 1,326); incomplete and
    non-final games are excluded; re-running is idempotent
  - security note: this fetches third-party data over the network into a parsing path. Pin the source
    by release tag rather than a moving branch, verify what was downloaded before parsing it, never
    deserialize with a format that can execute code, and write only inside the ignored data directory.
- **T-006** `features` deep module + tests — owner: `backend-engineer`
  - acceptance: one public interface taking history, target game and as-of moment; computes rolling
    form, rest days, season-to-date point differential and the home indicator as home-minus-away
    differences with `n/(n+k)` shrinkage; the module is pure — no I/O, no clock, no database access;
    golden-fixture tests pass; the leakage property test passes; shrinkage boundary tests at 0, 1 and
    `k` prior games pass
  - security note: the as-of filter is an integrity control, not a convenience — it is what makes every
    number this project reports honest. It must be enforced inside the module, never delegated to
    callers, so that no future call site can opt out of it.
- **T-007** `splits` fold generator + tests — owner: `backend-engineer`
  - acceptance: yields exactly the three expanding-window folds; tests assert every fold's training
    seasons precede its test season and that no season appears in both sides of a fold
  - security note: none beyond the integrity constraint that a fold must never train on its own future.
- **T-008** `evaluate` metrics module + tests — owner: `backend-engineer`
  - acceptance: computes accuracy, log loss, AUC and a calibration curve, plus comparison against a
    constant base-rate predictor; tests assert each metric against hand-computed values on a small
    labelled set and confirm the comparator's boundary behavior
  - security note: none.
- **T-009** `estimator` + walk-forward evaluation run — owner: `backend-engineer`
  - acceptance: fits logistic regression per fold, produces a versioned artifact, and runs the three
    folds end to end; reports per-fold and headline accuracy, log loss and AUC, plus the fold-to-fold
    spread; states plainly whether the ≥62% accuracy and log-loss criteria are both met; training-only
    dependencies are confined to the training requirements file and the served image is unchanged
  - security note: **the serialized model artifact is an arbitrary-code-execution vector.** Loading a
    pickled object executes code inside it, and Phase 2 will load this artifact inside the API service.
    It must be produced and consumed only by this project's own code, loaded only from a trusted local
    path, never fetched from a network location or user-supplied path, and never committed. Pin the new
    numeric and modelling dependencies exactly, consistent with the existing requirements discipline.
- **T-010** Written analysis of the result — owner: `human`
  - acceptance: records which features carried signal (via coefficients and their direction), where the
    model failed, whether the probabilities are calibrated, and how the folds differed; states the
    ship/no-ship verdict against the paired criterion; every number is reproducible from committed code
    and the pinned data release
  - security note: publish nothing that cannot be reproduced from committed code — an unreproducible
    number in a portfolio artifact is a claim that cannot be audited.

## Final review (filled at §5.4 — outcome write-back)

- Built: <pending>
- Changed vs. plan: <pending>
- Future hardening: <pending>
