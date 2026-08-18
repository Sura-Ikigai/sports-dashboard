# Plan v3 — Second-factor features, a Postgres corpus, and an explainable prediction surface

> **Status:** DRAFT   ·   **Version:** v3   ·   **Date:** 2026-08-17
> **Supersedes:** PLAN-v1.md (on activation)   ·   **Tracker:** docs/IMPLEMENTATION.md
> **Issue:** none — deliberately. This file is the only output of the planning session.

> **This is NOT `PLAN-current.md` yet.** PLAN-v1 remains ACTIVE while T-006…T-009 sit `BUILT` and
> unreviewed. This becomes current when Phase 1's review gate closes and the owner marks it ACTIVE.
>
> Numbered **v3**, skipping v2, because this project's tracker already uses "PLAN-v2" to mean the
> *factory's* plan (`Client Projects/Dev-System/docs/plans/PLAN-v2.md`), whose T-011/T-012 execute
> here. A second PLAN-v2 would collide in both the tracker and conversation.

---

## Problem Statement

Phase 1 answered its question and the answer was uncomfortable. The model clears the ship bar —
.6762 accuracy, .6020 log loss, AUC .7323 on a sealed season — but **D-031** established that it
ships on one feature. `point_diff_diff` carries essentially all of the signal; `rest_diff`,
`form_diff` and `home_advantage` are inert.

The owner's instinct was that rest and form were *encoded* badly rather than genuinely uninformative.
A diagnostic against the pinned corpus confirmed half of that and refuted the other half:

- **Rest signal is real and large.** Home on a back-to-back against a rested opponent: **.4393**.
  Away on a back-to-back against a rested home team: **.6438**. A **20.5-point swing** around a
  .5553 baseline, and a mean-margin swing from −0.10 to +3.59.
- **Linearity was not the sin.** The marginal curve on the existing feature is already monotonic
  (.4857 / .5116 / .5577 / .5714 / .6667 across rest_diff −2→+2). Two other things kill it: **mass**
  — 91% of games sit at |rest_diff| ≤ 1 where the effect is nil, so the fit is dragged to the null
  and the 5.9% of games carrying the swing cannot move it — and **cliff placement**: the 0→1 day
  step is +7.1 points while 2→3+ is +1.0, and one slope cannot fit both.
- **The ablation was over-read.** One season carries ±2.6 accuracy points at 95%. The deltas
  (+.0076, +.0023, +.0015) are all inside that band. The honest statement is "these features do
  nothing measurable," not "removing them helps" — which is the difference between *delete them* and
  *the encoding failed to extract known signal*.

A second diagnostic found the deeper problem. `point_diff_diff`, Elo and `form_diff` correlate at
**.87–.92**. They are three measurements of **one latent variable — team strength**. Stacking them is
measuring the same thing three ways, which is why the ablation found nothing, and why 17.9% of games
read as coin flips: when two teams are similarly strong, a model with only a strength factor has
nothing left to say. **Moving AUC requires a second factor, not a better ruler for the first.**

Separately: the product surface does not exist. The frontend renders a flat grid of live scores.
Nothing serves `models/*.json`, there is no predictions endpoint, and a visitor cannot see a
prediction at all, let alone why it was made.

## Solution

Rebuild the feature set around **distinct factors** rather than repeated measurements of strength,
move the corpus into Postgres so the modeling layer can join games to player participation, and build
a game detail surface that shows not just *what* the model predicts but *why* — exploiting the fact
that a linear model's per-feature contributions are additive in log-odds, so a waterfall falls out of
the arithmetic exactly rather than by approximation.

The model freezes before **2026-09-30**, so the 2026-27 season runs as one clean out-of-sample trial
on a single `model_version` rather than a record split across mid-season revisions.

**Measured expectations, stated up front so the result can be judged honestly:** Elo replacing point
differential is worth about **+.008 AUC**; rest re-encoded about **+.005–.015**; travel and altitude
perhaps **+.002**. Availability is the only genuinely new factor and the only real headroom, and its
value is **unknown**. A plausible landing zone is AUC .7323 → ~.745 plus whatever availability
returns. **If availability returns nothing, this plan produces a materially better-explained model
and a marginally better-performing one, and that is a legitimate outcome to report.**

## User Stories

**The model owner**

1. As the model owner, I want Elo to replace season-to-date point differential, so that team strength
   is opponent-adjusted rather than schedule-dependent.
2. As the model owner, I want Elo's margin-of-victory multiplier to dampen blowouts, so that a
   40-point win does not overstate a team's strength.
3. As the model owner, I want Elo ratings to regress toward the mean between seasons, so that roster
   turnover is reflected rather than carried forward intact.
4. As the model owner, I want Elo warmed up on seasons before the training window, so that fold 1
   trains on converged ratings rather than on burn-in noise.
5. As the model owner, I want warm-up seasons used *only* as rating state and never as training rows,
   so that the pre-2020 home-advantage regime never enters the fit.
6. As the model owner, I want rest encoded as a back-to-back indicator plus a bucketed advantage, so
   that the 0-day cliff gets its own coefficient instead of sharing a slope with a flat region.
7. As the model owner, I want separate home and away back-to-back indicators, so that an asymmetric
   effect can be measured rather than assumed symmetric.
8. As the model owner, I want travel distance since each team's previous game, so that a road trip is
   distinguishable from a home stand.
9. As the model owner, I want an altitude indicator for high-elevation venues, so that Denver and
   Salt Lake City are not treated as ordinary road games.
10. As the model owner, I want a lagged team availability measure derived from who actually played in
    prior games, so that a multi-game star absence is visible to the model.
11. As the model owner, I want availability computed strictly from games completed before the
    prediction moment, so that it cannot encode the outcome of the game being predicted.
12. As the model owner, I want zero-minute rows in blowouts not to read as absence, so that garbage
    time does not masquerade as an injury.
13. As the model owner, I want `form_diff` and `home_advantage` removed, so that the model stops
    carrying features that measure nothing and one that was never identifiable.
14. As the model owner, I want every surviving feature to measure a different thing, so that the
    coefficient table stays quotable and user story 30 is answerable.
15. As the model owner, I want feature selection done on 2024 and 2025 only, so that the 2026 season
    is spent once rather than iterated against.
16. As the model owner, I want the 2026 evaluation to happen exactly once after the feature set
    freezes, so that the reported number is not the maximum of many attempts.
17. As the model owner, I want the model frozen before the season opener, so that the live trial runs
    on one version across a full season.
18. As the model owner, I want a null result reported as readily as a positive one, so that the
    project's credibility does not depend on the answer being flattering.

**The visitor**

19. As a visitor, I want to see a predicted win probability for an upcoming game, so that I know who
    the model favors and by how much.
20. As a visitor, I want the probability expressed with a confidence label, so that I can tell a
    coin-flip from a genuine call without interpreting a decimal.
21. As a visitor, I want to see how often the model is right at this confidence level, so that the
    number is grounded in a track record rather than asserted.
22. As a visitor, I want a breakdown of which factors moved the prediction and by how much, so that
    the probability is explained rather than pronounced.
23. As a visitor, I want each factor's contribution shown with its underlying value, so that I can
    see *why* rest contributed what it did, not only that it did.
24. As a visitor, I want factors that pushed toward the home team visually distinct from those that
    pushed away, so that direction reads at a glance.
25. As a visitor, I want to explore hypothetical changes — a star out, a back-to-back — and watch the
    probability respond, so that I can build intuition for the model's behaviour.
26. As a visitor, I want hypothetical values clearly fenced from the real prediction, so that I never
    mistake something I invented for something the model committed to.
27. As a visitor, I want hypothetical exploration never recorded against the model's accuracy, so
    that the track record stays a record of real predictions.
28. As a visitor, I want to see the model version and the moment a prediction was made, so that a
    prediction can be traced to the exact model that made it.
29. As a visitor, I want the surface to work on a phone, so that I can check a game without a desktop.
30. As a visitor, I want the surface to be keyboard-navigable and screen-reader legible, so that the
    explanation is available to me regardless of how I browse.
31. As a visitor, I want a running record of predicted versus actual, so that I can judge the model
    rather than trust it.

**The operator**

32. As the operator, I want the historical corpus in Postgres, so that games, player participation
    and venues can be joined without hand-rolled frame manipulation.
33. As the operator, I want ingestion to be idempotent, so that re-running it repairs rather than
    duplicates.
34. As the operator, I want source bytes verified by content hash before parsing, so that a mutated
    upstream asset is refused rather than absorbed.
35. As the operator, I want per-season counts and the 30-franchise invariant asserted on read, so
    that a mutated table is caught rather than trusted.
36. As the operator, I want schema owned by migrations and data owned by an ingest command, so that
    the migration chain stays reversible and CI does not pay to load a corpus.
37. As the operator, I want the API's database role restricted to `SELECT` on corpus tables, so that
    no unauthenticated write endpoint can reach training data.
38. As the operator, I want predictions persisted append-only and keyed by game, model version and
    as-of moment, so that the prediction history is an audit trail.
39. As the operator, I want a scheduled job appending predictions daily over a seven-day horizon, so
    that the board shows a week while accuracy is still scored against the last pre-tip prediction.
40. As the operator, I want the reproducibility claim amended to state its Postgres dependency, so
    that §6 remains true after the store changes.

## Implementation Decisions

**Decisions carried from the planning session** — recorded in full in `docs/decisions.md` as
**D-032 … D-047**. Summarized here; that file is authoritative.

*Features and evaluation*

- **Margin-of-victory Elo replaces season-to-date point differential** (D-032). K=20, home adjustment
  100, season carryover 0.75. Measured on 2024–2025: AUC .7149 alone against the incumbent's .7073,
  correlation .92. MOV Elo *is* an opponent-adjusted point differential — the same margin information
  with strength of schedule folded in — so keeping both is collinearity for roughly +.002. Elo's
  residual on point differential still ranks at AUC .566, which is what replacement captures and
  addition would double-count.
- **`form_diff` and `home_advantage` are removed** (D-033). Form is a worse ruler for the factor Elo
  now measures. Home advantage was never identifiable (D-024) and lives in the intercept.
- **Rest is re-encoded as `home_b2b`, `away_b2b` and a bucketed `rest_edge`** (D-034), replacing the
  capped linear day difference.
- **Availability is lagged rotation participation** (D-035), derived from player box scores strictly
  before the prediction moment. It catches multi-game absences and cannot catch game-day scratches;
  that limitation is a property of choosing a leakage-free construction and is reported, not hidden.
- **Travel and altitude derive from the venue city** (D-036) already present in the corpus.
- **Seasons 2016–2019 are ingested as Elo warm-up state only** (D-037) and never become training
  rows, keeping the pre-2020 home-advantage regime out of the fit.
- **Feature selection happens on 2024 and 2025.** 2026 is evaluated exactly once, after the feature
  set freezes. 2026-27 is the genuine trial (D-017).

*Store*

- **Bulk history moves into the application Postgres** (D-038), superseding the standing invariant
  that separated the application store from the modeling store. That invariant's entry in the
  tracker's *Architecture snapshot* must be rewritten, not silently dropped.
- **SQL narrows; the feature module filters** (D-039). Queries may reduce rows by season or by team.
  No query anywhere applies an as-of predicate. The as-of filter stays inside the feature module,
  where it is enforced and tested, so no call site can opt out.
- **Migrations own schema; an ingest command owns data** (D-045), idempotent on game id. A data load
  inside a migration has no coherent `downgrade()`, makes CI pay to populate a corpus on every run,
  and puts regenerable derived data into an audit trail meant for irreversible structural change.
- **Integrity is verified at the ingest boundary** (D-046): content hashes over source bytes before
  parsing, pinned per-season counts, and the franchise invariant. The database is trusted after a
  verified ingest, with the curation invariant re-asserted on read.
- **There is no authorization boundary and model results are public** (D-047). Corpus tables are
  `SELECT`-only for the API's database role; the ingest job holds a separate writing role. This is
  grants, not row-level security.
- **The reproducibility claim is amended** (D-043): the pipeline now requires a running Postgres.
  CI provisions a service container for corpus-touching tests (D-044).

*Modules*

- **`elo`** — deep, pure, standard library. One interface returning each game's pre-game rating
  difference for the whole corpus. Hides the MOV multiplier, K, home adjustment and carryover.
  Replaying ~12,000 games is milliseconds, so ratings are recomputed rather than persisted — there is
  no rating state to drift, and no incremental-update path to get wrong.
- **`availability`** — deep, pure, standard library. Hides the rotation definition, minutes weighting
  and participation lookback behind one call.
- **`venues`** — deep, pure, standard library. A static city table plus haversine; exposes travel
  distance, altitude and timezone shift.
- **`features`** — modified. `history` is replaced by a **Context** carrying games, player
  participation and the venue table. The signature stays three arguments, the as-of filter runs once
  inside over every time-varying source, and the module remains standard-library only (D-021/D-016).
- **`store`** — new. Reads Postgres into the record types the pipeline already uses. Narrows by
  season and team; never by as-of.
- **`ingest`** — new. Loader to Postgres, idempotent, verifying at the boundary.
- **`loader`** — modified. Adds warm-up seasons and box-score assets with their own pinned counts and
  content hashes.
- **`splits`** — modified. Asserts that no warm-up season ever appears as a training row.
- **`prediction`** — new deep module. One interface producing probability, feature vector, per-feature
  logit contributions and model version, used by **both** the scheduled job and the API. This extends
  D-011's anti-skew mechanism from training/inference to job/serving.
- **`scorer` (TypeScript)** — new, pure. Reproduces the probability and the contribution decomposition
  in the browser for the what-if panel.

*Surface*

- **The game detail surface is a decomposition waterfall plus a fenced what-if panel** (D-040).
  Hypothetical values are visually distinct, never persisted, and never counted in the accuracy
  record.
- **A gate check pins the TypeScript scorer against the Python scorer** (D-041) across a grid of
  inputs, covering both the probability and the contribution decomposition. The what-if panel is a
  second implementation of the scoring path — precisely what D-011 forbids — so the contract check is
  what makes the invariant mechanical rather than asserted.
- **Predictions are persisted append-only**, keyed by game, model version and as-of moment, appended
  daily over a seven-day horizon, with accuracy scored against the last prediction made before
  tip-off.

*Schedule*

- **Full scope, frozen before 2026-09-30** (D-042). Nothing half-built at the opener.

## Testing Decisions

A good test here pins **external behaviour** — the value a caller observes — not internal structure.
The prior art in this repo is unusually strong and should be followed rather than reinvented: T-006's
leakage property test with its non-vacuity control, T-008's brute-force AUC cross-check over 400
tie-heavy random sets, and T-007's temporal assertion that checks *dates* rather than season labels
because "labels are a claim; dates are the fact."

**Heavyweight treatment — all four areas, per the owner's decision:**

- **`features` + Context — the leakage property test, extended.** The existing 200-trial seeded
  property test grows to inject future **player-box rows** as well as future games, still asserting
  exact equality, and still paired with the control proving a row dated *before* the as-of moment
  **does** change the output — without which a module that ignored its inputs entirely would pass
  perfectly. One test is added that the DB migration makes necessary: **a store query that returns
  future rows must still produce identical feature vectors**, which is what proves D-039 holds in
  practice rather than by convention.
- **`elo` — golden fixtures plus invariants.** Hand-computed ratings over a short game sequence, then
  properties that catch whole classes of bug rather than single cases: total rating is conserved
  under updates, beating a stronger opponent always gains more than beating a weaker one, carryover
  moves every rating toward the mean, replay is deterministic, and the first game of the corpus sees
  both teams at the initial rating.
- **`scorer` ↔ Python contract — in the gate.** Both implementations run over a grid of feature
  vectors; probabilities and per-feature contributions must agree to floating-point tolerance. This
  runs in CI and fails on drift.
- **`availability` and `venues` — behavioural.** Availability: a high-minutes player missing three
  games moves the number; a low-minutes player missing three does not; a zero-minute row in a blowout
  does not read as absence; a season opener with no history returns the prior. Venues: known
  distances, the altitude flag at Denver, and a same-city pair at zero.

**Standard unit coverage** for `ingest` (idempotence — running twice yields one row per game;
refusal of an unpinned season; refusal on hash mismatch), `store` (the narrowing query returns a
superset, never a filtered set), `splits` (a warm-up season appearing as a training row fails), and
the predictions router.

**Frontend:** component tests for the waterfall's ordering and sign treatment, and for the what-if
panel's fencing — specifically that exploring never mutates the displayed real prediction and never
issues a write.

**Environment constraint that has already cost this project a review round (F-037, F-091):** CI
installs the runtime requirements only. A test that silently skips in CI is not a test. Any test
requiring the corpus runs against the CI Postgres service container (D-044), and any test that would
skip must be justified in the task's outcome rather than discovered later.

## Out of Scope

- **Live injury reports.** No historical archive exists, so the feature cannot be trained. Revisit
  once a season of collected snapshots exists — and decide it by measurement against the lagged
  feature, not by assumption.
- **Play-by-play and lineup data.** The corpus supports it; no planned feature needs it.
- **Market prices.** Would likely dominate every feature here and would change what the product
  claims to be.
- **Gradient boosting or any non-linear model class.** The linear model's additive contributions are
  what make the waterfall exact; a model class change would require SHAP to approximate what this
  gives directly. Revisit only if interactions are shown to matter.
- **Spread or total prediction.** The model predicts who wins.
- **Authentication, authorization, and user accounts** (D-047, F-001).
- **Retiring `ci.yml`** (F-007) and the outstanding Playwright/perf gate work.

## Tracker tasks (decomposition)

> Numbering starts at **T-021**: T-013…T-020 are reserved for factory work per the tracker.

- **T-021** Corpus schema migrations + role grants — owner: `backend-engineer`
  - acceptance: migrations create empty tables for historical games, player participation, team box
    and venues, plus the append-only predictions table; the API role holds `SELECT` only on corpus
    tables and the ingest role holds write; `alembic upgrade head` and `downgrade` both succeed on an
    empty database; no migration inserts corpus rows
  - security note: the grant split is the only thing standing between an unauthenticated write
    endpoint and the training corpus. Assert it in a test that connects **as the API role** and
    proves a write is refused — a grant that is never exercised is a grant that is assumed.

- **T-022** Extend the loader: warm-up seasons and box-score assets — owner: `backend-engineer`
  - acceptance: seasons 2016–2019 schedules and 2022–2026 player/team box parquet download with
    per-season pinned counts and content hashes; every new season retains exactly 30 franchises;
    re-running skips valid cached files
  - security note: same posture as T-005 — pin by release tag, verify bytes before parsing, never a
    deserializer that can execute code, write only inside the ignored data directory. Parquet is a
    new format in this path: confirm the reader cannot execute embedded code.
  - **verify against an empty data directory** (F-037). A populated cache has hidden a broken
    download path in this repo before.

- **T-023** `ingest` — verified loader-to-Postgres — owner: `backend-engineer`
  - acceptance: idempotent on game id; running twice yields one row per game; content hashes and
    pinned counts verified before any row is written; an unpinned season is refused; a hash mismatch
    aborts without partial writes
  - security note: this is the boundary where verification moves from files to a database (D-046).
    Everything downstream trusts it, so it must refuse rather than repair.

- **T-024** `store` — Postgres read layer — owner: `backend-engineer`
  - acceptance: returns the record types the pipeline already uses; narrows by season and team;
    **contains no as-of predicate anywhere**; re-asserts the curation invariant on read
  - security note: D-039 is the whole point. Add a check that fails if an as-of-shaped date predicate
    appears in this module — the same class of mechanical enforcement T-006 used for the as-of filter
    and T-009's `ast` check used for argument forwarding.

- **T-025** `elo` deep module — owner: `backend-engineer`
  - acceptance: one interface returning pre-game rating differences for a game sequence; MOV
    multiplier, K, home adjustment and carryover configurable but defaulted to the measured values;
    standard library only; golden fixtures and the five invariants pass
  - security note: none — pure computation. But it consumes warm-up seasons, so it must be impossible
    for a warm-up game to reach the estimator as a training row; that assertion lives in T-029.

- **T-026** `venues` deep module — owner: `backend-engineer`
  - acceptance: static city table covering every venue in the corpus with coordinates and elevation;
    travel distance, altitude and timezone shift; standard library only; a venue absent from the
    table raises rather than defaulting
  - security note: none. A missing venue must raise — a silent zero would read as "no travel."

- **T-027** `availability` deep module — owner: `backend-engineer`
  - acceptance: lagged rotation availability from player participation; behavioural tests pass
    (high-minutes absence moves it, low-minutes absence does not, garbage-time zeroes are not
    absence, no history returns the prior); standard library only
  - security note: this feature is one line of code away from leakage — reading participation for the
    game being predicted rather than prior games. It must take its data through the Context and the
    as-of filter, never query directly.

- **T-028** `features` v2 — Context and the new feature set — owner: `backend-engineer`
  - acceptance: Context carries games, player participation and venues; signature stays three
    arguments; emits `elo_diff`, `home_b2b`, `away_b2b`, `rest_edge`, `avail_diff`, `travel_diff`,
    `altitude`; `point_diff_diff`, `form_diff`, `home_advantage` removed; standard library only; the
    extended leakage property test and its non-vacuity control pass; a store query returning future
    rows produces identical vectors
  - security note: the as-of filter is the integrity control for **every** time-varying source now,
    not just games. One filter, inside the module, applied uniformly — and the property test must
    inject future player rows or it is no longer proving what it claims.

- **T-029** `splits` — warm-up isolation — owner: `backend-engineer`
  - acceptance: warm-up seasons never appear as training or test rows; the existing temporal
    assertion still holds; a warm-up season injected as a training row fails a test
  - security note: integrity only — a fold must never train on its own future, and must never train
    on a different home-advantage regime.

- **T-030** Refit, the single 2026 evaluation, and freeze — owner: `backend-engineer`
  - acceptance: feature selection performed on 2024/2025 only; the feature set frozen and committed
    before 2026 is touched; **exactly one** evaluation run against 2026; per-fold accuracy, log loss
    and AUC reported with the ablation; versioned JSON artifact emitted
  - security note: the artifact stays JSON (D-030) — no deserializer that can execute code.
  - **the discipline is the deliverable.** If the frozen set underperforms, that is the result. A
    second look at 2026 to "check something" spends the fold and must be recorded if it happens.

- **T-031** `prediction` service, persistence, and the scheduled job — owner: `backend-engineer`
  - acceptance: one interface returning probability, feature vector, per-feature logit contributions
    and model version; used by both the job and the API; predictions appended (never updated) keyed
    by game, model version and as-of; daily append over a seven-day horizon
  - security note: the job writes; the API reads. Do not let the API path acquire a write. Also note
    the existing hazard in *Future hardening* — an in-process scheduler double-syncs under a second
    replica.

- **T-032** Predictions API — owner: `backend-engineer`
  - acceptance: a prediction with its decomposition for a given game; upcoming predictions; the
    accuracy record by confidence band; the last prediction before tip-off is the one scored
  - security note: read-only endpoints against a database with no authorization boundary (D-047).
    Return model outputs; do not expose corpus rows wholesale.

- **T-033** TypeScript scorer + contract check — owner: `frontend-engineer`
  - acceptance: pure scorer returning probability and contributions; a gate check runs both
    implementations over a grid and asserts agreement on **both** outputs; the check fails CI when
    either implementation drifts
  - security note: this is a second implementation of the scoring path, which D-011 forbids. The
    contract check is the entire justification — it must run in the gate, not locally.

- **T-034** Game detail surface — owner: `frontend-engineer`
  - acceptance: a route per game showing probability, confidence band with its historical hit rate,
    the contribution waterfall with each factor's underlying value, model version and as-of moment;
    a fenced what-if panel that is visually distinct, never persisted and never counted; responsive;
    keyboard-navigable with a screen-reader-legible alternative to the chart
  - security note: no direct calls to anything but this project's API. Hypothetical state must never
    reach a write endpoint.
  - ui/ux intent for review: direction must read pre-attentively; the fence between real and
    hypothetical must survive a screenshot with no surrounding text; the chart must carry a zero
    baseline; color must not be the only channel carrying sign.

- **T-035** Amend §6 and write the v2 analysis — owner: `human`
  - acceptance: `PHASE-1-RESULT.md` §6's reproducibility claim amended to state the Postgres
    dependency (D-043); a new analysis records which features carried signal, where the model failed,
    whether probabilities remain calibrated, and the honest delta against Phase 1 — including a null
    result on availability if that is what the data says
  - security note: publish nothing that cannot be reproduced from committed code and a verified
    ingest. Hold this document to T-010's standard — every numeric claim re-derived from a fresh run
    and string-matched against the prose.

## Final review (filled at §5.4 — outcome write-back)

- Built: —
- Changed vs. plan: —
- Future hardening: —
