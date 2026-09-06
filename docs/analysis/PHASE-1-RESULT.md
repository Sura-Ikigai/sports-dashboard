# Phase 1 result — can four pre-game features predict NBA winners?

> **Status:** DRAFT for human review (T-010, owner: `human`) · **Date:** 2026-08-12
> **Verdict:** **SHIP** — both halves of the criterion are met on the sealed fold.
> **Reproduce everything below:** `PYTHONPATH=backend python -m model.run_evaluation`
> Data: sportsdataverse release tag `espn_nba_schedules`, content-pinned by SHA-256 (D-004, F-030).

---

## 1. The verdict

Phase 1 existed to answer one question before any product was built on top of it (D-014): **can four
pre-game features predict NBA game winners well enough to be worth shipping?**

The bar was set deliberately hard and deliberately *paired* (D-008): ≥62% accuracy on a season the
model had never seen, **and** log loss beating a constant base-rate predictor. Either alone is
gameable — a model can hit 65% accuracy while its probabilities are meaningless, and calibration is
the product's whole promise, because a 55% call and an 80% call have to mean different things.

On the sealed 2026 season, untouched during development:

| | measured | bar | |
|---|---|---|---|
| **Accuracy** | **.6762** | ≥ .62 | **MET** |
| **Log loss** | **.6020** | < .6870 (constant .55534 predictor) | **MET** |
| AUC | .7323 | — | |

**Both halves met. The recommendation is to ship the analytical core.**

For scale: always predicting the home team scores **.5552** on the same games. The model adds
**+12.1 accuracy points** over that.

---

## 2. All three folds, not one season

A single season carries roughly ±2.6 accuracy points of noise at 95% confidence, so a 62% gate
decided on one season is substantially decided by chance. That is why the protocol is three
expanding-window folds (D-013), each training only on seasons preceding its test season.

| fold | train | test | accuracy | log loss | AUC | constant log loss | improvement |
|---|---:|---:|---:|---:|---:|---:|---:|
| train 22–23 → test 2024 | 2,643 | 1,319 | .6444 | .6242 | .7083 | .6888 | +.0646 |
| train 22–24 → test 2025 | 3,962 | 1,321 | .6503 | .6129 | .7138 | .6893 | +.0764 |
| **train 22–25 → test 2026** *(sealed)* | 5,283 | 1,322 | **.6762** | **.6020** | **.7323** | .6870 | **+.0850** |

**All three folds clear the 62% bar.** Spread .0318, standard deviation .0169, over **3,962
evaluation games** rather than 1,322 — which is precisely what D-013 bought.

The trend is monotonic in every metric as the training window grows. That is the expected direction
and mild evidence the model is data-limited rather than saturated, but three points are three points:
it is a trend, not a law, and the increments (+.006, +.026) are within the noise a single season
carries.

---

## 3. Which features carried signal — and the uncomfortable answer

Coefficients are on **standardized** features, so magnitudes are directly comparable. All four are
expressed as home-minus-away differences except the home indicator.

| feature | fold 1 | fold 2 | fold 3 (sealed) | direction |
|---|---:|---:|---:|---|
| `point_diff_diff` — season-to-date point differential | +0.5543 | +0.6430 | **+0.6854** | as expected |
| `rest_diff` — days rest, capped at 5 | +0.1604 | +0.1548 | +0.1569 | as expected |
| `form_diff` — shrunk win rate, last 10 games | +0.0455 | +0.0478 | +0.0598 | as expected |
| `home_advantage` — 1.0, or 0.0 at a neutral site | +0.1117 | +0.0030 | +0.0365 | unstable |
| *intercept* | +0.2794 | +0.2615 | +0.2492 | — |

Every sign is the direction basketball says it should be: the better, better-rested, better-formed
team is more likely to win. Nothing here is backwards, which is the first thing a coefficient table
should be checked for.

**But the coefficients understate how lopsided this is.** Removing each feature and refitting on the
sealed fold:

| removed | accuracy | change | AUC |
|---|---:|---:|---:|
| `point_diff_diff` | .6392 | **−.0371** | .6822 |
| `rest_diff` | .6838 | +.0076 | .7356 |
| `home_advantage` | .6785 | +.0023 | .7335 |
| `form_diff` | .6778 | +.0015 | .7320 |

**The model ships on one feature.** Season-to-date point differential carries essentially all of the
signal; removing any of the other three leaves the model *slightly better*. This is recorded as
D-031, and it is the single most important qualification on the verdict.

Two things follow. First, the honest description of this model is "point differential, lightly
adjusted" — not "a four-feature model". Second, the three weak features are candidates for removal,
which would make the served model simpler and easier to explain — but that is a Phase 2 decision, and
this document records the result as it stands rather than a model that was never evaluated.

**`home_advantage` was predicted to be uninformative before the model was ever fitted.** D-024/F-057
noted that after the All-Star exhibition games are excluded, the 2022 season contains *zero*
neutral-site games, leaving fold 1 with 1 neutral game in 2,643 rows — a column that is 1.0 in
2,642 of them is near-perfectly collinear with the intercept, so its coefficient is not identifiable.
The measured coefficients (+0.112, +0.003, +0.037) are exactly the instability that predicts. **Do
not quote a home-court coefficient from this model.** Home-court advantage is real and is in the
intercept; this feature only distinguishes the ~16 neutral-site games in the whole corpus.

---

## 4. Are the probabilities calibrated?

This is half the criterion, not a nicety. Sealed fold, by decile:

| predicted range | n | mean predicted | observed | error |
|---|---:|---:|---:|---:|
| [0.0, 0.1) | 2 | .082 | .000 | −.082 |
| [0.1, 0.2) | 35 | .168 | .171 | +.003 |
| [0.2, 0.3) | 114 | .256 | .202 | −.054 |
| [0.3, 0.4) | 142 | .355 | .310 | −.045 |
| [0.4, 0.5) | 211 | .451 | .469 | +.018 |
| [0.5, 0.6) | 261 | .549 | .575 | +.026 |
| [0.6, 0.7) | 224 | .647 | .638 | −.009 |
| [0.7, 0.8) | 207 | .747 | .792 | +.045 |
| [0.8, 0.9) | 112 | .843 | .830 | −.013 |
| [0.9, 1.0) | 14 | .918 | .857 | −.061 |

**Yes, with a mild and legible bias.** Across the populated middle of the range the model is within
about five points of truth. There is a slight pattern: it is a touch *over*-confident on away-favoured
games (the .2–.4 bins observe fewer home wins than predicted) and a touch *under*-confident on
home-favoured ones (.7–.8 observes more). Net, the log loss beats the constant predictor by .085 per
game, which is the number that decides the criterion.

The two extreme bins hold 16 games between them. Their errors look large and mean very little.

**Confidence means what it says**, which is the product's central claim:

| the model's confidence | share of games | accuracy |
|---|---:|---:|
| coin-flip (\|p−.5\| < .05) | 17.9% | .5212 |
| lean (.05–.15) | 32.8% | .6166 |
| confident (.15–.25) | 26.6% | .7208 |
| **strong (> .25)** | 22.8% | **.8311** |

A monotonic staircase. When the model says a game is close it is right about half the time; when it
commits, it is right five times in six. That is the difference D-008 was written to protect, and it
survives.

---

## 5. Where the model failed

**It rarely commits.** Predictions span .0808 to .9493, and only **12.3%** of games get a call at 80%
confidence or better. Nearly a fifth are effectively coin flips. For a product surface that promises
"who will win", roughly half the schedule will read as *lean home* or *lean away* rather than a pick.
That is an honest limitation of a model built on four team-level averages, not a defect.

**It is weakest early in the season**, exactly where cold start bites:

| period of the sealed season | n | accuracy |
|---|---:|---:|
| first 15% | 198 | .6515 |
| middle 70% | 925 | .6681 |
| last 15% | 199 | .7387 |

An 8.7-point gap between the opening weeks and the closing ones. This vindicates D-015's decision to
*shrink toward the league mean and keep predicting* rather than drop early games: opening-month
accuracy is .6515 — below the model's own average, but still comfortably over the 62% bar and **8.1
points better than always picking home over those same games** (home teams won .5707 of them, which
is the honest comparison; against the season's overall .5552 the gap would read 9.6, and that would
be the wrong baseline). Dropping those games would have cost roughly 16% of every season, including
all of opening month, to avoid a period the model handles acceptably.

**What it cannot see at all.** No injuries, no rest-of-roster information, no travel or altitude, no
lineup data, no market prices. A star ruled out an hour before tip-off does not move this model at
all. Its errors are concentrated where those factors dominate, which is also why 17.9% of games look
like coin flips: for many games, four team-level averages genuinely do not separate the teams.

---

## 6. Why these numbers can be believed

The failure modes for a project like this are silent, so each was designed against and then tested.

**No leakage.** Features are computed strictly from games completed before tip-off, enforced inside
the feature module rather than by callers, and proved three ways: a property test asserting that
adding games dated at or after the as-of moment changes nothing (with a control proving the test is
not vacuous), a re-derivation on 133 real games against a manually past-truncated history, and — the
blunt check — **refitting on shuffled training labels collapses the model to accuracy .5552, exactly
the test-season base rate, with AUC .5494.** A leaking model does not do that.

**No training on the future.** Folds are validated at construction, and the split asserts the *dates*
rather than the season labels: the latest training game must be strictly before the earliest test
game. Season labels are a claim; dates are the fact.

**The corpus is what it claims.** 6,615 completed games verified against per-season pinned counts and
SHA-256 content hashes; **6,605** after removing ten All-Star exhibition games the source mixes in
(F-042). The exclusion is verified, not trusted — every season must retain exactly 30 franchises.

**The baseline is measured, not remembered.** The widely quoted 58% home-court figure is an artifact
of a pre-2020 era: measured here, 2021-onward runs ~55.2% against 59.3% before. This project compares
against **.55534**, the home-win rate of the curated corpus it actually evaluates on (D-007, D-026).

**The arithmetic is checked.** Logistic regression is implemented in the standard library so the
model artifact can be JSON rather than a pickle — Phase 2 loads it inside the API service, and JSON
has no deserialization step that can execute code (D-030). Because the fit is ours, it is verified
against scikit-learn 1.9.0: **maximum coefficient difference 2.1×10⁻⁸**.

**Every number here is reproducible — with two conditions this document originally omitted.**
Artifacts are versioned by content hash, so the same data and configuration always produce the same
`model_version`, and an edited artifact is refused on load. Sealed-fold model: `972d33a83ad9`.

> **Amended 2026-09-06 (T-035).** The sentence this replaces read: *"`PYTHONPATH=backend python -m
> model.run_evaluation` regenerates §1–§4 from committed code and the content-pinned data release."*
> As written it is now false in two separate ways, and both are worth stating rather than quietly
> fixing.
>
> **1. It needs a running Postgres (D-043).** D-038 moved the bulk historical corpus into the
> application database, so "committed code and the content-pinned data release" is no longer
> sufficient input — `run_evaluation` takes a database URL and reads `corpus_*` tables that
> `python -m model.ingest` must have populated first. The integrity guarantee did not weaken; it
> moved to the ingest boundary, where source bytes are still verified against SHA-256 hashes before
> parsing, per-season counts are still pinned, and `corpus.assert_curated` re-runs on every read
> (D-046). What changed is the *setup* a reader needs, and a reproducibility claim that understates
> its own prerequisites is not one.
>
> **2. `run_evaluation` at HEAD no longer produces these figures at all.** D-032 and D-033 replaced
> the feature set: `point_diff_diff`, `form_diff` and `home_advantage` were removed in favour of
> margin-of-victory Elo, split back-to-back indicators and lagged availability. Running the command
> today reproduces **cycle 2's** numbers, not §1–§4's. Reproducing *these* requires checking out
> `30871d8`, the commit this document was published at, and supplying the data release it expected.
>
> §1–§4 are unchanged and were correct when measured. What was wrong was the instruction for
> checking them. The cycle-2 result is in `MODELING-V2-RESULT.md`.

---

## 7. What this does and does not license

**It licenses** building the product surface on this core. The paired criterion is met, on a sealed
season, with calibration good enough that displaying a probability is defensible rather than
decorative.

**It does not license** three things:

1. **Describing this as a four-feature model.** It is a point-differential model with three
   near-inert companions (D-031).
2. **Quoting a home-court coefficient.** Not identifiable here (D-024).
3. **Assuming the numbers hold live.** Every fold is a backtest. The genuine out-of-sample test is
   the 2026-27 season, which is why D-017 retrains on all five seasons before the opener on
   2026-09-30 and treats that season as the real trial.

**The open question for Phase 2**, stated plainly: this result was produced by one feature. Phase 2 as
planned builds persistence, a scheduled job, an API surface and a dashboard on top of it. The
alternative is to spend the next cycle on *features* — Elo, travel, injuries, a properly asymmetric
rest treatment — and build the product surface once there is something richer to serve. The bar is
cleared either way. Which order to take is a product decision, not an analytical one, and this
document does not make it.

---

### Provenance

Every figure above was produced by committed code against the pinned data release. Fold sizes,
per-fold metrics, coefficients and the calibration table come from `model.run_evaluation`. The
ablation, label-shuffle control, confidence stratification and early/late-season split were computed
with the same modules and are recorded in `docs/findings.md` and `docs/decisions.md` (D-031). Nothing
in this document is estimated, rounded from memory, or carried over from an earlier run.
