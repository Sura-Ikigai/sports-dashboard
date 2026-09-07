# Modeling cycle 2 — did re-encoding the features buy anything?

> Companion to `PHASE-1-RESULT.md`, which this does not supersede. Phase 1 asked whether four
> pre-game features could predict NBA winners well enough to ship. This asks a narrower and less
> comfortable question: **Phase 1 shipped on one feature (D-031). Does a better-encoded feature set
> change that?**
>
> Every figure below was re-derived on **2026-09-06** by `backend/model/report_v2.py` against the
> ingested corpus, and is checked against `docs/results/t035-figures.json` by
> `backend/tests/test_analysis_figures.py` on every CI run. Nothing here is rounded from memory.

---

## 1. The verdict

**The answer is: partly, and not where it was expected.**

The re-encoded model clears the same paired bar (D-008) on the same sealed 2026 season:

| | measured | bar | |
|---|---|---|---|
| **Accuracy** | **.6800** | ≥ .62 | **MET** |
| **Log loss** | **.6005** | < .6870 (constant .55534 predictor) | **MET** |
| AUC | .7319 | — | |

Sealed model `c97a9b14ddd5`, trained on 5283 games, evaluated on 1322.

Against Phase 1 on that same sealed season, the honest summary is **+.0038 accuracy, −.0015 log
loss, and −.0004 AUC** — which is to say: on the one season neither model ever saw, the two are the
same model to three decimal places.

That is not the whole story, and the rest of it is better. On the two development folds the gain is
real and consistent: **+.0220 and +.0204 accuracy**. And the fold-to-fold standard deviation fell
from **.0169 to .0070** — to **41%** of what it was. The v2 model is not much better at its best.
It is markedly more *stable*, and a model whose accuracy swings half as much between seasons is a
model whose reported number means more.

---

## 2. All three folds

Three expanding-window folds (D-013), each training only on seasons preceding its test season.
2016–2019 are warm-up seasons: they contribute Elo state and never appear as training or test rows
(D-037, T-029).

| fold | train | test | accuracy | log loss | AUC | constant log loss | model |
|---|---:|---:|---:|---:|---:|---:|---|
| train 22–23 → test 2024 | 2643 | 1319 | .6664 | .6100 | .7254 | .6888 | `fa9f0cd95807` |
| train 22–24 → test 2025 | 3962 | 1321 | .6707 | .6051 | .7265 | .6893 | `ce4429120b8b` |
| **train 22–25 → test 2026** *(sealed)* | 5283 | 1322 | **.6800** | **.6005** | **.7319** | .6870 | `c97a9b14ddd5` |

All three clear the bar. Spread **.0136**, standard deviation **.0070**, across **3962** evaluation
games. The corpus behind them is **11854** curated games over **9** seasons.

The trend is still monotonic as the training window grows, and it is still three points. The
increments here (+.0043, +.0093) are smaller than Phase 1's and sit comfortably inside the ±2.6
accuracy points of noise a single season carries — so read it as consistency, not as a growth curve.

---

## 3. Which features carried signal — the same uncomfortable answer, in a better key

Leave-one-out ablation, measured on the **development folds only** (2024 and 2025). Scoring a
feature set on the sealed season would be selection on sealed data, which is the one thing the fold
exists to prevent; `selection._dev_folds` refuses to build a fold set containing it.

Full model: AUC **.7260**, log loss **.6075**, accuracy **.6686**.

| feature dropped | AUC | AUC cost | log loss cost |
|---|---:|---:|---:|
| `elo_diff` | .5991 | **+.1268** | +.0673 |
| `home_b2b` | .7203 | +.0057 | +.0029 |
| `avail_diff` | .7229 | +.0031 | +.0025 |
| `away_b2b` | .7231 | +.0029 | +.0020 |

**Removing Elo costs 43 times what removing the next-best feature costs.** Without it the model
falls to AUC .5991 — barely above a coin flip.

So D-031's finding survives the rewrite: **this is a one-feature model.** What changed is which
feature, and how much the others contribute. In Phase 1 the three companions were *inert* — dropping
them cost essentially nothing. Here each is worth .003 to .006 AUC, above the .0020 materiality
threshold T-030 pre-registered, which is why the ablation kept them. That is a real improvement and
it is a small one: three features earning their place at the margin, around one that does the work.

The signed coefficients on the sealed model, on standardized features, are all correctly directed:

| feature | coefficient | reading |
|---|---:|---|
| `elo_diff` | **+.7124** | a stronger home team wins more |
| `home_b2b` | **−.1510** | the home team on a back-to-back wins less |
| `away_b2b` | **+.1334** | the away team on a back-to-back wins less |
| `avail_diff` | **+.1279** | the healthier team wins more |
| intercept | +.2501 | home-court, not separately identifiable (D-024) |

`home_b2b` and `away_b2b` carry **different magnitudes** (.1510 against .1334). That asymmetry is
the thing D-034 predicted and Phase 1's single `rest_diff` could not express: a tired home team is
penalised more than a tired away team is, and a model with one rest coefficient is forced to average
those into a number that is wrong for both.

---

## 4. Are the probabilities calibrated?

Calibration is half the ship criterion, not a nice-to-have (D-008), because a 55% call and an 80%
call have to mean different things or the product is decorative. On the sealed season:

| predicted | games | mean predicted | observed | gap |
|---|---:|---:|---:|---:|
| [.2,.3) | 104 | .253 | .240 | −.013 |
| [.3,.4) | 153 | .354 | .333 | −.020 |
| [.4,.5) | 209 | .451 | .445 | −.006 |
| [.5,.6) | 244 | .551 | .594 | **+.043** |
| [.6,.7) | 250 | .651 | .648 | −.003 |
| [.7,.8) | 192 | .749 | .776 | +.028 |
| [.8,.9) | 111 | .843 | .811 | −.032 |
| [.9,1.] | 18 | .913 | .944 | +.031 |

*(The two bins below .2 hold 41 games between them and are omitted as noise, not as
inconvenience — at n=2 and n=39 an observed rate carries no information.)*

The largest gap in a well-populated bin is **+.043**, and the direction of the errors is worth
naming: above a coin flip the model is more often **under**confident than over. Grouped into the
bands the product actually displays (D-049):

| band | games | mean confidence | observed accuracy |
|---|---:|---:|---:|
| Toss-up | 234 | .5264 | .5256 |
| Lean | 431 | .5994 | .6288 |
| Clear | 351 | .6991 | .7123 |
| Strong | 306 | .8166 | .8333 |

Every band above a toss-up wins **more** often than it claims. That is the error to have: a surface
that says "Lean" and is right 63% of the time has under-promised, and a visitor who trusts the number
is not misled. The toss-up band is almost exactly honest — .5264 claimed against .5256 observed —
which is the band where being wrong would matter most, because a coin flip dressed as a call is the
one thing a prediction surface must never do.

---

## 5. Where the model failed

**Its worst calls were close games, not blowouts.** The five most confident misses on the sealed
season were predictions of .879 to .900 on the home team. Four of the five finished within nine
points and three within three. The model was not blindsided by a collapse; it was on the wrong side
of games that could have gone either way — which is what a well-calibrated model losing 12% of its
`Strong` calls looks like from the inside.

**It is better at picking home teams than away teams.** On the sealed season it made 815 home calls
at **.6908** and 507 away calls at **.6627** — a **2.8-point** asymmetry. The model is not
symmetric in the thing it is most often asked to do.

**The known availability defect does not show up.** F-141 records that `availability` reads across
the offseason, so for roughly each team's first fifteen games `avail_diff` is computed from a roster
that has partly departed — at a season opener only 54% of the computed rotation is still on the team.
If that cost anything measurable, it would appear as a gap between early-season games and the rest.
It does not:

| | games | accuracy |
|---|---:|---:|
| both teams inside their first 15 games | 218 | .6743 |
| the rest of the season | 1104 | .6812 |

A gap of **.0068** on 218 games, which carries roughly ±3.2 accuracy points of noise at 95%. **This
is a null result and it is reported as one** (story 18). It does not mean the defect is harmless —
`avail_diff` is worth .0031 AUC in total, so there is very little room for its early-season
degradation to show up in an accuracy number at all. It means the defect is *bounded by the
feature's own smallness*, which is a weaker and more honest claim than "it does not matter". The fix
is measured at +.0009 dev AUC and carried to D-017's retrain.

---

## 6. The honest delta against Phase 1

Phase 1's figures are **quoted**, not recomputed. D-032 and D-033 removed the features that produced
them — `point_diff_diff`, `form_diff` and `home_advantage` no longer exist in this codebase — so
reproducing them would mean restoring superseded code. They come from `PHASE-1-RESULT.md` §2.

| fold | Phase 1 accuracy | v2 accuracy | Δ accuracy | Δ log loss | Δ AUC |
|---|---:|---:|---:|---:|---:|
| test 2024 | .6444 | .6664 | **+.0220** | −.0142 | +.0171 |
| test 2025 | .6503 | .6707 | **+.0204** | −.0078 | +.0127 |
| test 2026 *(sealed)* | .6762 | .6800 | **+.0038** | −.0015 | **−.0004** |
| accuracy stdev | .0169 | .0070 | | | |

Read the last two rows together, because they are the finding:

**On the sealed season the two models are indistinguishable.** +.0038 accuracy is about half a game
in 1322, and the AUC actually went *down* by .0004. Anyone hoping the rewrite would move the headline
should read that row and stop.

**On the folds where development happened, v2 is clearly better** — by two accuracy points, twice.
And the variance collapsed to 41% of Phase 1's.

There are two readings of that pattern and honesty requires stating both. The generous one: the
older model got lucky on 2026 and the v2 model is genuinely stronger, with the sealed season
understating it. The unkind one: the dev-fold gains are partly the residue of a cycle spent looking
at those two seasons, and the sealed season — the only one nobody tuned against — is telling the
truth. **The sealed season is the one designed to be believed**, and it says the rewrite bought
stability rather than accuracy.

Both readings agree on the practical conclusion: **the ceiling for pre-game features of this kind is
around .73 AUC**, and three cycles of re-encoding have not moved it. Getting past it needs different
*information* — in-game state, live injury reports, lineup data — not better arithmetic on the same
inputs.

---

## 7. Why these numbers can be believed

Everything Phase 1 §6 claims still holds and is still tested: no leakage, no training on the future,
a corpus verified against pinned counts and content hashes, a measured rather than remembered
baseline, and a standard-library fit checked against scikit-learn. Four things are new.

**The corpus lives in Postgres now (D-038), and that changes the reproducibility claim.** Phase 1
said "regenerates from committed code and the content-pinned data release". That is no longer
sufficient: `run_evaluation` and `report_v2` both require a **running Postgres holding a verified
ingest** (D-043). `PHASE-1-RESULT.md` §6 has been amended to say so. The integrity guarantee did not
weaken — it moved to the ingest boundary, where source bytes are still hashed before parsing, counts
are still pinned, and `corpus.assert_curated` re-runs on every read (D-046).

**The feature set was frozen before the sealed fold was spent.** T-030's ablation was
**pre-registered** — `selection.py`, including its .0020 materiality threshold and its refusal to
build a fold set containing the sealed season, was committed at `165239d` *before it ran*. A
threshold chosen after seeing the result is not a threshold.

**Every sealed evaluation is logged.** `docs/results/t030-sealed-runs.jsonl` records each one.
Because `model_version` is a content hash, two lines carrying the same version are a re-derivation
and two carrying different versions are a second attempt — the file makes the difference legible
without anyone having to remember which run was which. It currently holds **three** entries, all
`c97a9b14ddd5`, all `.6800`: two from T-030's freeze and one from generating this document.

**The prose is checked against the arithmetic.** Every figure here is generated into
`docs/results/t035-figures.json` and checked against this file by
`backend/tests/test_analysis_figures.py`, which runs in CI because it needs no data — only the
committed JSON. Four checks, in three directions:

- every figure in the data appears in the prose;
- every four-decimal number in the prose is accounted for by the data;
- every fold's table row carries **its own** figures, anchored on its unique `model_version`;
- the sealed-fold log holds one model version and it is the one reported.

The third exists because the first two, run together, still let a genuine figure be swapped for
another genuine figure from elsewhere in the document — the forward check finds the original
somewhere, the reverse check finds the substitute accounted for, and both pass. That was found by
sabotage, not by inspection, and the row check is what closed it. Each check is paired with a control
that proves it fires.

What none of them catch is a *sentence* that misreads a correct number. "The model improved" over a
figure that fell is a claim no string match can evaluate, and saying so here is better than implying
a coverage this does not have.

---

## 8. What this does and does not license

**It licenses** running the 2026-27 season on `c97a9b14ddd5` as the genuine trial (D-017), and
displaying its probabilities to a visitor, because they are calibrated well enough that a band label
means what it says.

**It does not license** three things:

1. **Describing this as a four-feature model.** It is an Elo model with three marginal companions.
   The companions are no longer inert — that is the improvement — but dropping Elo costs 43 times
   what dropping any of them does.
2. **Claiming the rewrite improved accuracy.** On the sealed season it did not, measurably. It
   improved *stability*, which is a different and smaller claim.
3. **Expecting the trial to match these numbers.** Every fold here is a backtest on completed
   seasons. The 2026-27 season is the first evaluation the model cannot have been shaped by, and
   `F-141` is a known defect it will run with.

**The open question for the next cycle**, stated plainly: three cycles of feature re-encoding have
converged on ~.73 AUC. The remaining headroom is not in encoding. It is in information the corpus
does not contain — and the one piece of it that can only be gathered *going forward*, never
retroactively, is a season of archived injury reports. That collection has an expiry date, and the
season opens on 2026-10-20.

---

### Provenance

Generated by `backend/model/report_v2.py` on **2026-09-06** against the ingested corpus, with
`--spend-the-sealed-fold`. Fold metrics, coefficients and the calibration table come from
`model.run_evaluation.evaluate_fold` — the same function the evaluation command uses, not a
reimplementation. The ablation comes from `model.selection`, restricted to the development folds.
The failure analysis and the early-season split come from `report_v2` itself. Phase 1's figures are
quoted from `PHASE-1-RESULT.md` and marked as quoted in §6.

Reproducing this requires Postgres (D-043):

```bash
PYTHONPATH=backend python -m model.report_v2 "$DATABASE_URL" \
    --spend-the-sealed-fold --out docs/results/t035-figures.json
```

Nothing in this document is estimated, rounded from memory, or carried over from an earlier run.
