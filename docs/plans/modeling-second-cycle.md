# Plan v3 — Second-factor features, a Postgres corpus, and an explainable prediction surface

> **Status:** **ACTIVE** as of 2026-09-04 — Phase 1 merged (PR #3, `30871d8`, 2026-08-25), which was
> this plan's activation condition. Building on `feat/modeling-second-cycle`. Progress is tracked in
> the *Tracker tasks* checkboxes below.
> **Date:** 2026-08-17 (grill-me, 16 decisions D-032..D-047)   ·   **Supersedes:** the archived PLAN-v1.
>
> Reformatted 2026-08-24 for one-file-per-feature. Scope below (T-021..T-035, 40 user stories) is
> unchanged; the tracker and `PLAN-current.md` pointers it referenced no longer exist — this file is
> its own status. Decisions D-001..D-047 are in `docs/archive/decisions.md`.

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

- [x] **T-021** Corpus schema migrations + role grants — owner: `backend-engineer` — **DONE 2026-09-04**
  - acceptance: migrations create empty tables for historical games, player participation, team box
    and venues, plus the append-only predictions table; the API role holds `SELECT` only on corpus
    tables and the ingest role holds write; `alembic upgrade head` and `downgrade` both succeed on an
    empty database; no migration inserts corpus rows
  - security note: the grant split is the only thing standing between an unauthenticated write
    endpoint and the training corpus. Assert it in a test that connects **as the API role** and
    proves a write is refused — a grant that is never exercised is a grant that is assumed.
  - **built:** `backend/alembic/versions/a1c4f7e29b30_corpus_schema_and_role_grants.py` and
    `backend/tests/test_corpus_schema.py` (26 tests). Verified against Postgres 16: 217 backend
    tests pass, ruff clean. Every refusal below was asserted by **connecting as the role** and
    reading the SQLSTATE (`42501`) back, not by inspecting `information_schema`.
  - **changed vs. plan — three decisions worth recording:**
    1. **Three roles, not two.** D-047 names an API role and an ingest role. T-031 then requires
       "the job writes; the API reads," and running the prediction job as `sports_ingest` would
       hand it write authority over the training corpus it has no business touching. So:
       `sports_api` (SELECT on corpus + predictions, DML on the app tables), `sports_ingest`
       (DML on corpus, **no reach into predictions**), `sports_job` (SELECT on corpus, SELECT +
       INSERT on predictions).
    2. **Append-only is a grant, not a trigger.** `sports_job` holds INSERT without UPDATE or
       DELETE, so the process that writes the track record cannot revise it. Consistent with
       D-047's "grants, not row-level security"; no trigger to bypass and nothing to keep in sync.
    3. **Schema and grants ship in one migration.** Splitting them would leave a revision at which
       the corpus tables exist with no authorization boundary — the exact window the boundary
       exists to prevent.
  - **also worth knowing:**
    - No SQLAlchemy ORM models were added. The corpus stays out of `Base.metadata` so
      `conftest.py`'s `create_all` can never conjure a corpus table in SQLite and let a test pass
      against a shape no migration produced. `store` (T-024) reads it with SQL.
    - `predictions` has **no** foreign key to `games`. It is an audit trail; a routine ESPN
      resync must not be able to erase the model's track record.
    - `corpus_games` carries CHECK constraints for the tie and same-team cases, mirroring
      `Game.__post_init__` (F-049) at the storage layer.
    - Warm-up seasons (D-037) are distinguished by `season` alone — no `is_warmup` column, which
      would be a second source of truth able to disagree with the first.
    - **CI already provisions the Postgres service container** D-044 called for, and the three
      `DB_*` secrets exist. These are the first tests in the repo that actually use it — before
      today the container ran every build and nothing connected to it.
  - **the skip guard is asymmetric on purpose** (F-037, F-091): no Postgres locally is a skip with
    instructions; no Postgres under `CI=true` is a **failure**. Verified in all three modes —
    with Postgres (26 pass), without (26 skip), and `CI=true` without (26 error).

- [x] **T-022** Extend the loader: warm-up seasons and box-score assets — owner: `backend-engineer`
      — **DONE 2026-09-04**
  - acceptance: seasons 2016–2019 schedules and 2022–2026 player/team box parquet download with
    per-season pinned counts and content hashes; every new season retains exactly 30 franchises;
    re-running skips valid cached files
  - security note: same posture as T-005 — pin by release tag, verify bytes before parsing, never a
    deserializer that can execute code, write only inside the ignored data directory. Parquet is a
    new format in this path: confirm the reader cannot execute embedded code.
  - **verify against an empty data directory** (F-037). A populated cache has hidden a broken
    download path in this repo before.
  - **built:** `backend/model/loader.py` extended; `backend/tests/test_loader.py` (**88 tests**, up
    from zero — F-111 discharged first, before the parquet path, as that finding required).
    305 backend tests pass, ruff clean.
  - **pins recorded 2026-09-04 from live downloads.** Warm-up schedules 2016–2019 (1317/1310/1314/
    1314 completed) and player/team box parquet for 2022–2026, each with a content hash. All ten box
    files verify; warm-up totals 5,255 games; `load_completed_games()` still returns exactly the
    same 6,615 modeling games as Phase 1.
  - **two cross-asset invariants held exactly and are now asserted, not just noted:**
    1. Box `game_id` coverage **equals** `EXPECTED_COMPLETED_COUNTS` for the same season, for both
       families, all five seasons. This is the check that earns its keep: a file covering the wrong
       *set* of games at the right size passes every per-file check ever written, because it hashes
       to whatever it now contains and its row count is whatever was pinned from it.
    2. `team_box` rows == 2 × games. A row count and a game count can both be right while one game
       carries three rows and another carries one.
  - **30 franchises confirmed for every warm-up season** — via the same games-played rule
    `corpus.py` already applies, so no new curation logic was needed. 2016/2017 carry 2 All-Star
    phantom ids, 2018/2019 carry 4; identical in shape to what Phase 1 already handles.
  - **parquet security note — confirmed, not assumed.** Parquet is a columnar data format read
    through pyarrow: no code, no callables, no import directives, and no `pickle.loads` analogue.
    The one real subtlety is that pandas honours an *extension dtype* declared in the file's
    key-value footer — a registry lookup, not an arbitrary import, and every file reaching the
    reader has already matched a pinned SHA-256 over its exact bytes. Still a parser, never a
    deserializer that can execute code. Framing is checked (`PAR1` at both ends) before the reader
    is pointed at the file; the *schema* check necessarily happens after parsing, since parquet
    keeps its schema in a binary footer rather than a text header.
  - **F-037 discharged by live test, not by habit:** all three families downloaded into a directory
    that did not exist, then re-run to confirm the cache path is a no-op.
  - **changed vs. plan:**
    - The download core (`_download_verified`) was **extracted and shared** rather than copied for
      the box families. A second implementation of the allowlist, TLS assertion, size cap, cache
      verification and atomic write is exactly how the two would drift apart; tests assert the
      shared posture applies on the new path.
    - `WARMUP_SEASONS` is a **separate tuple** from `SEASONS`, and `load_warmup_games()` a separate
      function from `load_completed_games()`. Being pinned is not the same as being trainable — the
      training path's default must be structurally incapable of returning a warm-up row. T-029 still
      enforces this at the split boundary; this is the ergonomic half.
    - `pyarrow==25.0.1` added to `requirements-train.txt` (not `requirements.txt` — the served image
      is untouched, D-016 intact).
  - **also landed here (see `phase-1-analytical-core.md`):** F-111 closed, F-039 closed (was already
    fixed, carried forward unclosed), F-114's loader half closed. `ci.yml` now installs
    `requirements-train.txt`, so every `backend/model/` test actually runs in CI instead of
    `importorskip`-ing away — these were green there without ever executing.

- [x] **T-023** `ingest` — verified loader-to-Postgres — owner: `backend-engineer` — **DONE 2026-09-04**
  - acceptance: idempotent on game id; running twice yields one row per game; content hashes and
    pinned counts verified before any row is written; an unpinned season is refused; a hash mismatch
    aborts without partial writes
  - security note: this is the boundary where verification moves from files to a database (D-046).
    Everything downstream trusts it, so it must refuse rather than repair.
  - **built:** `backend/model/ingest.py` and `backend/tests/test_ingest.py` (22 tests).
    **332 backend tests pass**, ruff clean.
  - **the live corpus is in Postgres.** All nine seasons ingested and verified in SQL:

    | table | rows |
    |---|---|
    | `corpus_games` | **11,870** (5,255 warm-up + 6,615 modeling) |
    | `corpus_player_box` | **173,065** |
    | `corpus_team_box` | **13,230** |
    | `corpus_venues` | **45** |

    Every season's game count matches `EXPECTED_COMPLETED_COUNTS` exactly. No orphan box rows,
    every game carries exactly two `team_box` rows, no null venues. 16 exhibitions identified
    (10 modeling + 6 warm-up), written and excluded on read.
  - **"a hash mismatch aborts without partial writes" is stronger than it sounds, and the ordering
    is why.** The content hashes are over *source bytes*; by the time rows exist those bytes are
    gone, so ingest cannot re-check them and does not try. Instead `loader` raises while the file is
    still a file — before the first INSERT, not partway through. The transaction covers only the
    remaining failure mode: a database error on the fourth table leaving the first three populated,
    which every count check would happily pass because each table is internally consistent. There is
    a test for exactly that.
  - **found by running it: 33 player rows have a null `athlete_id`.** All in 2026, all one team, no
    display name, no minutes, captioned "COACH'S DECISION" — a roster slot with no player attached.
    A full audit of every key column across all ten box files found nothing else.
    - Dropping a row **is** a repair, and this module's contract is to refuse rather than repair.
      The reconciliation is that the repair is **pinned**: `UNIDENTIFIED_PLAYER_ROWS` declares the
      exact count per season, and drift **in either direction** is refused. The known 33 pass; a
      34th, or a new one in 2024, stops the ingest. A season absent from the dict is refused rather
      than assumed clean.
    - It deliberately does **not** change `PLAYER_BOX_EXPECTED_ROWS`. That pin verifies the *file*
      as published; this one governs what the *corpus* can hold. Two questions, two numbers.
  - **US-12 survives the boundary**, confirmed in SQL on the real data: 31,769 rows with null
    minutes, 700 active rows at exactly zero minutes, and **zero** rows that are `did_not_play` with
    minutes present. Garbage time and absence stay distinguishable, which is what T-027 needs.
  - **changed vs. plan / found during the build:**
    1. **T-021's `downgrade` had a real bug, fixed here.** Roles are cluster-scoped, so a second
       migrated database in the same cluster makes `DROP ROLE` fail — and `alembic downgrade` then
       fails outright. Not hypothetical: it is what the test suite does, and it is how this was
       found. Neither extreme is acceptable (aborting means the migration is not reversible; forcing
       the drop means one database's downgrade silently breaks another), so the downgrade now drops
       the role when nothing depends on it and `RAISE NOTICE`s when something does. The round-trip
       test was over-specified too — it now asserts *this database's grants* are revoked, which is
       the actual scope of what the migration owns.
    2. **The loader now carries five `venue_*` columns**, required rather than optional:
       `corpus_venues` is the join from a game to a city and T-026's travel/altitude features have
       no fallback — a silent zero would read as "no travel". Verified present in all nine pinned
       seasons; `venue_id` and city never blank, `state` blank only for international games, which
       is why that column is nullable and city is not.
    3. **The Postgres skip-guard moved to `backend/tests/conftest.py`** and is now shared with
       `test_corpus_schema.py`. Two copies of a safety guard is two guards that can drift, and the
       whole point of this one is that it must not quietly stop firing.
    4. **No ORM models**, consistent with T-021: the corpus tables are **reflected** from the live
       database. Re-declaring their columns would reintroduce the second source of truth T-021 kept
       out of `Base.metadata`. An unmigrated database gets a refusal naming `alembic upgrade head`,
       not a stack trace from a missing relation.
    5. Exhibitions are **identified at ingest and excluded on read**, not filtered on write — the
       pinned counts count them (6,615, not the curated 6,605), so writing the curated set would
       make the database's own row count disagree with the number that verifies it.

- [x] **T-024** `store` — Postgres read layer — owner: `backend-engineer` — **DONE 2026-09-04**
  - acceptance: returns the record types the pipeline already uses; narrows by season and team;
    **contains no as-of predicate anywhere**; re-asserts the curation invariant on read
  - security note: D-039 is the whole point. Add a check that fails if an as-of-shaped date predicate
    appears in this module — the same class of mechanical enforcement T-006 used for the as-of filter
    and T-009's `ast` check used for argument forwarding.
  - **built:** `backend/model/store.py` and `backend/tests/test_store.py` (32 tests).
    **364 backend tests pass**, ruff clean.
  - **the round trip is exact.** Read back from Postgres and curated, the modeling seasons give
    **6,605** games — the same number Phase 1 reported from CSVs. 11,854 curated of 11,870 raw
    (16 exhibitions), warm-up 5,249 of 5,255. The corpus survived the move to a database unchanged.
  - **D-039 is enforced mechanically, and the enforcement was proven to fail.**
    `test_no_as_of_predicate_appears_anywhere_in_store` parses `store.py`'s AST and refuses any
    date-shaped inequality, `as_of` mention, or `BETWEEN` in any non-docstring string literal.
    Verified by smuggling `clauses.append("game_date < :as_of")` into the module: the test fails.
    Three deliberate details:
    - It scans **every non-docstring literal**, not just `sa.text(...)` arguments — this module
      builds WHERE clauses by appending fragments to a list, so a predicate can reach the SQL
      without ever appearing inside a `text()` call.
    - Docstrings are **excluded**, so prose can name the hazard. The module docstring quotes
      `WHERE game_date < :as_of` as the thing to avoid; a checker that forbade that would get the
      explanation deleted to appease it.
    - Both halves of non-vacuity are tested: five predicate shapes that **must** be caught, and five
      legitimate narrowings (`season = ANY(...)`, `ORDER BY g.game_date`, an equality join) that
      must **not** be — a check that flagged everything would force D-039's permitted narrowings to
      be written evasively.
  - **the curation trap, found while building it.** `corpus.partition_exhibitions` identifies a
    phantom team by how few games it plays. In a **team-narrowed** slice every *opponent* appears
    two or three times, so run naively over the slice it classifies nearly the whole thing as
    exhibition. Identification therefore always runs over the **full** corpus and only the exclusion
    is applied to the narrowed rows; the 30-franchises-per-season assertion is skipped for
    team-narrowed reads (a slice of one team cannot have thirty) and runs in full otherwise.
  - **`load_history` narrows by team only, and cannot express a season.** F-113's `_require_covers`
    refuses any non-`None` `complete_from`, because `rest_diff` is not season-scoped and `elo_diff`
    (D-032) is running state over every prior season — so a season-narrowed history is unusable for
    features however it is chosen. `load_games` still narrows by season for the callers that
    legitimately want one (fold construction, reporting); the function `compute_features` consumes
    simply has no such parameter. A test pins that signature.
  - **changed vs. plan:**
    1. **Warm-up exhibition counts are now pinned** in `corpus.EXPECTED_EXHIBITION_COUNTS`
       (2016: 1, 2017: 1, 2018: 2, 2019: 2; total 10 → 16), measured against the ingested corpus.
       `exclude_exhibitions` refuses an unpinned season outright, and `store` now reads these
       seasons. `test_corpus.py`'s constant-guard test did its job and was updated deliberately.
    2. `store` adds `assert_corpus_present` — an empty or partially ingested corpus produces
       shrinkage priors rather than an error, which is the same silent failure `Coverage` guards
       one layer up.
    3. `game_venue_ids` is a separate mapping rather than a field on `Game`: `Game` mirrors the
       loader's frame one-for-one, and adding a field would give one record type two shapes
       depending on which side of the store it came from.
    4. An **empty** narrowing (`teams=[]`) is refused rather than silently widened — under a naive
       `if teams:` it reads as "no narrowing" and returns the whole corpus.
  - **also closed here:** **F-113**, which was flagged `revisit-when: before-T-024`. Checking first
    rather than building on the assumption, the remediation had already landed in the round-3 fixes;
    verified and closed, and now exercised end-to-end through `store.load_history`.

- [x] **T-025** `elo` deep module — owner: `backend-engineer` — **DONE 2026-09-04**
  - acceptance: one interface returning pre-game rating differences for a game sequence; MOV
    multiplier, K, home adjustment and carryover configurable but defaulted to the measured values;
    standard library only; golden fixtures and the five invariants pass
  - security note: none — pure computation. But it consumes warm-up seasons, so it must be impossible
    for a warm-up game to reach the estimator as a training row; that assertion lives in T-029.
  - **built:** `backend/model/elo.py` and `backend/tests/test_elo.py` (32 tests).
    **396 backend tests pass**, ruff clean.
  - **D-032's measurement reproduced exactly.** Replayed over the full ingested corpus (11,854
    curated games, warm-up included), MOV Elo scores **AUC .7149** on the 2024+2025 dev seasons —
    the number D-032 recorded — over **2,640** games, also exactly the count D-032 names. An
    independent reimplementation landing on the same four decimals validates the formula, the
    Postgres round trip, the curation and the warm-up replay in one shot.
  - **the emitted feature excludes the home adjustment, and that is a correctness requirement.**
    The 100-point home adjustment belongs in the expected-score calculation, where it stops home
    wins from inflating ratings. Adding it to the emitted `elo_diff` would produce a column
    differing from the unadjusted one by a constant — **perfectly collinear with the intercept**,
    which is exactly the non-identifiability D-024 documented and D-033 removed `home_advantage`
    over. Home-court advantage stays in the intercept. There is a test for it.
  - **the five invariants, each catching a class of bug a fixture cannot:**
    1. total rating conserved (catches an asymmetric update — ratings inflate over a season,
       invisible in any single game);
    2. beating a stronger opponent gains more, a bigger margin gains more, and an upset gains more
       than an expected win of the same margin (the last one is the autocorrelation correction);
    3. carryover moves every rating toward the mean, with `carryover=1.0` a genuine no-op and
       `0.0` a full reset;
    4. replay is deterministic **and order-independent** — games are sorted internally by
       `(date, game_id)`, because dates are the fact and a caller's ordering is a claim (T-007);
    5. the first game sees both teams at 1500, and so does a team appearing mid-corpus.
  - **golden fixtures are checked against an independent implementation**, `_naive_elo`, written
    straight from D-032 rather than imported from the module under test — plus literal values
    pinned to 1e-9. Asserting the module equals itself would be no test at all.
  - **decision needing your confirmation — the 2019 → 2022 gap.** The corpus has no 2020 or 2021
    season, so replaying warm-up plus modeling crosses a three-year hole. The default applies
    carryover **once per observed season transition** (textbook, and how D-032's 0.75 was measured);
    `regress_per_elapsed_year=True` instead regresses once per calendar year (0.75³ ≈ 0.42 across
    the gap), on the view that three unobserved seasons decay a rating more than one does. **No
    measurement supports either choice.** Left explicit rather than buried, and a test pins that the
    two actually differ so the option is not decorative. By 2022 — the first training season — the
    warm-up has done its job under either setting.
  - **also guarded:** a season label that disagrees with its date is refused (T-007's lesson — the
    carryover would otherwise fire at the wrong moments); a generator is refused (F-045's shape —
    it would be consumed by the sort and replay nothing); the MOV denominator is floored, because
    under the defaults it reaches zero at a −1250 winner advantage and **inverts** past it, which
    would move the winner's rating down.

- [x] **T-026** `venues` deep module — owner: `backend-engineer` — **DONE 2026-09-04**
  - acceptance: static city table covering every venue in the corpus with coordinates and elevation;
    travel distance, altitude and timezone shift; standard library only; a venue absent from the
    table raises rather than defaulting
  - security note: none. A missing venue must raise — a silent zero would read as "no travel."
  - **built:** `backend/model/venues.py` and `backend/tests/test_venues.py` (38 tests).
    **434 backend tests pass**, ruff clean.
  - **coverage verified end-to-end against the live corpus:** all **45 venues** across **37 distinct
    `(city, state)` pairs** resolve to a known city. The pairs are pinned in the test so coverage is
    asserted in CI, which has no corpus to read — the same discipline as the loader's counts.
  - **keyed by city, not by venue.** Arenas are renamed constantly (the corpus holds
    `crypto.com Arena`, `Rocket Arena`, `Mortgage Matchup Center` — all recent renames), and a team
    can move buildings within a city without moving at all. The venue → city mapping already lives
    in `corpus_venues`; this module maps city → geography. **Both halves of the key matter:**
    "Portland" is Oregon here and Maine elsewhere, and a name-only table would put the two ~2,500
    miles apart silently.
  - **a missing city raises, and that is the whole security note.** A silent zero reads as *"no
    travel"* — the strongest possible signal for a home stand — and it would be produced for exactly
    the games this feature exists to describe: London, Paris, Berlin and Mexico City, the longest
    trips in the corpus. The error message names the fix (coordinates, elevation, IANA timezone) and
    says explicitly not to let it default; a test asserts the message still says both.
  - **the altitude threshold sits in an empty gap, not at a round number.** High-elevation cities
    are Mexico City (7,350 ft), Denver (5,280) and Salt Lake City (4,226); the next one down is Las
    Vegas at 2,001. Nothing sits between, so any threshold in that band separates the populations
    identically and none is near an edge — the same reasoning
    `corpus.MIN_SEASON_GAMES_FOR_A_REAL_TEAM` uses, carrying the same warning against adjusting it
    to make something pass. In 2026 this flags **86 games**: 44 Denver, 41 Salt Lake City, 1 Mexico
    City.
  - **timezone shift is a computation, not a lookup, because of Arizona.** Phoenix does not observe
    DST, so Phoenix → Denver is **0 hours in January and 1 hour in June** — and an NBA season spans
    both. A fixed per-city offset would be wrong for half of it. `timezone_shift_hours` therefore
    requires the moment, and requires it timezone-aware.
  - **sanity checks that landed:** LA→NY 2,447 mi, Boston→NY 188, Portland→Miami 2,704,
    Brooklyn→Manhattan 4.8; longest pair in the corpus is Mexico City → Berlin at 6,046 mi.
    Distance is symmetric and satisfies the triangle inequality (a property, not a case — it fails
    for a whole class of coordinate errors individual distances survive).
  - **expectations, per D-036:** worth roughly **+.002 AUC**. Included because it is nearly free
    given D-034's work, not because it is expected to matter on its own. That is the standard to
    judge it against when T-030 reports.

- [x] **T-027** `availability` deep module — owner: `backend-engineer` — **DONE 2026-09-04**
  - acceptance: lagged rotation availability from player participation; behavioural tests pass
    (high-minutes absence moves it, low-minutes absence does not, garbage-time zeroes are not
    absence, no history returns the prior); standard library only
  - security note: this feature is one line of code away from leakage — reading participation for the
    game being predicted rather than prior games. It must take its data through the Context and the
    as-of filter, never query directly.
  - **built:** `backend/model/availability.py` and `backend/tests/test_availability.py` (31 tests).
    **465 backend tests pass**, ruff clean.

  ### The result this cycle was built to get

  Measured on the **dev seasons 2024+2025 only** (2,640 games; **2026 untouched**, per T-030):

  | | |
  |---|---|
  | **corr(`avail_diff`, `elo_diff`)** | **+.2552** |
  | `elo_diff` alone, AUC | .7149 |
  | `avail_diff` alone, AUC | .6009 |
  | `elo_diff` + .25·`avail_diff`, AUC | **.7208**  (**+.0059**) |
  | where \|`avail_diff`\| > .20 (83 games) | favoured side correct **.7349** |

  **That correlation is the number that matters.** The problem statement opens on
  `point_diff_diff`, Elo and `form_diff` correlating at **.87–.92** — three measurements of one
  latent variable, which is why the Phase 1 ablation found nothing and why 17.9% of games read as
  coin flips. At **+.255**, availability is a genuine *second factor*, not a better ruler for the
  first. D-035 called its value "unknown" and "the only real headroom"; the headroom is real.

  **Stated honestly, and not more than it is:** +.0059 AUC from a *crude, unfitted* blend at a
  hand-picked weight. It is not a fitted model, it is on the seasons where feature selection is
  permitted, and the plan's own noise caveat applies. **T-030 does the real measurement.** What this
  establishes is that the factor is orthogonal and carries signal — not the size of the final gain.

  ### Construction and defaults, measured rather than chosen

  - **Rotation** — top **9** players by minutes over the last **15** games, each weighted by mean
    minutes per game. Nine because the corpus averages 13.08 players listed, 10.69 playing and
    **9.04** playing ten-plus minutes per team-game; nine is the shape of the thing, not a round
    number.
  - **Participation** — over the last **5** games, the share of that rotation weight that was
    *present*, then shrunk toward the prior by `n/(n+k)` (D-015's shape).
  - **Prior = .8764**, the corpus mean over 13,120 team-games. Not 1.0 — a team with no record is
    *average*, and 1.0 would make every season opener the healthiest game of the year.
  - **A consequence worth knowing when reading values:** shrinkage caps a perfectly healthy team at
    **.9794**, not 1.0. Five games of evidence is not certainty.

  ### "Present", never "played minutes" — and why that is a leakage question

  A rotation player logging zero minutes in a blowout is **available**. The corpus holds **31,769**
  `did_not_play` rows with null minutes against **700** active rows at exactly zero minutes, and
  counting the second as absence would make the feature partly a *blowout detector*. Blowouts are
  outcomes — so that is leakage arriving by the back door, not merely a modelling infelicity. This
  is user story 12, and it is why T-021's schema carries `minutes` and `did_not_play` separately.

  ### The security note, closed structurally rather than watched

  1. **The module never queries anything.** It receives `Appearance` records and reads nothing else;
     there is no database handle to point at the wrong game. A test parses its imports.
  2. **`as_of` is a tripwire, not a filter.** Given one, the module **refuses** any appearance dated
     at or after it — at `>=`, because a row dated exactly at the prediction moment *is* the game
     being predicted. It refuses rather than drops on purpose: dropping would let a mis-filtered
     caller work by accident, which is exactly how the as-of control migrates out of `features.py`
     where T-006's property test actually proves it. The error says so, and a test asserts it still
     does.
  - The tripwire ran clean across all 13,120 team-games during the dev measurement, which exercises
    the guard end-to-end rather than only on fixtures.

  ### Reported, not hidden (D-035)

  It catches **multi-game absences** — most star injuries. It **cannot** catch a game-day scratch:
  the only evidence of such an absence is the box score of the game being predicted. That is the
  honest cost of a construction whose leakage-freedom is structural rather than procedural.

- [x] **T-028** `features` v2 — Context and the new feature set — owner: `backend-engineer` — **DONE 2026-09-05**
  - acceptance: Context carries games, player participation and venues; signature stays three
    arguments; emits `elo_diff`, `home_b2b`, `away_b2b`, `rest_edge`, `avail_diff`, `travel_diff`,
    `altitude`; `point_diff_diff`, `form_diff`, `home_advantage` removed; standard library only; the
    extended leakage property test and its non-vacuity control pass; a store query returning future
    rows produces identical vectors
  - security note: the as-of filter is the integrity control for **every** time-varying source now,
    not just games. One filter, inside the module, applied uniformly — and the property test must
    inject future player rows or it is no longer proving what it claims.
  - **built:** `backend/model/features.py` rewritten, `backend/model/records.py` extracted, plus
    `elo.Timeline`, `availability.AppearanceIndex`, three `store` loaders and three `dataset`
    adapters. **523 backend tests pass with zero skips** (up from 465), ruff clean.

  ### Measured on the dev seasons — user story 14, answered

  2,640 games, 2024+2025 only, 2026 untouched. The problem statement was that `point_diff_diff`,
  Elo and `form_diff` correlated at **.87–.92** — three measurements of one latent variable. The v2
  set's largest correlation between two *different factors* is **.255** (`elo_diff` ↔ `avail_diff`):

  | | elo | b2b(h) | b2b(a) | rest | avail | travel | alt |
  |---|---|---|---|---|---|---|---|
  | **elo_diff** | 1.000 | .002 | -.024 | -.008 | **.255** | .023 | -.005 |
  | **home_b2b** | | 1.000 | .154 | **-.485** | -.025 | -.094 | -.007 |
  | **away_b2b** | | | 1.000 | **.472** | .005 | .098 | -.025 |
  | **rest_edge** | | | | 1.000 | .004 | .114 | -.025 |
  | **avail_diff** | | | | | 1.000 | .025 | -.003 |
  | **travel_diff** | | | | | | 1.000 | -.016 |

  The two large entries are the **±.47–.49 between the b2b indicators and `rest_edge`**, and they are
  D-034 working rather than collinearity to fix: a back-to-back *is* zero days of rest, so the
  indicator and the bucket necessarily move together. Giving the 0-day cliff its own coefficient
  instead of a shared slope is the whole point of user story 6, and the three columns are not
  linearly dependent — `rest_edge` varies freely within `home_b2b == 0`.

  Single-feature AUC on the same games: `elo_diff` **.7149** (reproducing D-032 exactly, end to end
  through the Context), `avail_diff` .6009, `rest_edge` .5381, `away_b2b` .5140, `altitude` .5014,
  `travel_diff` .4970, `home_b2b` .4774 (i.e. .5226 with its sign).

  Fitting on 2024 and testing on 2025 — a dev split, not the walk-forward, which is T-030's:

  | | accuracy | log loss | AUC |
  |---|---|---|---|
  | `elo_diff` alone | .6593 | — | .7159 |
  | full v2 set | **.6692** | .6054 (constant .6893) | **.7249** |

  **+.0090 AUC over Elo alone**, against the plan's stated expectation of "+.005–.015 for rest,
  ~+.002 for travel and altitude, availability unknown". Every coefficient carries the sign the
  design predicted, which is the check that matters more than the number:

  `elo_diff` +.863 · `home_b2b` **−.155** · `away_b2b` **+.139** · `rest_edge` +.027 ·
  `avail_diff` +.121 · `travel_diff` **−.104** · `altitude` +.084 · intercept +.225

  The b2b pair is the one worth naming: **−.155 against +.139**. User story 7 asked for separate
  indicators so an asymmetric effect could be *measured* rather than assumed symmetric, and it is
  asymmetric. `travel_diff`'s negative coefficient is the sign convention working — every difference
  feature is signed home-minus-away, and travel is a cost, so more of it for the home team lowers the
  probability.

  ### The Context, and the filter that had to move up a level

  T-006's `history` was one sequence and the as-of filter had one thing to filter. The v2 set reads
  three time-varying sources, and the security note is blunt about the consequence. `Context` makes
  "uniformly" mechanical rather than aspirational, three ways:

  1. **It holds sources, never answers.** Every index it carries — `GameHistory`, `elo.Timeline`,
     `availability.AppearanceIndex` — is built over the whole corpus and has **no method that
     answers without an `as_of`**. There is no cached "current" anything to read by mistake.
  2. **One `as_of` reaches every source.** `compute_features` derives it once and passes that same
     instant down, so no source can be consulted at a different moment than its neighbours.
  3. **There is no partial Context.** Participation and venues are required, not optional, because
     an absent participation map defaults `avail_diff` to 0.0 and an absent venue map defaults
     `travel_diff` to 0.0 — and *both zeros are values the features legitimately take* (a healthy
     pair of teams; a home stand). A caller who forgot to load the box scores would get a vector
     that is wrong in a way nothing downstream can detect. Same shape as F-045 and F-113, closed
     the same way: refuse at construction, where the mistake is still visible.

  ### Two indices, and the equality that makes them safe rather than fast

  Replaying Elo per target is O(n²) over a training set — 6,600 targets against ~12,000 games is
  ~80M rating updates, minutes of pure Python. Handing `team_availability` a team's whole history is
  the same shape. So both were turned into precomputed indices, and **a performance shortcut on a
  leakage-critical path is only acceptable if its answers are equal to the slow path's, not close**:

  - `elo.Timeline` — `replay` processes games sorted by `(date, game_id)`, so "games dated strictly
    before `as_of`" is exactly a *prefix* of that order. `test_elo.py` asserts bit-exact equality
    against `pregame_rating_differences` for **every game of a corpus-shaped replay, under both
    carryover settings** (which apply different numbers of regressions across the 2019 → 2022 gap).
  - `availability.AppearanceIndex` — hands `team_availability` `lookback_games + 1` games instead of
    the whole history, and `test_availability.py` asserts exact equality against the unsliced call at
    every moment of a 60-game season, across three seeds.

  The Elo index also uncovered a bug worth recording: carryover is applied to the **whole ratings
  dict** at a season transition, not to a team when it next plays. A first draft caught teams up
  lazily and was correct on opening night and wrong for every game after it, for every team that had
  not yet played that season. It is now tracked as a regression *count* per stored rating. The
  corpus-wide equality test is what found it — a point check would not have.

  `Timeline` additionally **refuses a team appearing in two games at the same instant**, which is the
  precondition the prefix equivalence rests on. Simultaneous tip-offs between different teams are
  ordinary and stay ordinary; the same team twice at one moment has no well-defined answer, so it is
  refused rather than silently approximated.

  ### The leakage property test, extended — and both halves proven non-vacuous

  200 seeded trials now inject future **games and future player-box rows**, asserting exact equality,
  paired with **two** controls: a game before `as_of` moves the vector, and a player row before
  `as_of` moves `avail_diff`. Both halves were verified by sabotage — the as-of filter was removed
  from each index in turn and the property test went red each time.

  The same discipline caught a real gap in the acceptance test. `test_a_store_query_returning_future_rows_produces_identical_vectors`
  passed with the availability filter deliberately broken, because the store fixture gave every team
  identical participation in every game — there was nothing for a filter to change. The fixture now
  carries a nine-man rotation with absences that vary by game and by team, and the sabotage is
  caught. **A test that passes against a broken implementation is not a test**, and the only way to
  know is to break it.

  F-044's shape is closed on both new sources: a target's own copy dated microseconds *before* its
  matchup date reaches neither `elo_diff` nor `avail_diff`, because `exclude_game_id` is threaded
  through both indices. The Elo path takes an exact replay of the prefix-minus-that-game rather than
  an incremental un-update — Elo's update is not exactly invertible once later games have moved both
  ratings, and an approximate reversal on the corrupt-input path is how a leak becomes a rounding
  error nobody looks at.

  ### A layering inversion this task forced, and fixed

  `elo` imported `Game` from `features` — the deep module depending on its consumer. That was
  harmless until `features` grew a `Context` that builds an `elo.Timeline`, at which point it was an
  import cycle. `Matchup`, `Game` and their validation moved to `backend/model/records.py`;
  `features` re-exports them, so `from model.features import Game` still means what it always meant
  and no caller changed.

  ### Feature definitions worth pinning down

  - **Rest is elapsed hours rounded to days, never a difference of calendar dates.** Every date in
    this pipeline is UTC, and a 10:30pm Eastern tip-off is already the next day in UTC — so
    differencing dates would call a genuine back-to-back "two days apart", and would do it for
    exactly the late games where rest matters most. The boundary sits at 36 hours and real
    consecutive-day pairs span roughly 18–32, so nothing in the corpus is near it. A test pins it.
  - **Travel is season-scoped; rest deliberately is not.** A months-long gap reads honestly as
    "fully rested", so rest needs no season boundary. It does not read honestly as "flew 2,400
    miles", so travel does — a season opener carries no accumulated travel.
  - **`altitude` does not fire at a neutral site.** The feature's content is the *asymmetry*: at
    Denver the visitor is the unacclimated side, which is a home advantage the model can price. At
    Mexico City (7,350 ft, and in this corpus) both teams flew in, the elevation affects them
    equally, and it says nothing about who wins.
  - **A venue that cannot be placed is refused at construction**, once and loudly, rather than 6,000
    times as a quiet zero. `venues.city_for` has no fallback and this is where that pays.

  ### Both data paths build a Context

  `store.load_context` (Postgres, narrows **by team only** for the same reason `load_history` does)
  and `dataset.context_from_frames` (the pandas seam). `run_evaluation` was moved onto the second so
  the loader path still runs end to end; it was **not executed**, because its sealed fold tests on
  2026 and T-030 owns that single shot. Warm-up seasons are deliberately absent from it —
  `load_warmup_games` stays a separate function so the training path cannot reach warm-up rows by
  accident, and wiring them in as Elo state is T-029's mechanism.

  ### Reported, not hidden

  `travel_diff` at AUC .4970 and `altitude` at .5014 carry essentially nothing on their own, which is
  what the plan predicted (~+.002 combined). They earn their place in the fit, not in isolation, and
  T-030's ablation is where that gets decided rather than assumed.

- [x] **T-029** `splits` — warm-up isolation — owner: `backend-engineer` — **DONE 2026-09-05**
  - acceptance: warm-up seasons never appear as training or test rows; the existing temporal
    assertion still holds; a warm-up season injected as a training row fails a test
  - security note: integrity only — a fold must never train on its own future, and must never train
    on a different home-advantage regime.
  - **built:** `backend/model/splits.py` and `backend/model/corpus.py` extended,
    `backend/tests/test_splits.py` (+16 tests). **539 backend tests pass with zero skips**, ruff
    clean. Verified on the real corpus: 11,854 games partition into **5,249 warm-up + 6,605
    modeling** — and 6,605 is exactly the curated count F-042 established — with **zero warm-up rows
    reaching train or test on any of the three folds**.

  ### Two routes in, closed in two different places

  They fail differently, so one check cannot cover both:

  1. **Nominally, at construction.** `Fold` refuses to name a warm-up season at all, in either
     `train_seasons` or `test_season`. A fold that would train on the pre-2020 home-advantage regime
     is not a thing that should be constructible and then caught later — same reasoning T-007 used
     for the train-precedes-test check sitting in `__post_init__`. Checked **before** the ordering
     rule, so a fold naming only warm-up seasons is refused for the right reason rather than for
     being out of order.

  2. **Temporally, in `split_games`** — and this is the only half that can fire in practice. Once
     `Fold` shuts the nominal route, what is left is a warm-up *game* wearing a modeling season's
     label, which passes every season-number check ever written because the label is what they read.
     Its date does not. Labels are a claim, dates are the fact, applied to the second boundary.

  ### The boundary is anchored to the population being excluded

  `corpus.WARMUP_ERA_END` is the **last warm-up tip-off**, 2019-06-14T01:00Z — not the first modeling
  tip-off, 2021-10-19T23:30Z. The two are **858 days** apart, because no 2020 or 2021 season is
  pinned, and a test asserts that gap so it cannot be narrowed by accident.

  Pinning the boundary at the *start* of the modeling era was the first attempt and it was brittle in
  a way worth recording: a source revising the 2022 opener's timestamp by a few hours would begin
  refusing legitimate rows. It also broke the existing `test_splits` fixture immediately, whose epoch
  sat 4½ hours before the real opener — and "fix the fixture" would have been fixing the test to suit
  the check. Anchoring at the *end* of the warm-up era cannot fire on a modeling game unless one
  moves by more than two years, still catches every warm-up row, and left every existing test passing
  untouched. That last part is the signal that the check discriminates rather than merely raises.

  ### Warm-up games are dropped, not refused

  T-030's call has one shape: `elo.Timeline` needs the **whole** corpus and the estimator must see
  **none** of the warm-up. So one collection is passed to both and `split_games` is what separates
  them. Refusing warm-up rows in the input would force two collections and a caller to keep them in
  step, which is a job nobody should have. `corpus.partition_warmup` is the ergonomic half — it makes
  the right thing easy; `split_games` makes the wrong thing impossible, and neither substitutes for
  the other.

  ### `WARMUP_SEASONS` has exactly one definition, and it moved

  It lived in `loader`, with the download pins. But `splits` is the module that decides which rows the
  estimator sees, it is standard-library-only (D-021), and `loader` is unavoidably pandas — so
  `splits` could not import it, and the alternative was a second copy of the tuple. Two definitions of
  *"which seasons may be trained on"* is precisely the drift this project keeps closing elsewhere.

  The tuple now lives in `corpus`, which already owns the other curation policy (exhibition
  exclusion) and is already a `splits` dependency; `loader` re-exports it, and the pinned counts and
  hashes for those seasons stay with the download where they belong. A test asserts the two are the
  **same object**, not merely equal, so a copied literal fails.

  ### All three controls verified by sabotage

  Each check was removed in turn and the suite went red each time: dropping the temporal check fails
  the two mislabelled-game tests, dropping the nominal check fails eight, and re-declaring
  `WARMUP_SEASONS` in `loader` fails the identity test. Paired with a non-vacuity control asserting
  the boundary does **not** fire on any legitimate fold — a check that refused everything would
  satisfy the refusal tests perfectly and be worthless.

  ### Measured: the warm-up costs nothing and, on fold 1, buys nothing either

  D-037's stated rationale is *"so that fold 1 trains on converged ratings rather than burn-in
  noise."* The first half is real: over the first 200 games of 2022, warming up shifts `elo_diff` by a
  **mean of 89 rating points** (median 72.5, max 281.5). The features genuinely change.

  The second half does not follow. Fold 1 (train 2022-2023 → test 2024, both dev seasons):

  | Elo state | accuracy | log loss | AUC |
  |---|---|---|---|
  | warmed on 2016-2019 | .6687 | .6088 | .7272 |
  | cold start at 2022 | .6702 | .6066 | .7265 |

  **+.0007 AUC and −.0015 accuracy** — a wash. By 2024 the ratings have converged either way, and the
  fit absorbs the early-2022 difference in the training rows. This is user story 18 in practice: a
  null result reported as readily as a positive one, and it is input for **T-030's ablation**, which
  is where the keep-or-drop call belongs. Nothing here changes: the warm-up is cheap, the isolation
  is required regardless of whether the warm-up is kept, and `elo`'s open question about the
  2019 → 2022 carryover gap is now known to be worth even less than it looked.

- [ ] **T-030** Refit, the single 2026 evaluation, and freeze — owner: `backend-engineer`
  - acceptance: feature selection performed on 2024/2025 only; the feature set frozen and committed
    before 2026 is touched; **exactly one** evaluation run against 2026; per-fold accuracy, log loss
    and AUC reported with the ablation; versioned JSON artifact emitted
  - security note: the artifact stays JSON (D-030) — no deserializer that can execute code.
  - **the discipline is the deliverable.** If the frozen set underperforms, that is the result. A
    second look at 2026 to "check something" spends the fold and must be recorded if it happens.
  - **owner's calls, taken 2026-09-05 on T-029's measurements, before 2026 is touched:**
    1. **Keep the warm-up.** It is already built and tested, it costs nothing, and T-029 measured its
       benefit on fold 1 at +.0007 AUC / -.0015 accuracy — a wash. Dropping it now would be a change
       for its own sake, and the isolation machinery is required either way.
    2. **Drop `travel_diff` and `altitude` if the ablation confirms them weak.** Standalone they
       carry nothing (AUC .4970 and .5014), which is the basis for the call. **But standalone AUC and
       multivariate contribution are different questions, and here they disagree:** in T-028's dev
       fit both coefficients were mid-pack — `travel_diff` -.104 and `altitude` +.084, *larger in
       magnitude than `rest_edge`'s* +.027, which is not a candidate for removal. So the ablation on
       2024/2025 decides, with removal as the standing prior: drop unless leaving them out measurably
       hurts, and report the number either way before the set is frozen.

- [ ] **T-031** `prediction` service, persistence, and the scheduled job — owner: `backend-engineer`
  - acceptance: one interface returning probability, feature vector, per-feature logit contributions
    and model version; used by both the job and the API; predictions appended (never updated) keyed
    by game, model version and as-of; daily append over a seven-day horizon
  - security note: the job writes; the API reads. Do not let the API path acquire a write. Also note
    the existing hazard in *Future hardening* — an in-process scheduler double-syncs under a second
    replica.

- [ ] **T-032** Predictions API — owner: `backend-engineer`
  - acceptance: a prediction with its decomposition for a given game; upcoming predictions; the
    accuracy record by confidence band; the last prediction before tip-off is the one scored
  - security note: read-only endpoints against a database with no authorization boundary (D-047).
    Return model outputs; do not expose corpus rows wholesale.

- [ ] **T-033** TypeScript scorer + contract check — owner: `frontend-engineer`
  - acceptance: pure scorer returning probability and contributions; a gate check runs both
    implementations over a grid and asserts agreement on **both** outputs; the check fails CI when
    either implementation drifts
  - security note: this is a second implementation of the scoring path, which D-011 forbids. The
    contract check is the entire justification — it must run in the gate, not locally.

- [ ] **T-034** Game detail surface — owner: `frontend-engineer`
  - acceptance: a route per game showing probability, confidence band with its historical hit rate,
    the contribution waterfall with each factor's underlying value, model version and as-of moment;
    a fenced what-if panel that is visually distinct, never persisted and never counted; responsive;
    keyboard-navigable with a screen-reader-legible alternative to the chart
  - security note: no direct calls to anything but this project's API. Hypothetical state must never
    reach a write endpoint.
  - ui/ux intent for review: direction must read pre-attentively; the fence between real and
    hypothetical must survive a screenshot with no surrounding text; the chart must carry a zero
    baseline; color must not be the only channel carrying sign.

- [ ] **T-035** Amend §6 and write the v2 analysis — owner: `human`
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
