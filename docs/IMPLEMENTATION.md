# Implementation Tracker — Sports Dashboard

> Source of truth across sessions. Read this first every session.
> Last updated: 2026-08-09 by human + Claude (instantiation session)

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

**State now:** Phase 1 is underway on `feat/phase-1-analytical-core`. T-005 (loader) is `BUILT`,
remediated, and **awaiting re-review** — F-026..F-034 are fixed in `backend/model/loader.py` (F-035/
F-036 in `checks/` were already fixed by the main thread). Both HIGH findings were re-demonstrated
against the pre-fix code (`git show 32110ce:backend/model/loader.py`) to confirm the exact fixtures
still reproduce them, then run again against the fixed code to confirm both now raise: F-026
(`load_season` bypassing verification — truncated-file fixture, was 1,266 games/no exception, now
raises before returning) and F-027 (count-only verification — drop-one/duplicate-one fixture that held
the count at 1,324, now raises before returning). Each was additionally isolated at the specific layer
its finding named (the count/uniqueness assertion inside `_verify_season`), independent of the new
F-030 content-hash check that also happens to catch both. F-028 (unpinned season silently unverified)
verified closed at two layers; F-029/F-031/F-032/F-033/F-034 (LOW) all fixed — see the Review gate
section below for what changed in each. Full gate green, 8/8. `git status` clean; no `data/` files
were modified (corrupted fixtures live only under the scratch dir used for the repro, never committed).

**Next action:** Re-run both reviewers (security-auditor + logic-reviewer) against this remediation.
T-005 cannot advance past `BUILT` until logic clears — this thread does not self-grant `REVIEWED`.

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

- [ ] **T-005** Historical data loader — `BUILT` — owner: `backend-engineer`
      - acceptance: downloads 2022–2026 season schedules from a pinned upstream release tag into the
        ignored data dir; normalizes to a completed-game collection; per-season counts match
        (2022 → 1,324; 2026 → 1,326); non-final games excluded; re-running is idempotent — **all met**
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
- [ ] **T-006** `features` deep module + tests — `PLANNED` — owner: `backend-engineer`
      - acceptance: one interface (history, target game, as-of) → feature mapping; rolling form, rest
        days, season-to-date point differential, home indicator, all as home-minus-away differences
        with `n/(n+k)` shrinkage; module is pure (no I/O, no clock, no DB); golden fixtures pass;
        **leakage property test passes**; shrinkage boundaries at 0, 1, `k` pass
      - security note: the as-of filter is an integrity control, not a convenience — it is what makes
        every reported number honest. Enforce it inside the module, never delegate to callers, so no
        future call site can opt out.
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

## Review ledger

| Task  | security-auditor | logic-reviewer | ui-ux-reviewer | notes |
|-------|------------------|----------------|----------------|-------|
| T-005 | ✅ 32110ce       | ⛔ F-026/F-027 | n/a            | loader's count assertion is bypassable (`load_season`) and count-only (drop+dup passes). Security ✅ with F-030 MEDIUM on source mutability |
| T-001 | ✅ 763101e       | ✅ 763101e     | n/a            | 4 rounds: r1 ⛔⛔@d8e3515 · r2 ✅✅@d0e661d · r3 logic ✅/security ⛔@fef3dc8 (F-019) · r4 ✅✅@763101e. n/a: no user-facing surface changed |

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
- **F-032** (network, LOW) — Redirects are followed without asserting the final scheme/host; a redirect
  to `http://` would silently drop TLS. Reachability is low (requires controlling GitHub's TLS-verified
  response) and the redirect itself is required, so it cannot simply be disabled.
  Status: **FIXED**. After `urlopen` follows redirects, the final response URL (`response.geturl()`) is
  parsed and checked: scheme must be `https`, host must be in a new `_ALLOWED_DOWNLOAD_HOSTS` allowlist
  (`github.com`, `objects.githubusercontent.com` — the actual GitHub → release-CDN redirect chain).
  Either check failing raises `LoaderVerificationError` before the response body is read.
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
- **F-036** (gate tooling, LOW) — `knownUngated` hand-duplicates `STATUS_ENUM` minus `gated`; adding a
  status to the enum without editing the literal hard-fails every task at that status. Fail-closed, but
  a trap. Remediation: derive it. Status: FIXED, canon c513a52.

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

---

## Tracker rules (the contract every agent follows)

1. **Read before acting.** Read *Current state* and *Architecture snapshot* before doing anything.
2. **Write on completion.** When you finish a task you MUST: set the task's status, OVERWRITE *Current
   state*, append any *Decisions* or *Findings*, append one line to `docs/log.md` (the chronology), and
   `git commit` the change. (Enforced by a Stop hook.) Narrative goes in `log.md`, not *Current state*.
3. **Append-only logs stay append-only.** Decisions and findings are superseded, never erased.
4. **One tracker per project, committed to git.** The git history is the versioning of this state.
