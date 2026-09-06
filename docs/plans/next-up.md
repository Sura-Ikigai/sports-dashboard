# Next up

> Opened 2026-09-06, at the close of `modeling-second-cycle.md` (T-021…T-035, all fifteen shipped).
> This is a parking file, not a plan: items live here until they are either scheduled into a real
> plan file or closed with a reason. Nothing here has been committed to.
>
> Ordered by **when it bites**, not by size.

---

## Before the season opens — 2026-10-20

### 1. The daily job is not scheduled anywhere

`python -m model.job` runs on demand and nothing runs it. The model is frozen, the schedule feed
works, the API serves what the job writes — and if nobody runs the job, every one of those is a
surface with no data behind it, and the first symptom is a dashboard that quietly shows nothing.

**The in-process `AsyncIOScheduler` in `main.py` is the wrong home for it.** It already syncs ESPN
every fifteen minutes, and under a second replica it double-fires. `truncate_to_hour` plus
`ON CONFLICT DO NOTHING` make a double-fire *harmless*, which is not the same as making it correct —
and a job that writes the model's public track record should not be a side effect of the web
process's lifespan.

Options, cheapest first: a host cron calling the module; a container with a `restart: unless-stopped`
loop; a GitHub Actions scheduled workflow (needs the database reachable from CI, which it is not
today). Whichever, it needs **a way to tell that it ran** — a heartbeat row or an alert on "no
prediction written in 26 hours" — because the failure is silent by construction.

**This is the only item on this page that is not optional before 2026-10-20.**

### 2. The injury-snapshot collector — a decision with an expiry date

Raised twice during cycle 2 and still undecided, so it is written down properly here.

The plan puts live injury reports out of scope for a stated reason: *no historical archive exists, so
the feature cannot be trained*. That is true and it is a chicken-and-egg. It can be broken in exactly
one direction: **start archiving now, and the archive exists next cycle.** It cannot be broken later —
a season of injury reports cannot be collected retroactively, and by June the 2026-27 season's are
gone.

What it would cost: a table, a fetch on the same cadence as the schedule refresh, and the discipline
of D-048's structure-and-continuity checks applied to a second live feed. Days, not weeks, and none
of it touches the frozen model or the trial.

What it would buy: `avail_diff` is currently *lagged rotation participation* — who actually played
recently, which is a proxy for who is available. A real injury report is the thing itself, and it is
the one input in reach that the corpus genuinely does not contain (§8 of `MODELING-V2-RESULT.md`
argues the remaining headroom is in information, not encoding).

**Decide before the opener, or decide by default.**

---

## During the trial

### 3. Watch the track record, not the accuracy number

The whole point of D-017 is that 2026-27 is the first evaluation the model cannot have been shaped
by. Two things are worth checking early rather than in April:

- **Calibration by band.** The v2 model is slightly *under*confident above a coin flip on backtests
  (Lean claimed .5994, observed .6288). If that flips to overconfident on live data, the band labels
  are misleading in the direction that matters and the surface should say less.
- **Early-season accuracy.** F-141 predicts availability is degraded for roughly each team's first
  fifteen games. It showed no measurable cost on the sealed season, but that was a backtest with a
  full box-score history; the live path has **no participation data at all** for 2027 until the
  season is under way, which is a strictly worse position than the one that was measured.

### 4. F-142 — the accuracy record is a full-season scan per request

Measured at **59 ms** at end-of-season volume against **3.4 ms** for the endpoint that narrows
properly. Not a problem, recorded because the cost grows with the season and nothing watches it. The
fix if it ever matters is a cached `Record` per model version, invalidated when `predictions` gains a
row.

`revisit-when: a game page exceeds 200 ms, or predictions covers more than one season`.

---

## At D-017's retrain

### 5. F-141 — season-scope the availability lookback

Measured, accepted for the trial, and carried here. Season-scoping is worth **+.0009 dev AUC,
−.0006 log loss, +.0015 accuracy**, and changes `avail_diff` by .0579 on the 523 affected dev games
and **exactly .0000** on the other 2,117. Small, principled, and the reason to take it is that it
measures the roster that exists — not the size of the gain.

### 6. Re-examine the feature set with a season of live data in hand

`MODELING-V2-RESULT.md` §8 is blunt: three cycles of re-encoding have converged on ~.73 AUC and the
remaining headroom is in information the corpus does not contain. Candidates, in the order their
inputs become available:

| candidate | needs | why it might matter |
|---|---|---|
| real injury/availability status | item 2 above, starting now | the current feature is a proxy for the thing |
| rest asymmetry beyond back-to-backs | nothing new | 3+ days rest is not distinguished from 1 |
| opponent-adjusted pace / efficiency | box scores already ingested | Elo compresses everything into one number |
| starting-lineup continuity | box scores already ingested | distinguishes "healthy" from "same five" |
| in-game state | a live feed | out of scope for a pre-game model, listed for completeness |

None of these is committed. The honest position after cycle 2 is that **more features are not
obviously the answer** — the last cycle added three and they are worth .003–.006 AUC each — so
whichever is tried next should be pre-registered the way T-030's ablation was, with a materiality
threshold chosen before it runs.

---

## Carried, no deadline

| item | what | why it is here rather than done |
|---|---|---|
| **F-006** | `docker-compose.prod.yaml` is a **0-byte file** | reads as a deployment story that exists; carried from Phase 1 |
| **F-112** | `run_evaluation.py` has no test of its own | partially addressed by T-030's tests; the transposition case remains |
| **F-125** | `splits.py`'s temporal-leak guard, mutation-confirmed | the guard is real; the finding is about its test |
| **F-110** | `save_artifact` containment | closed in substance (F-119 added the tests); the finding text predates that |
| **F-057** | corrects D-024 on home advantage | a decision correction, not a code change |
| **F-114** | estimator half of an 8-item test batch | loader half closed in T-022 |
| **F-126** | `estimator.py` error paths untested | 3-item batch, low |
| **F-016** | `.gitignore`'s `models/` was unanchored | fixed in substance; listed for the audit trail |

---

## Not doing, and why

- **A second model class.** D-023 keeps the model linear so contributions are exact arithmetic rather
  than SHAP estimates — that is what makes D-040's waterfall a decomposition. A tree model would
  trade the explanation surface for a fraction of a point, and the explanation is the product.
- **Retraining mid-season.** D-042 froze the model before the opener specifically so the trial runs
  on one version across a full season. A mid-season retrain would be *recorded* (predictions are keyed
  by `model_version`) rather than hidden, but it would end the trial.
- **Auth.** D-047 is deliberate: model outputs are public, every reader is equivalent, and the grant
  split is what protects the corpus. This changes the day there is user-scoped data, and F-001 fires
  on `first-user-scoped-data`.
