# Implementation Tracker — Sports Dashboard

> Source of truth across sessions. Read this first every session.
> Last updated: 2026-08-10 by Claude (T-006 build session)

## Status legend

`BACKLOG` → `PLANNED` → `IN_PROGRESS` → `BUILT` → `REVIEWED` → `DONE`
`BLOCKED` is an orthogonal flag (a task can be e.g. `IN_PROGRESS` + `BLOCKED`).

- **BACKLOG** — not yet planned in detail
- **PLANNED** — spec'd, acceptance criteria written, ready to build
- **IN_PROGRESS** — a subagent is actively building it
- **BUILT** — code complete, NOT yet reviewed
- **REVIEWED** — passed all required reviews
- **DONE** — merged / shipped

> **A task is never `DONE` until it is `REVIEWED`.** Review is a gate, not an afterthought.

## Current state

**State now:** **T-005 is `REVIEWED` at `34759ed` (✅✅)** — both reviewers re-reviewed it after
`406dd09` touched `loader.py`, proved the change AST-identical once docstrings are stripped, and
security re-ran the download from a genuinely empty dir anyway. **T-006 is `BUILT` and remediated,
awaiting re-review.** Round 1 was ⛔⛔ and both verdicts were earned: security showed the target's own
result was reachable through `history` (F-044 — the `Matchup` split closed only the direct route), and
logic ran ~60 mutations of which **7 survived my test suite**, three changing 5,000+ of the 6,615 real
feature vectors. The module was largely right; the *tests* were not. All 17 findings (F-044..F-060)
are addressed, and the fix is verified by re-running the reviewers' own mutations — **10/10 now
caught**. Suite 58 → 79. Real-corpus numbers unchanged to the digit.

**Next action:** re-review T-006 (`Use the security-auditor subagent on T-006`, then `logic-reviewer`)
against the remediation. Then **F-042** (exclude the 10 All-Star games) before T-007 begins — note
**D-024/F-057**, which corrects D-020(4): once F-042's filter lands, `home_advantage` is 1.0 in
2,642 of fold 1's 2,643 rows, so its coefficient is not identifiable in the early folds.

**Active plan:** docs/plans/PLAN-current.md (= PLAN-v1, ACTIVE)
**History:** docs/log.md (append-only, session-by-session)

## Architecture snapshot

- **FastAPI is the only DB writer, and the only caller of third-party APIs.** The frontend talks to
  FastAPI and nothing else — never ESPN, never Postgres directly.
- **No auth, no RLS, no authorization boundary exists today.** Postgres enforces nothing about who may
  read or write a row, and no endpoint checks a caller's identity. See F-001 before adding any
  user-scoped data.
- **Game status is normalized at the boundary** to exactly `scheduled` | `live` | `final`. ESPN's raw
  status strings never reach the DB or the client.
- **Every schema change is an Alembic migration.** Never `Base.metadata.create_all()` outside tests.
- **The app DB (Postgres) and the historical/modeling store are separate concerns.** Bulk history
  never enters Postgres wholesale or the git repo; the modeling layer reads history and writes only
  its outputs back for the API to serve.
- **One ID space: ESPN's.** The app keys teams on ESPN `team.id` (`teams.external_id`) and the chosen
  historical source (D-004) is ESPN-keyed too, so no translation layer is needed. This is an
  invariant to defend: adding any stats.nba.com-keyed source reintroduces the join problem (F-005).
- **Python is pinned at 3.11** across `backend/Dockerfile`, `ci.yml` and `gate.yml`. They move
  together or not at all — a local interpreter that differs from the container is drift. See T-004.
- **`backend/model/features.py` is standard-library only, and must stay that way** (D-021). CI installs
  `requirements.txt` and never `requirements-train.txt`, so an import of pandas/numpy there fails
  `be-unit` in CI while passing locally; and D-016 has Phase 2 importing this module inside the API
  service, so whatever it imports the served image must carry. `backend/model/dataset.py` is the
  seam where pandas is allowed to meet the feature pipeline — put frame handling there, never in
  `features.py`.

## Tasks

### Dev-System instantiation
- [x] **T-001** Instantiate the Dev-System into this repo — `DONE` — owner: `human`
      - acceptance: `.claude/` (5 agents + 2 Stop hooks + settings.json + CANON-VERSION),
        `checks/` + `stacks/nextjs-fastapi-postgres.md`, `docs/` (tracker, log, learning-notes),
        root `CLAUDE.md`, `.github/workflows/gate.yml`; `node checks/run-gate.mjs
        --stack stacks/nextjs-fastapi-postgres.md` exits 0 locally
      - security note: the gate job must stay secret-free and stay on `on: pull_request`
        (never `pull_request_target` — that would run a fork's edited `run:` with repo secrets)

### Historical data + modeling (planning pending)
- [ ] **T-002** Ingest sportsdataverse NBA seasons 2022–2026 into a local modeling store — `BACKLOG` — owner: `backend-engineer`
      - source (verified by download 2026-08-09, not from docs): release assets on
        `github.com/sportsdataverse/sportsdataverse-data` — `espn_nba_schedules/nba_schedule_<season>.csv`
        (2026 = 1,330 games, 2025-10-21 → 2026-06-14, 1,326 completed; 2022 = 1,335 games from
        2021-10-19) and `espn_nba_pbp/play_by_play_<season>.parquet`. Assets refreshed 2026-07-28/08-07.
      - acceptance: seasons 2022–2026 land in the modeling store; game counts per season match the
        source files; ingest is idempotent (re-running does not duplicate); the store is gitignored
      - security note: pin the asset URLs by release tag and verify what is downloaded before
        parsing — this is third-party data fetched over the network into a parsing path
      - blocked on: T-003 (shape of the store is a planning decision, not a build-time one)
- [ ] **T-004** Evaluate moving the Python runtime off 3.11 — `BACKLOG` — owner: `backend-engineer`
      - acceptance: `backend/Dockerfile`, `.github/workflows/ci.yml`, `.github/workflows/gate.yml`
        and the local venv all move together, and the full gate is green on the new interpreter
      - first checks: does `psycopg2-binary==2.9.12` have a wheel for the target (no wheel → source
        build → `-slim` image has no `pg_config` → broken build); do the modeling libs
        (pandas/pyarrow/scikit-learn) all publish wheels for it
      - also: `backend/pyproject.toml` has no `requires-python`, so ruff's `UP` rules are targeting a
        default rather than this project's real floor — set it as part of this task
- [ ] **T-003** PLAN the modeling layer (grill-me → to-PRD → `PLAN-v1.md`) — `BUILT` — owner: `human`
      - acceptance: `docs/plans/PLAN-v1.md` exists, is copied to `PLAN-current.md`, and decomposes
        into tracker tasks each carrying acceptance criteria + a security note — **all met**
      - outcome: grill-me resolved 10 forks (D-007..D-016); to-PRD wrote PLAN-v1. Two premises of the
        original brief did not survive contact with the data — see D-007.

### Phase 1 — analytical core (PLAN-v1)

<!-- Offline only. No schema, API, UI, or served-image change. Answers the one question that can
     invalidate everything downstream: can four pre-game features clear 62% with honest calibration? -->

- [ ] **T-005** Historical data loader — `REVIEWED` — owner: `backend-engineer`
      - acceptance: downloads 2022–2026 season schedules from a pinned upstream release tag into the
        ignored data dir; normalizes to a completed-game collection; per-season counts match
        (2022 → 1,324; 2026 → 1,326); non-final games excluded; re-running is idempotent — **all met**
      - **acceptance (added post-review, F-037):** any change to the download path MUST be verified by
        a run against an **empty** data dir. "Gate green" is not evidence the download works — nothing
        outside `loader.py` calls the loader, and every local run hits the populated cache. That is
        precisely how a broken redirect allowlist passed the gate, the builder's own testing, and a
        five-season smoke run.
      - security note: third-party data over the network into a parsing path. Pin by release tag not a
        moving branch, verify the download before parsing, never use a deserializer that can execute
        code, write only inside the ignored data dir.
      - outcome: `backend/model/loader.py` (package `__init__.py` deliberately empty, D-016) pins
        `RELEASE_TAG = "espn_nba_schedules"`; downloads verify a header/size sanity check on raw bytes
        before pandas ever parses them, write via temp-file + atomic rename, and skip re-download when
        a valid cached file already exists (idempotent). Parsing uses pandas' text CSV reader with
        every column typed `str` — never pickle/yaml/eval. Ran for real: 2022→1324, 2023→1321,
        2024→1320, 2025→1324, 2026→1326, 6,615 total — all match. New training-only deps (pandas,
        numpy, python-dateutil, six, certifi) pinned exactly in new `backend/requirements-train.txt`;
        `backend/requirements.txt` unchanged. `data/raw/nba_schedules/` confirmed gitignored via
        `git check-ignore`; `git status` stays clean after running the loader. Gate green, 8/8.
        **Remediated post-review (F-026..F-034, see Review gate section below):**
        `load_season` — not just `load_completed_games` — now calls `_verify_season` directly, which
        asserts both the pinned count *and* `game_id` uniqueness per season and refuses any season
        absent from `EXPECTED_COMPLETED_COUNTS` outright, so no call path can obtain unverified data.
        A new `EXPECTED_SHA256` dict pins per-season content hashes (computed from the files that
        produced the verified 6,615-game count) and is checked before parsing on both the download and
        cached-file paths, closing the "tag is stable but assets are mutable" gap. Also: size cap
        enforced via `stat()` on the cached path, redirect final URL checked against an
        https+host-allowlist, `season` validated against `SEASONS` with the resolved destination path
        asserted inside the data dir, non-UTF-8 header decode now raises the module's own error type,
        and `certifi` added directly to `requirements-train.txt`. Both HIGH findings reproduced against
        the pre-fix code and closed against the fixed code — see Review gate section. F-024/F-025
        (`checks/`) were closed by the main thread, out of this task's scope.
- [ ] **T-006** `features` deep module + tests — `BUILT` — owner: `backend-engineer`
      - acceptance: one interface (history, target game, as-of) → feature mapping; rolling form, rest
        days, season-to-date point differential, home indicator, all as home-minus-away differences
        with `n/(n+k)` shrinkage; module is pure (no I/O, no clock, no DB); golden fixtures pass;
        **leakage property test passes**; shrinkage boundaries at 0, 1, `k` pass — **all met**
      - security note: the as-of filter is an integrity control, not a convenience — it is what makes
        every reported number honest. Enforce it inside the module, never delegate to callers, so no
        future call site can opt out.
      - outcome: `backend/model/features.py` — one public interface,
        `compute_features(history, target, as_of)`, returning a mapping keyed by exactly
        `FEATURE_NAMES` = `home_advantage`, `form_diff`, `rest_diff`, `point_diff_diff`. Pure: no I/O,
        no DB, and **no clock** — `as_of` is always a parameter, never `datetime.now()`, so every
        number is reproducible. Standard-library only (D-021); `backend/model/dataset.py` is the new
        pandas seam converting the loader's frame to `Game` records.
      - **the security note, honored three independent ways** (one filter is a thing code can forget):
        (1) the filter lives inside the module and runs at query time in `GameHistory._records_before`,
        strict `<` not `<=`, with no flag, no alternate entry point and no other code path that reads
        a game dated at or after `as_of`; (2) the target is a **scoreless `Matchup`**, so a completed
        game's own result is not merely filtered out of its own features but structurally unreachable
        (`Game.matchup` is a one-way door) — a `Game` passed as target raises; (3) `as_of` after
        tip-off raises `FeatureLeakageError`, making D-010's "never re-predict a finished game"
        mechanical. The index holds no as-of state, so passing a prebuilt `GameHistory` is exactly
        equivalent to passing the raw sequence (asserted both directions).
      - tests: 40 in `backend/tests/test_features.py`, stdlib-only so they run in CI. Golden fixture
        hand-computed and written as literals (`form_diff` 0.125, `rest_diff` 3.0, `point_diff_diff`
        6.0). The leakage property test runs 200 seeded trials injecting 1–8 games dated at/after
        `as_of` into shuffled histories and asserts **exact** equality — plus a **control asserting a
        game dated *before* `as_of` DOES change the output**, without which a module that ignored
        history entirely would pass the leakage test perfectly. Boundaries at n=0 (prior exactly), n=1
        (w=1/6), n=k=5 (exactly halfway between observation and prior), plus monotonicity in n.
        Also: home/away swap negates every difference, the window caps *both* the average and the
        shrinkage count, and season scoping holds. Full suite 52 passed, ruff clean.
      - **verified against the real corpus, not only fixtures** (the T-005/F-037 lesson): all 6,615
        games load and produce complete, finite, correctly-ordered vectors in ~0.1s; the leakage
        guarantee re-checked on 133 real games against a manually past-truncated history, exact
        equality every time; `home_advantage` is 0.0 for exactly the 19 neutral-site games; 2026
        opening night is *predicted*, at home-court advantage alone with every difference 0.0 (D-015
        working as intended, user story 12); and the signal points the right way — mean `form_diff`
        +0.043 when the home team wins vs −0.058 when it loses, `point_diff_diff` +1.89 vs −2.44.
      - **not a T-006 defect, but found by it: F-042** (10 All-Star exhibition games in the corpus)
        and **F-043** (this commit makes T-005's review stale by the gate's currency rule). Read both
        before reviewing.
- [ ] **T-007** `splits` fold generator + tests — `PLANNED` — owner: `backend-engineer`
      - acceptance: yields exactly the three expanding-window folds (22-23→24, 22-24→25, 22-25→26);
        tests assert every fold's training seasons precede its test season and no season appears on
        both sides of a fold
      - security note: integrity only — a fold must never train on its own future.
- [ ] **T-008** `evaluate` metrics module + tests — `PLANNED` — owner: `backend-engineer`
      - acceptance: accuracy, log loss, AUC, calibration curve, and comparison against a constant
        base-rate predictor; tests assert each against hand-computed values on a small labelled set,
        plus the comparator's boundary behavior
      - security note: none.
- [ ] **T-009** `estimator` + walk-forward evaluation run — `PLANNED` — owner: `backend-engineer`
      - acceptance: logistic regression fit per fold; versioned artifact emitted; three folds run end
        to end; per-fold and headline accuracy/log loss/AUC reported with the fold-to-fold spread;
        states plainly whether **both** criteria are met (≥62% accuracy AND log loss beating a constant
        55.56% predictor); training-only deps confined to the training requirements file, served image
        unchanged
      - security note: **the serialized artifact is an arbitrary-code-execution vector.** Loading a
        pickled object executes code inside it, and Phase 2 loads this artifact inside the API service.
        Produce and consume it only with this project's own code, load only from a trusted local path,
        never from a network or user-supplied path, never commit it. Pin the new numeric/modelling deps
        exactly, consistent with existing requirements discipline.
- [ ] **T-010** Written analysis of the result — `PLANNED` — owner: `human`
      - acceptance: records which features carried signal (coefficients + direction), where the model
        failed, whether probabilities are calibrated, how folds differed; states the ship/no-ship
        verdict against the paired criterion; every number reproducible from committed code + the
        pinned data release
      - security note: publish nothing that cannot be reproduced from committed code — an
        unreproducible number in a portfolio artifact is a claim that cannot be audited.

## Decisions log (append-only)

- **D-001** 2026-08-09 — Instantiated the Dev-System into `sports-dashboard/` before planning the modeling
  layer, rather than after. Reason: modeling adds a second data domain and a second store; without a
  tracker and gate in place first, those decisions live only in chat — the exact loss SYSTEM.md §0
  exists to prevent. (Supersedes nothing.)
- **D-002** 2026-08-09 — Authored a new stack overlay `nextjs-fastapi-postgres` instead of reusing
  `nextjs-fastapi-supabase`. Reason: this project runs plain Postgres with no Supabase and no RLS, so
  the Supabase overlay's RLS invariants and `rls-coverage` gate would be gates over a mechanism that
  does not exist — worse than no gate, because they read as protection. Its safety role transfers to
  `authz-deny`, which is undeclared until auth exists (see F-001). (Supersedes nothing.)
- **D-003** 2026-08-09 — The gate declares only the five checks that genuinely run today (fe-typecheck, fe-lint,
  fe-unit, be-lint, be-unit). `a11y`, `perf`, and `authz-deny` are deliberately NOT declared because
  their tooling is not installed; `gate-completeness` fails a declared-but-missing gate by design.
  (Supersedes nothing.)
- 2026-08-09 — **D-004: sportsdataverse-data is the historical source.** Seasons 2022–2026 (the last
  five) via GitHub release assets. Chosen over `nba_api` and `shufinskiy/nba_data`: `nba_api` pulls
  live from stats.nba.com, which rate-limits to ~1 req/s and silently drops datacenter IPs — a
  dependency that fails precisely when deployed, and one we decline to build on; `shufinskiy` was
  last refreshed 2025-02 and does not reach the current season. Decisive factor: sportsdataverse is
  ESPN-keyed, the same ID space the app already stores. (Supersedes nothing.)
- 2026-08-09 — **D-005: the Kaggle `wyattowalsh/basketball` corpus is dropped and deleted.** Its
  2.35 GB bought pre-2002 depth the project does not need for game modeling, at the cost of a
  three-season staleness gap, zero indexes, and a foreign ID space. Deleted from disk this session.
  Consequence: the modeling horizon is 2002-present (sportsdataverse's floor), and F-002/F-003/F-004
  are closed as moot. (Supersedes nothing.)
- 2026-08-09 — **D-006: stay on Python 3.11 for now.** The container (`python:3.11-slim`), CI, and
  the local venv agree today; moving one without the others is drift. An upgrade is real work with a
  real failure mode (psycopg2-binary wheel availability against a `-slim` image), so it goes through
  the gate as T-004 rather than riding along with the instantiation. If it moves, 3.13 is preferred
  over 3.14 for scientific-stack wheel coverage. (Supersedes nothing.)

<!-- D-007..D-017 come from the 2026-08-09 grill-me session; the full reasoning is in PLAN-v1. -->

- **D-007** 2026-08-09 — **The NBA home-court baseline is 55.56%, not 58%.** Measured over 6,615
  completed games (2022–2026); only one of those five seasons reached 58%. Sampling back to 2002 shows
  why: pre-2020 seasons average 59.3% home wins, 2021-onward 55.2% — a ~4-point structural break at the
  COVID no-crowd seasons that never reverted. The 58% figure is an artifact of averaging across an era
  that no longer describes the sport. Consequence: every "beat the baseline" claim in this project is
  measured against 55.56%. (Supersedes nothing.)
- **D-008** 2026-08-09 — **Ship criterion is paired: ≥62% accuracy on the sealed fold AND log loss
  beating a constant 55.56% predictor.** Accuracy alone cannot validate the product's central promise —
  that a 55% call and an 80% call differ — and a model can hit 65% accuracy while being badly
  calibrated. Calibration is therefore a gate, not a nice-to-have. (Supersedes nothing.)
- **D-009** 2026-08-09 — **Train 2022–24, validate 2025, seal 2026.** 25 seasons are available back to
  2002; older ones are deliberately unused. Training on a 59.3% home-court era shifts every emitted
  probability, which fails D-008's calibration half even where accuracy survives. The window also
  excludes the two COVID-affected seasons as abnormal for a home-court model. (Supersedes nothing.)
- **D-010** 2026-08-09 — **Predictions are persisted append-only and written by a scheduled job inside
  FastAPI.** Forced by the live accuracy tracker: proving "we said 71% before tip-off" requires the
  prediction as it existed then, and a model that re-predicts a finished game will use information that
  did not exist. Keeping the writer inside FastAPI preserves the only-DB-writer invariant, currently the
  only structural guarantee about write access given there is no RLS and no auth. (Supersedes nothing.)
- **D-011** 2026-08-09 — **One `games` table, one feature function, for both training and inference.**
  Backfill history into Postgres and let the existing ESPN sync keep it current. Two sources feeding one
  feature function is train/serve skew — the failure that produces a great backtest and a quietly worse
  live model, with no visible symptom. Feasible only because D-004 chose an ESPN-keyed source, so the
  two merge rather than needing reconciliation. (Supersedes nothing.)
- **D-012** 2026-08-09 — **Seven-day horizon, appended daily, keyed (game_id, model_version, as_of).**
  Resolves the conflict between wanting a week-ahead board and immutable predictions: rows are never
  updated, the board shows the newest, and the tracker evaluates the last row before tip-off. A
  prediction made six days out has a rest-day feature that is wrong, not merely stale — refreshing is
  required, so immutability had to be defined per-row rather than per-game. (Supersedes nothing.)
- **D-013** 2026-08-09 — **Expanding-window walk-forward, three folds**, rather than one sealed season.
  A single 1,326-game season carries ~±2.6 points of accuracy noise at 95% confidence, so a 62% gate on
  one season is substantially decided by chance. Three folds give ~3,900 evaluation games plus a
  fold-to-fold spread, while the final fold remains untouched as the headline. Random k-fold is rejected
  outright: it trains on the future. (Supersedes nothing.)
- **D-014** 2026-08-09 — **Phase 1 is the analytical core evaluated offline, with no application
  change.** The existential risk is whether four pre-game features clear 62%, and that is answerable
  from the data files before any migration exists. Building the plumbing first means possibly writing it
  for a model that never ships. This is §5.2's "extract the analytical core" applied literally.
  (Supersedes nothing.)
- **D-015** 2026-08-09 — **Cold start handled by shrinkage toward the league mean**, weight ≈ `n/(n+k)`
  with k≈5, rather than dropping early-season games. Dropping until both teams have N prior games costs
  ~16% of every season including all of opening month — and would take the product dark for three weeks
  each autumn, exactly when interest peaks. (Supersedes nothing.)
- **D-016** 2026-08-09 — **The model package lives inside the backend, with serving and training
  dependencies split.** D-010 puts inference inside FastAPI, so the API image must eventually load the
  artifact and run the feature function regardless — placing the package anywhere else guarantees a
  later move or a duplicated feature function, the latter reintroducing exactly the skew D-011 removed.
  Training-only tooling stays out of the deployed image. (Supersedes nothing.)
- **D-017** 2026-08-09 — **Retrain on all five seasons before the 2026-27 opener (2026-09-30).** The
  walk-forward establishes the honest estimate using only past-trained folds; once banked, the holdout
  has done its job. The next NBA game is 2026-10-03, so there are ~7.5 weeks with no games — the window
  Phase 1 needs — and 2026-27 then becomes a genuine live out-of-sample test, the most credible number
  this project can produce. (Supersedes nothing.)

- **D-018** 2026-08-09 — **A reviewer ✅ goes stale only while a task is `REVIEWED`, never once it is
  `DONE`.** Gating DONE meant every completed task's review expired on the next commit anywhere in the
  repo, which made the check unusable past a project's first phase (F-018). REVIEWED is "passed review,
  awaiting merge" — its ✅ must reflect the code about to merge. DONE is "merged/shipped": frozen
  history. If DONE code is later modified, that is a new task with its own review, not a re-review of
  the old one. Promoted to canon (`be5f6dc`) rather than patched locally, because every project stamped
  from this canon has the bug. (Supersedes nothing.)

- **D-019** 2026-08-09 — **T-005 implementation choices the plan left open.** (1) Parses with pandas
  rather than the stdlib `csv` module — this source's CSVs carry a `highlights` column with embedded
  newlines *and* Python-repr'd numpy arrays inside quoted fields, and pandas' C parser is the more
  battle-tested engine for that; it's also what T-006/T-009 will need regardless, so `loader.py`'s
  "heavy import" is not wasted. (2) Data lands at repo-root `data/raw/nba_schedules/` (not
  `backend/data/`) — matches CLAUDE.md's framing of `data/` as the historical store's home. (3)
  Idempotency is cache-then-verify: a valid cached file short-circuits re-download entirely, rather
  than always re-fetching and overwriting; a corrupted/truncated cache fails loudly instead of
  silently re-downloading, consistent with "fail hard, don't self-heal quietly." (4) Filtering does
  **not** split by `season_type` — the pinned expected counts (docs/IMPLEMENTATION.md T-005) are
  measured across all three (regular/postseason/play-in), so filtering further would fail the count
  tripwire by construction; `season_type` is carried through unfiltered for T-006+ to use. (5) Hit an
  environment issue, not a plan gap: the python.org macOS build doesn't read the system keychain, so
  `urllib`'s default SSL context failed closed with `CERTIFICATE_VERIFY_FAILED`. Fixed by passing an
  explicit `ssl.create_default_context(cafile=certifi.where())` — `certifi` is already a transitive
  `requirements.txt` dependency (via `httpx`), so this added nothing to the served image; verification
  is unchanged, only the CA source is made explicit. (Supersedes nothing.)

- **D-020** 2026-08-10 — **T-006 feature definitions the plan left open.** (1) **Rolling form is
  season-scoped**, over a 10-game window. Last season describes a different roster, and scoping it
  this way is also what makes D-015's cold start recur every autumn rather than only in 2022 — which
  is what D-015's own cost estimate ("~16% of every season, including all of opening month") assumes.
  The window caps the shrinkage count too, not just the average, so 15 straight wins is n=10, not
  n=15. (2) **Season-to-date point differential uses every game of the season so far**, not a window —
  the plan's wording, and deliberately the slower-moving counterpart to form. (3) **Rest days are
  measured to tip-off, not to `as_of`, and capped at 5 days.** Measured on the real corpus: median gap
  2.0 days, p95 3.9, max 10.0 (the All-Star break), only 2.3% of gaps reach 5. Past that point extra
  days are schedule structure rather than rest. The cap also removes the empty case — a season opener's
  "no previous game" and an offseason gap both land on the cap, which reads as fully rested. Measuring
  to tip-off is what makes D-012's "a prediction six days out has a rest feature that is *wrong*, not
  merely stale" visibly true; measuring from `as_of` would hide it behind a self-consistent number.
  (4) **`home_advantage` is 0.0 at a neutral site** — 19 real games, all regular season, spread across
  all five seasons, so the feature is genuinely non-constant rather than an intercept in disguise.
  (5) **A tied final score is refused as corrupt input**: NBA games cannot tie, none of the 6,615 do
  (verified), and the bare `home_score > away_score` the loader uses would silently score a tie as a
  home loss and bias every form feature that team appears in. (Supersedes nothing.)
- **D-021** 2026-08-10 — **`features.py` is standard-library only; `dataset.py` is the pandas seam.**
  Two independent constraints force it, and either alone would be sufficient. CI (`gate.yml`) installs
  `requirements.txt` and never `requirements-train.txt`, so a pandas import in the feature module fails
  `be-unit` in CI while passing locally — the split-environment failure class that cost T-005 a review
  round (F-037). And D-016 has Phase 2 importing this module inside the FastAPI service, so whatever it
  imports the served image must carry; a heavy feature module creates pressure to write a lighter second
  copy for serving, which is exactly the train/serve skew D-011 removed. Rather than leave the seam
  implicit, `backend/model/dataset.py` exists to hold it: `games_from_frame` / `load_games` convert the
  loader's frame to `Game` records and are the only place the two worlds meet. (Supersedes nothing.)
- **D-022** 2026-08-10 — **The target is a scoreless `Matchup`, and an `as_of` after tip-off is
  refused.** The plan said "target game", which would naturally have been the completed `Game` record.
  Splitting the type is what turns the no-leakage guarantee from a filter into a structural property:
  the target's own result is not reachable from inside the feature computation at all, so no bug can
  leak it, and the same type is equally constructible for a game played in 2022 and one tipping off
  next Tuesday — which is what lets training and inference call one function (D-011). Refusing
  `as_of > target.date` makes D-010's "a model that re-predicts a finished game uses information that
  did not exist" mechanical rather than remembered. Cost: T-009 calls `compute_training_features`
  (`as_of` = the game's own tip-off, no parameter to get wrong) or `game.matchup` explicitly.
  (Supersedes nothing.)

- **D-023** 2026-08-10 — **Per-task review currency is derived from git, not declared in the tracker,
  and process commits do not claim ownership** (the two forks F-043's fix ran into). (1) A
  hand-maintained `files:` field per task was rejected: narrowing it narrows the gate, so it would be
  a new bypass of exactly the kind F-019 and F-025 already had to close. Deriving ownership from
  commit history can't be gamed by editing a document. Attribution reads the commit **subject** only —
  bodies routinely discuss other tasks, and the commit that opened F-043 names T-005, T-006, T-007 and
  T-009 in its body, so body matching would have handed T-005 ownership of files it never touched.
  (2) `review(...)` and `docs(...)` subjects are excluded, on evidence rather than taste: `0c3b8a9`
  (`review(T-005): record gate verdicts; fix F-035/F-036`) also touched `checks/lib/tracker.mjs` and
  `.claude/CANON-VERSION`, so attributing it would have made T-005 own the gate's own source — and
  the F-043 fix, which edits that file, would then have invalidated T-005's review. The remedy would
  have reintroduced the false positive it exists to remove. This also matches a convention the project
  had already written down in `log.md`: a review commit must be `docs/`-only. Note what the rule does
  **not** need: later commits need no attribution at all. A task's own commits establish its file set;
  git then answers whether anything touched those files since, however it was labelled. A task with no
  attributable commits falls back to the stricter repo-wide rule rather than being exempted.
  (Supersedes nothing; implements the *Future hardening* item F-019 filed and F-043 sharpened.)

- **D-024** 2026-08-10 — **`home_advantage` is near-collinear with the intercept in the early folds.
  Supersedes D-020(4).** D-020(4) justified the neutral-site feature with "19 real games, all regular
  season, spread across all five seasons, so the feature is genuinely non-constant rather than an
  intercept in disguise." The logic reviewer checked that claim against the corpus and it is wrong on
  both counts; re-verified independently here:
  - **3 of the 19 neutral games involve F-042's All-Star phantom ids**, so they are not all regular
    season, and they disappear the moment F-042's filter lands.
  - After that exclusion the per-season counts are `{2023: 1, 2024: 3, 2025: 6, 2026: 6}` — **2022 has
    zero**, so they are not spread across all five seasons either. PLAN-v1's **first training fold
    (2022–23) contains exactly 1 neutral game in 2,643 rows**: `home_advantage` is `1.0` in
    2,642 of 2,643.
  A column that constant is near-perfectly collinear with the intercept, so its fitted coefficient is
  unstable and essentially arbitrary — which directly undermines T-010's acceptance ("records which
  features carried signal, via coefficients and their direction"). **This is not a T-006 code change**
  — the feature is computed correctly and is genuinely informative by the later folds. It is a
  constraint on T-009/T-010: report the coefficient with its uncertainty and say plainly that the
  early folds cannot identify it, or fold `home_advantage` into the intercept for those folds. What
  must not happen is quoting a home-court coefficient from fold 1 as if it meant something.
  Lesson worth keeping: D-020(4) cited a real number (19) and drew a wrong conclusion from it,
  because the number was never broken down per season or checked against the known-contaminated ids
  the *same session* had just found (F-042). A count is not a distribution.

## Review ledger

| Task  | security-auditor | logic-reviewer | ui-ux-reviewer | notes |
|-------|------------------|----------------|----------------|-------|
| T-006 | pending          | pending        | n/a            | r1 ⛔⛔ @34759ed: security F-044 (target's own result reachable via `history`) · logic F-051 HIGH (7 mutations survived; 3 change 5,000+ real vectors). Remediated — awaiting re-review. n/a: offline module, no user-facing surface |
| T-005 | ✅ 34759ed       | ✅ 34759ed     | n/a            | 4 rounds: ✅/⛔ @32110ce (F-026/F-027 HIGH) · ⛔⛔ @e2d2558 (F-037 broke real downloads) · ✅✅ @8eae86c · re-review ✅✅ @34759ed after `406dd09` touched loader.py (F-039 docstring; both reviewers proved it AST-identical, security re-ran the download from an empty dir). n/a: no user-facing surface |
| T-001 | ✅ 763101e       | ✅ 763101e     | n/a            | 4 rounds: r1 ⛔⛔@d8e3515 · r2 ✅✅@d0e661d · r3 logic ✅/security ⛔@fef3dc8 (F-019) · r4 ✅✅@763101e. n/a: no user-facing surface changed |

## Findings — status index

<!-- DERIVED VIEW, not history. The entries below this table are append-only and stay as written;
     this index is the one place that may be rewritten, and it is what you read to answer "is F-0NN
     still open?" without reading 43 entries in order.

     It exists because the ledger failed that question three times in one session (2026-08-10):
     F-024 was fixed in `32110ce` and still read OPEN; F-032 and F-035 were fixed and carried no
     `Status:` line at all. A finding closed in a commit message is not closed until the ledger says
     so — and until this table said so, "closed" was invisible to anyone who had not read the whole
     document. Adding a finding, or changing one's status, means updating this row too. -->

**Open / accepted — the live set (6):**

| ID | Sev | Area | Status | Fires when |
|----|-----|------|--------|-----------|
| **F-001** | HIGH | security | ACCEPTED — no authorization boundary exists anywhere in the app | `first-user-scoped-data` |
| **F-042** | MEDIUM | data | OPEN — 10 All-Star exhibition games in the corpus | **`T-007`** (next task) |
| **F-015** | LOW | ops | ACCEPTED — `ui-ux-reviewer` declared but not mechanically enforced | `reconcile-canon` |
| **F-006** | LOW | ops | OPEN — `docker-compose.prod.yaml` is a 0-byte file | — |
| **F-007** | LOW | ops | OPEN — `ci.yml` duplicates every gate check | — |
| **F-057** | MEDIUM | data/design | OPEN — `home_advantage` collinear with the intercept in early folds (see D-024) | `T-009` |

**Closed (54):** F-044 · F-045 · F-046 · F-047 · F-048 · F-049 · F-050 (T-006 security review, all FIXED in remediation) · F-051 · F-052 · F-053 · F-054 · F-055 · F-056 · F-058 · F-059 · F-060 (T-006 logic review, all FIXED; every surviving mutation re-run and now caught) · F-002 · F-003 · F-004 · F-005 (all CLOSED as moot or designed out by D-004/D-005) ·
F-008 · F-009 · F-010 · F-011 · F-012 · F-013 (FIXED @ `d0e661d`, T-001 round 1) · F-014 · F-016 ·
F-017 (CLOSED, T-001 round 3) · F-018 (FIXED, canon `be5f6dc`) · F-019 (FIXED, canon `6f49f29`) ·
F-020 · F-021 · F-022 · F-023 (FIXED @ `763101e`; F-021 project-local only — canon still ships the
unpinned range) · F-024 (CLOSED @ `32110ce`, recorded 2026-08-10) · F-025 (FIXED, canon) ·
F-026 · F-027 · F-028 · F-029 · F-030 · F-031 · F-032 · F-033 · F-034 (FIXED across `e2d2558`/
`8eae86c`, T-005 remediation) · F-035 · F-036 (FIXED, canon `c513a52`) · F-037 · F-038 (FIXED @
`8eae86c`) · F-039 (CLOSED @ `406dd09`) · F-040 · F-041 (FIXED @ `55dc586`) · F-043 (FIXED @
`34759ed`; **canon promotion still pending** — see *Future hardening*).

## Findings (from reviews, append-only)

<!-- F-001..F-007 are from the instantiation audit (2026-08-09), not from a gate review — recorded
     here so they survive the session. The review gate has not yet run on this repo. -->

- **F-001** (security, HIGH) — There is no authorization boundary anywhere in the app.
  `POST /nba/favorite/{team_id}` is unauthenticated and mutates a single globally-shared favorites
  table; any caller can add or delete any favorite. With no auth and no RLS, nothing else does.
  Remediation: introduce identity + per-user scoping before favorites (or any user-scoped data) is
  treated as real, and declare the `authz-deny` gate in the stack overlay at the same time.
  Status: ACCEPTED. revisit-when: `first-user-scoped-data` (any endpoint that stores per-user state).
- **F-002** (data, HIGH) — The staged Kaggle corpus (`../archive/nba.sqlite`) ends **2023-06-12**;
  three seasons (2023-24, 2024-25, 2025-26) are missing relative to today. A model trained on it
  cannot be backtested against, or fed by, the live ESPN feed without a backfill.
  Status: **CLOSED 2026-08-09 — moot per D-005** (corpus dropped and deleted; the replacement source
  reaches the completed 2025-26 season).
- **F-003** (performance, MEDIUM) — That SQLite file has **zero indexes** across 16 tables, including
  a 13.6M-row `play_by_play`. Every modeling query is a full scan until fixed.
  Status: **CLOSED 2026-08-09 — moot per D-005.** The lesson survives the corpus: whatever store
  T-002 builds must ship its indexes with the ingest, not after the first slow query.
- **F-004** (data, MEDIUM) — `other_stats` (pace / paint / fast-break / lead-changes — the richest
  modeling features in the corpus) covers only 28,271 of 65,698 games (~43%). Any feature built on it
  is missing-not-at-random by era. Status: **CLOSED 2026-08-09 — moot per D-005.** The lesson
  survives: check per-season coverage of any derived feature before modeling on it.
- **F-005** (integration, HIGH) — The app keys teams on ESPN `team.id`; the Kaggle corpus keyed on
  NBA.com `team_id` (`1610612737`+). No shared key. Abbreviations look like a bridge but disagree
  (`GSW/NYK/SAS/UTA/NOP/WAS` vs ESPN's `GS/NY/SA/UTAH/NO/WSH`), so a naive join silently drops teams.
  Status: **CLOSED 2026-08-09 — designed out by D-004**, which selects an ESPN-keyed source so no
  join is needed. revisit-when: `first-nba-stats-keyed-source` — adding any stats.nba.com-keyed data
  reintroduces this in full. If that happens, do not hand-roll the mapping:
  `sportsdataverse-data/releases/download/nba_crosswalk/nba_team_crosswalk_2026.csv` is a verified
  30-row `espn_team_id` → `nba_team_id` table carrying `match_method`/`match_confidence`.
- **F-006** (ops, LOW) — `docker-compose.prod.yaml` is a 0-byte file. It reads as a production config
  that exists; it is empty. Remediation: write it or delete it. Status: OPEN.
- **F-007** (ops, LOW) — `.github/workflows/ci.yml` now duplicates every check the `gate` job runs,
  costing a second full CI pass per PR. Remediation: retire `ci.yml` once `gate` is the required
  status check on `main`. Status: OPEN.

### Review round 1 — T-001 @ d8e3515 (both reviewers ⛔; all remediated at the SHA in the ledger)

- **F-008** (security, HIGH) — The builder agents were installed unmodified from Supabase canon.
  `backend-engineer.md` instructed builders that *"RLS ships with the schema — any new table's
  row-level-security policies go in the same migration"*, in a project whose central invariant is
  that no RLS, no Supabase and no auth exist. A builder would have shipped RLS on the first
  user-scoped modeling table and reported it `BUILT` believing rows were protected — doubly wrong,
  since the app connects as table owner and an owner bypasses RLS absent `FORCE ROW LEVEL SECURITY`.
  This is D-002's own reasoning ("a gate over a mechanism that does not exist reads as protection")
  defeated one layer up, and `gate-completeness` cannot catch it: it existence-checks agent *files*,
  never their content. Remediation: rewrote the domain guidance of `backend-engineer.md` and
  `frontend-engineer.md` from the overlay's builder notes, leading with the no-RLS invariant.
  Status: FIXED.
- **F-009** (security, MEDIUM) — `gate.yml` declared no `permissions:` block and checked out with
  `persist-credentials` defaulting to true, so `GITHUB_TOKEN` was written into `.git/config` where
  every manifest `run:` command and every npm lifecycle script could read it — in a job whose own
  header claims to hold no secrets. Same-repo branch PRs (this repo's model) get a write-capable
  token. Remediation: added `permissions: contents: read` and `persist-credentials: false`.
  Status: FIXED. **This gap is inherited from canon** `templates/ci/gate.yml` — see learning-notes.
- **F-010** (security, MEDIUM) — `ui-ux-reviewer.md` instructs the reviewer to *"not re-audit"*
  a11y/perf and to *"assume a green gate means they passed"* — but D-003 deliberately declares
  neither check. Both layers were off: nothing mechanical ran, and the only reviewer who would look
  was told to assume it had. Remediation: added a project override to the agent making a11y and perf
  the reviewer's own responsibility until the checks are declared. Status: FIXED.
- **F-011** (security, MEDIUM) — `.gitignore` covered `.env`, `.env.local`, `.env*.local` but **not**
  `.env.production` / `.env.development`, the exact names Next.js loads by convention. Verified with
  `git check-ignore`. Remediation: `.env*` plus `!.env.example`. Status: FIXED.
- **F-012** (logic, MEDIUM) — T-001's acceptance claimed the gate "exits 0 locally", but from the
  project's own documented instructions it exits **1**: `ruff` and `pytest` are absent from
  `requirements.txt` and installed nowhere but `backend/venv`. The prerequisite existed only as a
  historical aside in `log.md`. Remediation: `CLAUDE.md` now carries the venv build + `PATH`
  invocation as explicit setup. Status: FIXED.
- **F-013** (ops, LOW) — `.claude/settings.json` interpolated `$CLAUDE_PROJECT_DIR` unquoted into
  both Stop-hook commands. This repo's path is space-free today, but the workspace above it is not
  (`Client Projects/`), and a hook that exits 127 fails *open* and silently — the enforcement just
  stops. Remediation: quoted. Status: FIXED.
- **F-014** (ops, LOW) — The gate's own fixture tests (`checks/*.test.mjs`) run nowhere. `run-gate`
  imports `checks/lib/*` directly and never shells to vitest, and the CI job deliberately skips
  installing `checks/`. The meta-checks are what make the gate self-guarding; their tests are the
  only thing guarding *them*. `checks/package.json` also floats `vitest: ^2.1.0` with no lockfile.
  Status: ACCEPTED. revisit-when: `first-edit-to-checks-lib` — the moment anyone changes the gate's
  own logic, this stops being theoretical. Deferred rather than half-wired: `npm ci` needs a
  lockfile that does not exist yet.
- **F-015** (ops, LOW) — Declaring `ui-ux-reviewer` in the manifest implies more enforcement than
  exists: `checks/lib/tracker.mjs` hardcodes `mandatory = ['security-auditor','logic-reviewer']` and
  never consults the manifest's reviewer list, so a deleted `ui-ux-reviewer` column or an `n/a` cell
  silently exempts it while `gate-completeness` still reports "3 reviewer(s) installed".
  Status: ACCEPTED — canon-level, not project-level. revisit-when: `reconcile-canon`.

### Review round 2 — T-001 @ d0e661d (both reviewers ✅; F-008..F-013 verified closed)

<!-- Round 2 confirmed each round-1 fix was real rather than cosmetic, and opened two LOW residuals.
     These are deliberately NOT fixed in this cycle: every non-docs/ edit advances the code tip and
     invalidates the ✅@d0e661d that was just earned. Fixing them is the next cycle's opening move. -->

- **F-016** (ops, LOW) — `.gitignore`'s `models/` is an unanchored directory pattern, matching at any
  depth. Harmless today, but `backend/models.py` → `backend/models/` is a routine FastAPI refactor,
  and the package would land untracked and silent. Remediation: anchor it (`/models/` or
  `data/models/`). Status: OPEN. revisit-when: `next-non-docs-commit`.
- **F-017** (docs, LOW) — Three lines in `.claude/agents/ui-ux-reviewer.md` still contradict the
  F-010 override: `:38-39` calls the reduced-motion fallback "a check's job", `:42-43` frames
  mechanical a11y as out of lane, `:51` refers to "the a11y check's evidence" that does not exist.
  The override wins on placement and explicitness, so the fix holds — but the stale lines weaken it.
  Remediation: strike them. Status: OPEN. revisit-when: `next-non-docs-commit`.

> Also noted in round 2, folded into existing items rather than opened as new findings: `ci.yml` has
> the exact `permissions:`/`persist-credentials` gap that F-009 closed in `gate.yml`, and injects
> `secrets.DB_*` into a `pull_request`-triggered job — **F-007** already schedules its retirement, so
> retire it rather than harden it. And `frontend/.gitignore` has `.env*` with no `!.env.example`
> negation, asymmetric with the root fix; pre-existing, cosmetic until someone adds that file.

### Review round 3 — T-001 @ fef3dc8 (logic ✅ · security ⛔ — the F-018 fix was over-scoped)

- **F-018** (canon, HIGH) — `review-ledger-current` gated every `REVIEWED` **and `DONE`** task against
  the repo's *current* code tip, so any commit anywhere invalidated every completed task's review at
  once. A project with N done tasks owed N re-reviews per commit; the check was unusable past its
  first phase. Surfaced the instant Phase 1 tried to start: T-005's first code commit would have failed
  the gate on T-001, a finished and correctly-reviewed task. Verified empirically with
  `--code-head abc1234` before changing anything. Remediation: gate `REVIEWED` only — it means "passed
  review, awaiting merge", which is the stale-review case the check exists to catch — while `DONE`
  means "merged/shipped", frozen history that later unrelated work must not retroactively invalidate.
  Status: FIXED, and **promoted to canon** as Dev-System `be5f6dc`; CANON-VERSION re-stamped.
- **F-014** — CLOSED. Its `revisit-when: first-edit-to-checks-lib` fired when F-018 required editing
  `checks/lib/tracker.mjs`. Wired `meta-unit` into the manifest and CI, committed `checks/package-lock.json`,
  pinned vitest to 2.1.9 exactly. **The deferral was vindicated immediately**: the F-018 edit broke two
  existing fixtures that used `DONE` as their gated status, and no CI anywhere would have caught it.
- **F-016** — CLOSED. `models/` → `/models/`, anchored to the repo root.
- **F-017** — CLOSED. The three stale `ui-ux-reviewer` lines now agree with the project override rather
  than contradicting it; mechanical a11y/perf is stated as in-lane, and the reference to non-existent
  "a11y check evidence" is gone.

- **F-019** (security, MEDIUM) — **The F-018 fix removed too much.** `evaluateLedgerCurrency` did two
  independent jobs for gated tasks: *verdict* (a ledger row exists, every mandatory reviewer is present,
  every applicable cell is a ✅ with a SHA) and *currency* (that SHA still equals the code tip). Only
  currency is meaningless for frozen history; verdict never depended on the code tip at all. Dropping
  `DONE` from the gated set entirely removed both — leaving canon's "never `DONE` until `REVIEWED`" with
  **no mechanical enforcement anywhere** (the auditor traced it: `gate-completeness` ignores statuses,
  neither hook reads them, and branch protection's only status-aware check is this one). It also opened a
  one-word bypass: a `REVIEWED` task failing on a stale review could be cleared by editing its status to
  `DONE` — a change that moves *forward* along the intended state machine and was the cheapest way to
  make the gate green. Demonstrated with fixtures: `DONE` + all-pending, `DONE` + `⛔`, and `DONE` with no
  ledger row all passed. Remediation: keep `DONE` gated for verdict; scope only the currency comparison
  to `REVIEWED`; four regression tests added. Status: FIXED, promoted to canon as `6f49f29`.
- **F-020** (docs, LOW) — Three places still asserted the pre-F-018 guarantee, including
  `review-ledger-current`'s **success message**, which printed a claim the check no longer made — the
  operator-facing statement of what the gate proved. Also its module header and `BRANCH-PROTECTION.md`,
  the document a human reads to decide the gate is sufficient. Status: FIXED (all three now state the
  verdict/currency split).
- **F-021** (deps, MEDIUM) — vitest was pinned to 2.1.9 while this project's own Future-hardening item
  specified the frontend's 4.1.10, leaving two vitest majors in one repo and 5 npm advisories (1 critical,
  1 high) on the committed lockfile. Not reachable as configured — every advisory needs a listening
  dev/UI server and `vitest run` starts none — but it was the wrong version to pin to. Status: FIXED,
  now 4.1.10 matching the frontend; `npm audit` reports **0 vulnerabilities**.
- **F-022** (docs, LOW) — `.claude/CANON-VERSION` was re-stamped but its trailing prose still named the
  previous SHA as "the SHA stamped above". Status: FIXED — it now lists all three canon contributions.
- **F-023** (docs, LOW) — F-017 enumerated three lines and fixed exactly those; the same contradiction
  survived in two it did not name: the agent's frontmatter `description` (what the agent picker surfaces)
  and its "What you do NOT do" section, where a prohibition reads as more binding than corrected prose.
  Status: FIXED. Lesson: a finding that enumerates line numbers gets fixed at those line numbers — state
  the *claim* to eliminate, not its coordinates.

### Review round 4 — T-001 @ 763101e (both reviewers ✅; F-019..F-023 verified closed)

<!-- Verified by independent fixtures run against the committed evaluator, not against the repo's own
     tests. Two LOW residuals opened; deliberately NOT fixed here — both live in non-docs files, so
     fixing them would advance the code tip and invalidate the ✅ this round records. -->

- **F-024** (docs, LOW) — The JSDoc header of `evaluateLedgerCurrency` was updated to say "REVIEWED or
  DONE" but its second bullet was left unscoped, so the docstring still asserts currency applies to
  DONE — the exact claim F-020 existed to remove, contradicted by the code twenty lines below. This is
  the **F-023 lesson repeating within the same cycle**, and because the file is byte-identical to canon
  the inaccuracy is now inherited by every future instantiation. Status: OPEN.
  revisit-when: `next-non-docs-commit`.
- **F-025** (logic, LOW) — `parseTasks` fails **OPEN** on an unparseable status: `statusFromBlock`
  returns `null` for anything outside `STATUS_ENUM`, and `null` is not in the gated set, so a
  stale-review failure clears if the status is lowercased, misspelled, or deleted. Demonstrated:
  `` `reviewed` ``, `` `Reviewed` ``, and a removed status word all turn a failing fixture green. Note
  `BLOCKED` is live vocabulary — `next-command.sh` greps for it — yet is absent from `STATUS_ENUM`, so
  a `BLOCKED` task is silently ungated. Pre-existing; predates the whole F-018 line. Remediation: fail
  closed on an unrecognized status, and add `BLOCKED` to the enum as explicitly ungated.
  Status: FIXED in the T-005 PR — an unrecognized status now fails closed, and `BLOCKED` is
  recognized as a known-but-ungated flag. Promoted to canon.
- **Residual bypass, judged and accepted.** Flipping a stale-review `REVIEWED` task to `DONE` still
  clears currency — but it no longer clears *review*: a complete ledger row with every mandatory
  reviewer ✅ and a SHA is still required, so a `⛔` or pending task cannot be laundered this way. The
  auditor's judgment, which I accept: exempting DONE from currency *necessarily* makes declaring DONE
  an escape from currency; the only real alternatives are re-gating DONE (reopens F-018) or recording a
  per-task **done-at SHA** and comparing against that instead of the moving tip. The latter is a design
  addition, not a bug fix — filed in *Future hardening*.
- **Canon gap noted, not closed:** F-021 was fixed project-locally only. Canon still ships
  `checks/package.json` with an unpinned `vitest: ^2.1.0` and no lockfile, so every future project
  inherits the advisory-bearing 2.x range. Belongs in a canon promotion, out of scope for T-001.

### Review gate — T-005 @ 32110ce (security ✅ · logic ⛔)

<!-- T-005 stays BUILT. Per SYSTEM.md §5.4 the builder remediates and the reviewers re-run. Both HIGH
     findings were demonstrated by corrupting fixtures, not reasoned about — that is why they landed. -->

- **F-026** (logic, HIGH) — **The count assertion is bypassable.** `verify_completed_counts` is called
  only from `load_completed_games`; `load_season` — the function that downloads, parses and normalizes
  a season — never calls it. Any caller doing `loader.load_season(2022)`, which T-006/T-009 or an
  ad-hoc script would do naturally, gets **zero verification**. Demonstrated: a file truncated by ~300
  lines returned 1,266 games with no exception and no warning. The module docstring and the T-005
  outcome note both claim the counts are re-asserted "on every run"; they are not.
  Remediation: verify inside `load_season` so no path can obtain data without it firing.
  Status: **FIXED**. `load_season` now calls a new `_verify_season` (count + `game_id` uniqueness,
  F-027/F-028) directly before returning, so every path — `load_completed_games`'s loop and any direct
  caller — is verified identically. Reproduced against the pre-fix code (`git show 32110ce`) on a
  truncated 2022 fixture: 1,266 games, no exception, same as the original finding. Against the fixed
  code the same fixture now raises before returning: caught first by the new `EXPECTED_SHA256` content
  check (F-030) inside `download_season_csv`, and independently by `_verify_season`'s own count check
  when exercised directly (bypassing the download/hash layer) — `LoaderIntegrityError: season 2022:
  got 1266 completed games, expected 1324`. Module docstring corrected to describe the real call graph.
- **F-027** (logic, HIGH) — **"Right count, wrong rows" passes.** Verification is count-only; nothing
  asserts `game_id` uniqueness. Demonstrated: a 2022 file with one real completed game dropped and
  another duplicated in its place — net count unchanged at 1,324 — passed with no exception, one real
  game silently missing. This is the exact failure the tripwire exists to catch, and it is the reason a
  count is a weak substitute for a test. Remediation: assert `game_id` uniqueness per season before
  counting.
  Status: **FIXED**. `_verify_season` asserts `game_id` uniqueness (via `value_counts()`) before the
  count comparison. Reproduced the exact fixture against pre-fix code: dropped game_id `401361042`,
  duplicated game_id `401360941` in its place, count held at 1,324 — `load_completed_games` (the old
  code's only verified path) passed it silently. Against the fixed code the same fixture now raises:
  caught first by the `EXPECTED_SHA256` content check (F-030), and independently by `_verify_season`'s
  uniqueness assertion when exercised directly — `LoaderIntegrityError: season 2022: 1 duplicate
  game_id value(s) ... {'401360941': 2}`.
- **F-028** (logic, MEDIUM) — A season absent from `EXPECTED_COMPLETED_COUNTS` is silently unverified:
  the raising loop iterates `expected.items()`, so an unpinned season contributes nothing to check.
  `seasons=(2021,)` loads 1,172 rows with no signal. Not live today, but D-017's 2026-27 retrain adds a
  season and nothing keeps the two structures in sync. Remediation: fail when `actual` carries a season
  `expected` does not.
  Status: **FIXED**, at two independent layers. `_verify_season` refuses any season not present in
  `EXPECTED_COMPLETED_COUNTS` before checking counts/uniqueness (`load_season(2021)` now raises
  `LoaderIntegrityError`, tested against the real leftover `data/raw/nba_schedules/nba_schedule_2021.csv`
  on disk). `verify_completed_counts` independently rejects any season present in `actual` but absent
  from `expected` (`set(actual) - set(expected)`), so a hand-built `actual` dict is covered too, not
  only the `load_season` call path.
- **F-029** (logic, LOW) — `_validate_header` decodes with `errors="strict"`, so a non-UTF-8 response
  raises `UnicodeDecodeError` rather than the module's own `LoaderVerificationError`. Fails loudly,
  wrong type.
  Status: **FIXED**. The decode is wrapped in `try/except UnicodeDecodeError`, re-raised as
  `LoaderVerificationError` with `from exc` preserving the original traceback.
- **F-030** (supply-chain, MEDIUM) — **The release tag is stable but its assets are mutable, so the
  source is not pinned in the sense the code claims.** The auditor queried the GitHub API: the release
  dates from 2023-03-04, but `nba_schedule_2022.csv` — a completed historical season — carries
  `updated_at 2026-07-29`. sportsdataverse uses one release per dataset as a rolling CDN. A re-upload
  that corrects a score or team ID while leaving row counts identical passes **silently**, and that is
  the likeliest form of upstream revision. This directly defeats PLAN-v1 user story 20 (re-running
  months later reproduces the same numbers) and matters most at T-009, where the headline numbers are
  produced. Remediation: record a SHA-256 per season file and verify before parsing — that pins content
  rather than a filename. If deferred, correct the docstring so "pinned" is not read as "immutable".
  Status: **FIXED**. New `EXPECTED_SHA256` dict (next to `EXPECTED_COMPLETED_COUNTS`) pins a SHA-256
  per season, computed from the five files on disk that produced the verified 6,615-game count
  (`8cd13a11…`, `71aad62f…`, `ae89a6e5…`, `a7a5b660…`, `5a4a7473…` for 2022–2026 respectively). Checked
  in `_validate_content_hash`, called from `download_season_csv` on **both** the fresh-download and
  cached-file paths, before parsing. A mismatch raises `LoaderVerificationError` with a message that
  states plainly this is not transient, must not be retried or silently re-baselined, and that
  `EXPECTED_SHA256`/`EXPECTED_COMPLETED_COUNTS` must be updated deliberately by a human after
  re-verifying the new content. Both HIGH repros (F-026/F-027) are in fact caught by this check first,
  ahead of the count/uniqueness assertions, since both corruptions change file bytes. Docstring rewritten
  to state what "pinned" now actually means (content, not just tag/filename).
- **F-031** (input validation, LOW) — The size cap is not enforced on the cached path: only 8,192 header
  bytes are read and length-checked, so an oversized file already on disk short-circuits straight into
  the parser. The download path is correct (bounded read before anything touches disk).
  Status: **FIXED**. `download_season_csv`'s cached-file branch now calls `dest.stat().st_size` and
  raises before reading anything into memory if it exceeds `_MAX_DOWNLOAD_BYTES`; only then is the full
  file read (needed anyway for the F-030 content-hash check, which requires the full bytes, not a
  header peek). The now-unused `_HEADER_PEEK_BYTES` constant was removed.
- **F-032** (network, LOW) — Redirects were followed without asserting the final scheme/host, so a
  redirect to `http://` would silently drop TLS. Fixed by checking the post-redirect response URL
  against https and a host allowlist. **The host I named in this finding was assumed, not observed —
  see F-037, which is how that shipped broken.** The allowlist now carries the observed
  `release-assets.githubusercontent.com` (recorded with its observation date) plus `github.com` and the
  prior `objects.githubusercontent.com`.
  Status: **FIXED** at `8eae86c`, but only after F-037 corrected it — the control shipped in
  `e2d2558` refused every real download. Recorded here because this entry had carried no `Status:`
  line at all until the 2026-08-10 ledger audit: a finding whose remediation is described in prose
  but never given a status reads as done to a writer and as unresolved to a reader.
- **F-033** (input validation, LOW) — `season` is unvalidated and the `url.startswith(...)` guard that
  looks like it prevents redirection does not — a `..` segment passes it. Not exploitable today (the
  fixed filename prefix makes every traversal hit a non-directory, and `season` only ever comes from
  the module-level tuple), but the comment advertises a control that does not work.
  Status: **FIXED**. New `_validate_season` rejects any `season` not in `SEASONS`, called once at the
  top of `download_season_csv` (the single choke point both `load_season` and any direct caller go
  through). The ineffective `url.startswith(...)` check was removed and replaced in `_dest_path` with
  an assertion on the *resolved* path (`resolved_dest.is_relative_to(resolved_data_dir)`) — the check
  that actually proves the write stays inside the data dir, rather than one that only looked like it
  did. Verified `load_season(1999)` (outside `SEASONS`) is rejected before any URL/path is built.
- **F-034** (deps, LOW) — `certifi` is imported directly but declared only transitively via
  `requirements.txt`; a direct import should be a declared dependency.
  Status: **FIXED**. Added `certifi==2026.6.17` to `backend/requirements-train.txt`, pinned to the
  exact version already resolved transitively in `backend/requirements.txt` (via `httpx`) — adds
  nothing to the served image, only makes the training-side dependency explicit.
- **F-035** (gate tooling, LOW) — **Regression introduced by my own F-025 fix.** `parseTasks` starts a
  task block on *any* `**T-NNN**` match, including a bold cross-reference in prose, producing a phantom
  task with a null status — which now hard-fails the gate. Before F-025 the phantom was silently
  ungated. The current tracker is clean, so this is latent, but a maintainer writing "depends on
  **T-001**" in an acceptance bullet would hit a confusing block. Direction of failure is right, the
  diagnostic is wrong. Remediation: anchor the task-header match to the start of a list item.
  Status: **FIXED**, canon `c513a52` — the same commit as F-036. Verified at the 2026-08-10 ledger
  audit against the running code: `parseTasks` matches `/^\s*-\s*\[[ xX]\]\s*\*\*T-(\d+[a-z]?)\*\*/`,
  and `checks/review-ledger-current.test.mjs` carries the regression test. This entry had carried no
  `Status:` line until that audit, even though the canon commit message names the fix.
- **F-036** (gate tooling, LOW) — `knownUngated` hand-duplicates `STATUS_ENUM` minus `gated`; adding a
  status to the enum without editing the literal hard-fails every task at that status. Fail-closed, but
  a trap. Remediation: derive it. Status: FIXED, canon c513a52.

### Re-review — T-005 @ e2d2558 (both reviewers ⛔ on the same regression)

- **F-026, F-027 — CLOSED and independently verified.** The logic reviewer did not accept that the
  count/uniqueness checks were fixed just because the new SHA-256 layer caught its fixtures: it
  *neutralised the hash layer* (repinning the hash to the corrupted file's own value) and re-ran, proving
  `_verify_season` catches both a truncated season (`got 1319, expected 1324`) and the drop-one/dup-one
  case (`1 duplicate game_id ... {'401360941': 2}`) on its own. That matters because a legitimate
  upstream refresh will require re-baselining the hashes, and the count logic must still stand.
- **F-028, F-029, F-030, F-031, F-033, F-034 — CLOSED**, each verified against the running code.
- **F-037** (availability / control integrity, HIGH) — **The F-032 redirect allowlist named a host
  GitHub no longer uses.** Release assets now redirect to `release-assets.githubusercontent.com`, not
  `objects.githubusercontent.com`, so `download_season_csv` refused every real download. Both reviewers
  reproduced it live and independently. It shipped because `data/` was already populated, so **the
  download path never executed** in any local run, in the builder's own testing, or in the gate — and
  the gate structurally cannot see it, since nothing outside `loader.py` calls the loader. The host I
  put in the F-032 write-up was assumed, not observed. Status: FIXED — allowlist now records the
  observed host with the date it was observed, and the prior host is retained. **Verified the only way
  that counts: a real download into an empty directory** (2023 → 1,776,788 bytes → hash-verified →
  1,321 completed games).
- **F-038** (error handling, LOW) — `EXPECTED_SHA256[season]` was indexed unguarded, so a season with
  counts but no pinned hash raised a bare `KeyError` instead of an actionable error — the same
  inconsistency F-028/F-029 fixed in the other two verification paths. Status: FIXED.

> **Acceptance note added for T-005, from the auditor's observation:** "gate green" is not evidence the
> download works. The loader is invoked by nothing outside itself, and every local run hits the cache.
> Any change to the download path must be verified by a run against an **empty** data dir.

### Final re-review — T-005 @ 8eae86c (both reviewers ✅)

- Download verified from a **fresh empty directory** for seasons not previously exercised (2024, 2025):
  bytes arrived, SHA-256 matched the pins, counts verified. Five-season load from empty: 6,615 games,
  6,615 unique `game_id`. The allowlist was probed as a control (8 cases) — it evaluates the
  post-redirect URL, rejects scheme downgrade, subdomain and userinfo tricks, and fires *before* the
  response body is read.
- **F-039** (docs, LOW) — `loader.py`'s module-level security note still names
  `objects.githubusercontent.com` as the redirect target: the exact stale claim F-037 was about, left
  uncorrected 60 lines above the constant that was fixed. Code correct, narrative stale.
  Status: OPEN. revisit-when: `next-non-docs-commit` (it lives in a `.py` file, so fixing it here would
  have moved the code tip and invalidated the ✅ this round records).
- **F-040** — FIXED in this commit (the F-032 entry had an unbalanced parenthesis and still enumerated
  a two-host allowlist the code no longer uses).
- **F-041** — FIXED in this commit. The empty-dir requirement had landed in the Findings section rather
  than in T-005's `acceptance:` bullet, where a builder would actually read it. Now in both.

### T-006 build session (2026-08-10) — findings opened by building, not by a review

- **F-039** — **CLOSED** in the T-006 commit, as its `next-non-docs-commit` trigger required. The
  loader's module-level security note no longer restates the allowlist's contents; it points at
  `_ALLOWED_DOWNLOAD_HOSTS` as the authoritative list and records *why* restating it is how the note
  came to name a host GitHub had stopped using. Fixing the claim rather than the coordinates, per the
  F-023 lesson.
- **F-024** — **CLOSED, and it was already fixed before this session.** The JSDoc of
  `evaluateLedgerCurrency` was correctly scoped in `32110ce` ("fix(canon): … scope JSDoc (F-024)"),
  but the finding was left reading `Status: OPEN` here. Verified against the committed file: the
  header now states the verdict/currency split and matches the code. The tracker was stale, not the
  code. Lesson: a finding closed in a commit message is not closed until the ledger says so.
- **F-042** (data, MEDIUM) — **The corpus contains 10 All-Star exhibition games.** Not a T-006 defect;
  found while verifying it against real data. The five seasons carry **42 distinct team ids, not 30**.
  Exactly 30 ids play ≥82 games per season; 12 phantom ids appear in 1–3 games each, all dated
  All-Star weekend, with tell-tale scores (2024's 211–186; the 2025/2026 mini-tournament formats at
  41–32, 42–35, 21–47). They are `season_type = 2` upstream, which is why T-005's pinned counts
  include them. **Feature computation is provably unaffected**: phantom and real ids do not overlap,
  and **zero games mix a phantom with a real id**, so no NBA team's form, rest or point differential
  reads an All-Star result. The exposure is downstream — these become 10 training/evaluation rows
  (0.15%) whose features are all-priors and whose labels are coin flips, in T-009's headline numbers.
  Remediation: exclude them in T-007/T-009, not here. The separation is unambiguous (≥82 games vs ≤3,
  no overlap in any season), so a per-season minimum-games threshold is safe. Do **not** filter inside
  `loader.py` or `dataset.py` without deliberately re-baselining `EXPECTED_COMPLETED_COUNTS` — the
  6,615 count is a pinned tripwire and silently changing what it counts defeats it. Status: OPEN.
  revisit-when: `T-007` (fold generation) — whichever of T-007/T-009 lands first owns the filter.
- **F-043** (gate tooling / process, MEDIUM) — **`review-ledger-current`'s currency rule does not
  survive a multi-task branch: F-018's failure shape, one layer over.** Demonstrated before committing,
  with `node checks/review-ledger-current.mjs --code-head deadbee`: both of T-005's ✅@`8eae86c` fail
  as stale. The check compares every `REVIEWED` task's ✅ against a **repo-wide** code tip, so any
  non-docs commit anywhere invalidates it — including one that touches no file T-005 owns. Phase 1
  lands T-005…T-009 on one branch, so this compounds exactly as F-018 did: once T-006 is `REVIEWED`,
  T-007's first commit invalidates both, and so on. The escape valve D-018 leaves is flipping a task
  to `DONE`, which is exempt from currency — but `DONE` means "merged/shipped", and T-005 is not
  merged, so writing it would put a false claim in the source of truth. **This is the residual F-019
  already identified**: exempting DONE from currency necessarily makes declaring DONE the escape, and
  the real fix is a per-task **reviewed-at/done-at SHA** compared against that task's own last-touched
  commit rather than the moving tip — already filed in *Future hardening*, and now with a second,
  sharper motivation than the bypass it was filed for. Needs a human call; options in order of
  honesty: (a) implement the per-task SHA (canon change, fixes it for every project stamped from this
  canon); (b) merge the T-005 work to `main` and flip it `DONE`, matching T-001's precedent
  (`23a9a88` merge → `8ef637b` DONE) — correct but front-loads a merge mid-phase; (c) accept a red
  `review-ledger-current` for the rest of Phase 1, which trains the team to ignore the one check that
  makes the reviewer gate falsifiable, and is the worst option.
  **Status: FIXED — option (a), chosen by the human.** `evaluateLedgerCurrency` now accepts an
  injected `opts.staleAt(taskId, sha)` and asks, per task, whether any commit has touched *that task's
  own files* since its ✅. The repo-wide comparison survives as the fallback and under `--repo-wide`.
  The evaluator stays pure — git lives in the CLI, which derives ownership from commit **subjects**
  (`T-006: …`, `fix(T-005): …`), never bodies. 9 new fixture tests; 53 pass, including all 44
  pre-existing, so the change is backward-compatible. See **D-023** for the two forks this exposed.
  **Consequence to read before re-reviewing:** the gate is *still* red on T-005, and now correctly so.
  `406dd09` edited `backend/model/loader.py` (the F-039 docstring fix), a file T-005 owns, so its
  review is genuinely stale rather than spuriously stale — F-039's own entry predicted exactly this
  cost. The complaint changed from "the tip moved" to "`406dd09` touched this task's files", which is
  a true and specific statement. T-005 needs a cheap re-review (one docstring diff); T-006 needs its
  first. **Not yet promoted to canon** — see *Future hardening*.

### Review gate — T-006 @ 34759ed (security ⛔) · T-005 re-review @ 34759ed (security ✅)

<!-- T-006 stays BUILT; per SYSTEM.md §5.4 the builder remediates and the reviewer re-runs. Every
     finding below was reproduced by the auditor with a runnable script, not argued — F-044, F-045,
     F-046, F-048, F-049 and F-050 are all marked CONFIRMED with output. -->

- **T-005 re-review: ✅.** The auditor did not take "it's only a docstring" on faith. It stripped
  docstrings and compared ASTs, then compared every code object in the module recursively: the sole
  difference in `406dd09` is the module docstring string constant, with `download_season_csv`,
  `_validate_header`, `_validate_content_hash`, `_verify_season` and `load_season` byte-for-byte
  identical. It then honored F-041 anyway and ran the download path against a genuinely **empty**
  temp dir — 2023 → 1,776,788 bytes → hash matched the pin → 1,321 completed; 2024 → 1,320 — leaving
  the real corpus read-only and byte-identical. Also confirmed the new docstring is *accurate*: it
  stops restating the host set and points at `_ALLOWED_DOWNLOAD_HOSTS`.
- **F-044** (integrity/leakage, MEDIUM) — **CONFIRMED. The target's own result IS reachable through
  `history`.** T-006's security note claims the scoreless `Matchup` makes it "unreachable by any bug".
  That closes the *direct* door only: the scores live in `history`, and `compute_features` never
  compares `target.game_id` against the filtered window. The only thing keeping a target out of its
  own features is the *coincidence* that `Matchup.date` equals its `Game.date` — a caller-side data
  property, which is exactly the delegation the security note forbids. Demonstrated: with the target
  in history as a 200–80 blowout and `as_of` one hour after tip-off, `point_diff_diff` moves 6.0 →
  32.0. Not hypothetical via the new seam either — `to_pydatetime()` truncates nanoseconds, so a
  caller using the frame's own `Timestamp` as `as_of` is already past `Game.date` (the pinned corpus
  is minute-precision, so it does not fire today). Remediation: refuse or drop records whose
  `game_id == target.game_id`, and correct the docstring's claim.
- **F-045** (integrity, MEDIUM) — **CONFIRMED. A total lookup miss is indistinguishable from D-015's
  legitimate cold start.** Three triggers, none of which raises: a one-shot iterator (the declared
  `Iterable[Game]`) reused across calls returns priors on the second call; `Matchup.season` as `"2024"`
  vs `Game.season` as `2024`; team ids typed `str` in history and `int` in the target. Each silently
  yields `form_diff 0.0, point_diff_diff 0.0` — byte-identical to what opening night legitimately
  produces, because D-015 deliberately removed the dropped-games symptom that would have exposed it.
  Failure scenario: T-009's headline reads "four features carry no signal, no-ship" when the truth is
  "the pipeline is broken" — the exact silent failure PLAN-v1 §2 exists to design out. Remediation:
  type `history` as `Sequence[Game]` (or refuse a consumed iterator) and validate season/team-id types
  at the boundary. **Do not simply refuse unknown teams** — that would break the genuine cold start.
- **F-046** (integrity, MEDIUM) — **CONFIRMED. Nothing asserts `game_id` uniqueness across the
  *combined* collection.** T-005 checks per season; `verify_completed_counts` keys `actual` by season,
  so a repeated season collapses to one key and passes. Demonstrated on the real corpus:
  `load_games((2022, 2022))` → **2,648 games, 1,324 unique ids, no error**, and a target's
  `point_diff_diff` shifts 4.400 → 4.755. This is F-027's "right count, wrong rows" one layer
  downstream, and `GameHistory`'s docstring claims it is "a pure function of the **set** of input
  games" when it is a function of the multiset. Remediation: assert uniqueness in
  `GameHistory.__init__` — it already iterates every game, and it is the only place covering every
  path in. Scope note from the auditor: `verify_completed_counts` is byte-identical to what was
  reviewed at `8eae86c`, so this does **not** reopen T-005; it is filed against T-006 because
  `dataset.load_games` is the new entry point that surfaces it.
- **F-047** (integrity, LOW) — missing check CONFIRMED, exploit PLAUSIBLE. `date` is **tip-off**, not
  completion, so the contract "games completed strictly before `as_of`" is really "games that tipped
  off before". `Game` carries no status field. Harmless in Phase 1 — the auditor measured every
  consecutive-game gap in the corpus (min 0.50h, median 48h) and all seven sub-12h gaps belong to
  F-042's All-Star phantom ids, never a real team. But D-011 puts one `games` table behind training
  and inference, and the app's enum is `scheduled|live|final`; a Phase-2 caller that forgets to filter
  `status == 'final'` feeds a live partial score into a "pre-game" vector. Remediation: state it as a
  precondition, or give `Game` a final-only construction path so Phase 2 cannot forget.
- **F-048** (input validation, LOW) — **CONFIRMED.** `dataset.games_from_frame` coerces with bare
  `bool()`/`int()`/`str()` despite claiming "no reinterpretation": `neutral_site` as the *string*
  `'False'` becomes `True`, a float score `118.9` truncates to `118`, and a plain-`str` date column
  dies with a raw `AttributeError`. The inverted `neutral_site` matters most — it would make
  `home_advantage` a constant `0.0` and silently delete the model's only guaranteed feature, and
  `test_neutral_site_survives_as_a_real_bool` only exercises `True`. Nothing is wrong at `34759ed`
  (the loader emits a real bool); this is a gap in the seam that exists to *be* the trust boundary.
- **F-049** (input validation, LOW) — **CONFIRMED.** `Game.__post_init__` validates awareness,
  self-play and ties but not that scores are numbers. `float('nan')` scores pass the tie check
  because `nan != nan`; `to_vector` then propagates NaN and inf, and accepts the string `"3.0"`. Not
  reachable from the loader path (`.astype(int)` raises on NaN — verified). Remediation: require
  integral scores; assert `math.isfinite` in `to_vector`.
- **F-050** (integrity, LOW) — **CONFIRMED.** `GameHistory.of` uses `isinstance`, so a **subclass** is
  returned unwrapped and `compute_features` calls its `_records_before`. A 6-line subclass overriding
  that method leaks a future game — the literal counterexample to the docstring's "no code path in
  this module reads a game dated at or after `as_of`". Remediation: `type(history) is cls`.

> **What the auditor tried to break and could not** — recorded because it is as load-bearing as the
> findings. It neutered `_records_before` in memory two ways (`bisect_right`, and no filter at all)
> and re-ran the suite: **both mutations were caught by the leakage property test**, so that test is
> not vacuous and the builder's control is genuine. Timezone handling survived re-expressing `as_of`,
> the target and the whole history in `+05:00` — bit-identical output. `GameHistory` genuinely holds
> no as-of state (prebuilt index ≡ raw sequence across 14 real games). Leakage re-derived
> independently on 133 real corpus games: 0 mismatches. D-021 verified *faithfully* — the auditor
> noted pytest 9.1's `importorskip` defaults to `ModuleNotFoundError`, blocked pandas/numpy/pyarrow/
> dateutil/six, and got **52 passed, 1 skipped**; importing `model.features` loads **zero**
> site-packages modules. No pickle/yaml/eval/subprocess/network/filesystem access in either new
> module. T-005's verification chain is not weakened by the new entry point: `(1999,)`, `(2021,)` and
> `(2027,)` all raise before any URL or path is built. Cost is linear and flat (~0.06–0.10 µs/game;
> a full T-009-shaped pass over 6,615 games takes 0.12 s, 0 non-finite vectors).

> **Noted for later, not filed as findings:** `_form`/`_season_point_diff` re-scan the team's whole
> filtered history per call (`[r for r in records if r.season == season]`), which partly negates the
> bisect `GameHistory` justifies itself with — measured harmless, an efficiency/altitude call. The
> `max(gap, 0.0)` clamp in `_rest_days` is unreachable dead code, since
> `records[-1].date < as_of ≤ target.date` guarantees a positive gap. And `backend/models.py`
> (SQLAlchemy) vs `backend/model/` (this package) is a one-character import-path collision that will
> bite when Phase 2 imports both into the FastAPI service — pre-existing, out of T-006's scope.

### Review gate — T-006 @ 34759ed (logic ⛔) — mutation testing found the tests, not the module

<!-- The logic-reviewer's verdict is worth reading in full: the MODULE passed every acceptance
     criterion it could independently verify, including recomputing the golden fixture by hand. What
     failed was the TEST SUITE. It ran ~60 mutations; 39 were caught, 7 survived, and 3 of the
     survivors change 5,096 / 5,809 / 5,283 of the 6,615 real feature vectors while every test still
     passes. PLAN-v1's Testing Decisions is the standard: "the tests that matter are the ones that
     would fail if the module were subtly wrong in the ways this domain fails." -->

- **F-051** (logic/tests, **HIGH**) — **CONFIRMED. Nothing asserted that rolling form is *rolling*.**
  Reversing the window to the *oldest* `FORM_WINDOW` games (`[-FORM_WINDOW:]` → `[:FORM_WINDOW]`)
  passed all 46 tests, and changes **5,096 / 6,615** real vectors. Root cause is a fixture flaw, and
  it is mine: every form fixture was a *uniform* streak (15 straight wins, 10 identical +2s), so the
  first ten games and the last ten were numerically identical by construction. The feature would have
  been the opposite of the one T-006's acceptance names, and T-009 would have trained on it silently.
  Status: **FIXED** — new `_home_results("LLLLLWWWWWW")` helper builds non-uniform records, and two
  tests pin magnitude *and* direction (a reversed record must flip the sign). Mutation re-run: CAUGHT.
- **F-052** (logic/tests, MEDIUM) — **CONFIRMED.** The shrinkage count `n` for season-to-date point
  differential was unpinned: capping it at `FORM_WINDOW` (**5,809** vectors differ) and using the
  all-seasons record count (**5,225** differ) both passed. The old test asserted only a *direction*
  (`with_earlier > windowed_only`), which both mutations satisfy. Status: **FIXED** — literals pinned
  for a 12-game season (`12/17 × mean`), plus a prior-season fixture that separates the all-seasons
  count specifically. Both mutations re-run: CAUGHT.
- **F-053** (logic/tests, MEDIUM) — **CONFIRMED.** `compute_training_features`'s `as_of` — the single
  entry point T-009 uses for *every* training row — was pinned by nothing. Substituting
  `game.date - 1 day` or midnight-of-tip-off-day both passed, because the golden fixture has no game
  between day 9 and day 10 and every fixture `as_of` was midnight. Status: **FIXED** — a game three
  hours before tip-off now sits inside the window, so any coarsening is detectable. Both: CAUGHT.
- **F-054** (logic/tests, MEDIUM) — **CONFIRMED, and the sharpest of the set: the test written to
  catch this could not catch it.** `test_leakage_a_game_one_microsecond_before_as_of_is_included`
  claims to guard against "a coarser day-level comparison", but the whole fixture was anchored at
  `datetime(2024,1,1)` — midnight — and every `as_of` was a whole-day offset, so truncating `as_of`
  to midnight was a *no-op for the fixture*. Not theoretical: the corpus has **83 team-days with two
  games on the same UTC date** and **5,360 / 6,615** games tip off at a non-midnight instant; the
  mutation changes 77 real vectors. Status: **FIXED** — the fixture epoch is now `19:00Z`, which
  re-arms the existing microsecond test *and* F-053's mutations, plus an explicit same-UTC-date
  before/after pair. CAUGHT.
- **F-055** (logic/tests, MEDIUM) — **CONFIRMED.** `test_to_vector_emits_features_in_the_declared_order`
  built its expected tuple *from* `FEATURE_NAMES`, so both sides moved together and
  `tuple(features.values())` passed. Latent today (the dict literal happens to match) but `to_vector`
  is documented as accepting *any* mapping and T-009's coefficients line up positionally.
  Status: **FIXED** — asserts hardcoded literals in a fixed order, and feeds in a deliberately
  reversed mapping. CAUGHT.
- **F-056** (logic/tests, MEDIUM) — **CONFIRMED.** `dataset.py`'s `season` was not pinned by the test
  that claims to pin the field mapping: `_frame()` used `season=2022`, so hardcoding `season=2022` in
  the adapter passed — while changing **5,283 / 6,615** real vectors, because season scoping drives
  both accumulating features. Status: **FIXED** — fixture moved off 2022, plus a two-row/two-season
  assertion. CAUGHT.
- **F-057** (data/design, MEDIUM) — **CONFIRMED, and it corrects a decision.** See **D-024**, which
  supersedes D-020(4). Not a T-006 code change; a constraint on T-009/T-010. Status: OPEN,
  revisit-when: `T-009`.
- **F-058** (docs/tests, LOW) — **CONFIRMED.** `GameHistory`'s docstring said the `(date, game_id)`
  tiebreaker made the index "a pure function of the *set* of input games ... **asserted in the
  tests**". It was not asserted — dropping the tiebreaker passed. Status: **FIXED**, and the first
  fix attempt *also* failed the mutation: two same-instant games both inside the form window
  contribute identically however they are ordered. The tiebreaker is only observable when a
  same-instant pair **straddles** the window boundary, so the test now uses 11 season games with the
  pair at the oldest position. CAUGHT.
- **F-059** (tests, LOW) — **CONFIRMED.** The `neutral_site` defaults on `Matchup`/`Game` were never
  exercised (helpers always passed the flag), so flipping the default to `True` passed all 46 tests.
  Status: **FIXED**. CAUGHT.
- **F-060** (code hygiene, LOW) — **CONFIRMED.** Two unreachable defensive branches: `_rest_days`'s
  `max(gap_days, 0.0)` (records are strictly before `as_of ≤ tip_off`, so the gap is always positive)
  and `_shrink`'s `n <= 0` guard. Status: **FIXED** for the first — removed, with the invariant that
  actually holds written down instead. **Deliberately kept** for `_shrink`: it is the documented
  contract ("n=0 → the prior exactly, and `observed` is never read"), so it is a stated behavior
  rather than dead defense, and callers outside this module may rely on it.

> **What the logic reviewer verified and could not break** — 39 mutations caught, including every
> sign flip, both season filters, the rest cap, the rest-to-tip-off rule, `k`, the window cap, the
> home indicator, D-022's Game-as-target refusal, and both adapter id/score swaps. It recomputed the
> golden fixture by hand and confirmed all three literals. It confirmed the leakage property test
> catches a leak weighted at **1e-9** and could not construct one the test missed. It re-derived the
> leakage guarantee on 300 randomly sampled real games: 0 mismatches. Every corpus number in this
> tracker reproduced to the digit. **One caution it put on its own record:** mutation `P05` is listed
> as caught but failed on an `AttributeError` from `__slots__`, not on the leakage assertion — it
> explicitly said not to count that as evidence. That is the standard this project should hold.

### T-006 remediation @ (this commit) — all 10 surviving mutations now caught

- Every finding above and F-044..F-050 is addressed in code or tests. **Verified by re-running the
  reviewers' own mutations**, not by inspection: a harness applies each of the 10 survivors, runs the
  suite, and restores the file — **10/10 CAUGHT** (two needed a second attempt: M28, as F-058 records,
  and N13). Suite grew 58 → 79. Ruff clean. CI simulation with pandas/numpy blocked: 68 passed,
  1 skipped — D-021 still holds.
- Real-corpus re-verification after remediation is **unchanged to the digit**: 6,615 games, 19
  neutral, home win rate 0.55556, `form_diff` +0.0433/−0.0582, `point_diff_diff` +1.8922/−2.4415,
  leakage exact on 120 sampled real games. The fixes changed what is *refused*, not what is computed.
- F-046 landed at **two** layers, matching T-005's download/cached-path discipline: `GameHistory`
  protects the features, `games_from_frame` protects the *count* — without the second,
  `load_games((2022, 2022))` still returned 2,648 records to anything that merely counted them, and
  T-009's headline would have quoted a doubled number that never reached a `GameHistory`.

## Future hardening (review output → next-cycle backlog)

- Install Playwright and declare the `a11y` check; tune `checks/reference/perf-budgets.json` for this
  app and declare `perf`. Both are canon gates this project currently runs without.
- `GET /nba/games` returns a bare `LIMIT 20` with no pagination or date filter — fine for a scoreboard,
  insufficient once historical games are queryable.
- The APScheduler sync runs in-process in the API container; a second replica would double-sync. Move
  to a single scheduled worker before scaling out.
- ~~Wire the gate's own tests (F-014)~~ — **done** in the F-018 cycle: lockfile committed, vitest
  pinned to 4.1.10 (matching the frontend), `meta-unit` declared and running. The gate runs 8 checks.
- `gate.yml` pins Node 22 while the legacy `ci.yml` pins Node 20. Resolves itself when F-007 retires
  `ci.yml`; until then two workflows run every check on different Node majors.
- Constrain T-002's store location: acceptance says "the store is gitignored", but only `data/`,
  `models/` and the listed extensions are. Require the store under `data/` so the claim is structural
  rather than dependent on remembering to add an extension.
- ~~Per-task reviewed-at SHA in `review-ledger-current`~~ — **done** in the T-006 cycle (F-043, D-023).
  F-019 had recorded it as "filed in *Future hardening*" when it was never actually written down here
  — a reminder that "filed" is a claim the ledger has to be able to back.
- **Promote the F-043 fix to canon, then re-stamp `.claude/CANON-VERSION`.** `checks/lib/tracker.mjs`,
  `checks/review-ledger-current.mjs` and `checks/review-ledger-current.test.mjs` are now ahead of
  canon `c513a52`. Every project stamped from this canon carries the compounding bug, so this belongs
  upstream like F-018/F-019/F-025/F-035/F-036 did. **Deliberately not done in this session:** the
  factory is `~/Desktop/LabRoomV2.0/Client Projects/Dev-System` (verified clean at `c513a52`), a
  different repo outside this project — a promotion is a decision to make on purpose, not a side
  effect of a build session. Note `Sports/Dev-System/` is a *stale copy* at `bd58a22`, not the
  factory; do not promote there.
- **Residual bypass F-019 named is now narrower but not gone.** Flipping a stale `REVIEWED` task to
  `DONE` still escapes currency, since DONE remains exempt by design (F-018). What changed is that the
  incentive largely evaporates: currency now only fires when the task's *own* files changed, which is
  a real re-review rather than an accident of someone else's commit. Closing it fully still needs a
  recorded done-at SHA per task.
- **`backend/model/dataset.py` is covered only by tests that skip in CI** (D-021 — pandas is
  training-only). `backend/tests/test_dataset.py` pins the field mapping locally and skips in CI;
  verified by running the suite with pandas/numpy blocked (52 passed, 1 skipped). Same accepted
  exposure PLAN-v1 records for `loader.py`. Revisit if the gate ever installs the training
  requirements, or if the adapter grows logic beyond a field-by-field conversion.

---

## Tracker rules (the contract every agent follows)

1. **Read before acting.** Read *Current state* and *Architecture snapshot* before doing anything.
2. **Write on completion.** When you finish a task you MUST: set the task's status, OVERWRITE *Current
   state*, append any *Decisions* or *Findings*, append one line to `docs/log.md` (the chronology), and
   `git commit` the change. (Enforced by a Stop hook.) Narrative goes in `log.md`, not *Current state*.
3. **Append-only logs stay append-only.** Decisions and findings are superseded, never erased. The
   one exception is the *Findings — status index*, which is a derived view and is rewritten in place:
   every new finding gets a row, and every status change updates one. A finding closed in a commit
   message is not closed until the ledger says so (F-024, F-032, F-035 each proved this).
4. **One tracker per project, committed to git.** The git history is the versioning of this state.
