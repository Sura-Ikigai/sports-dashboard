# Implementation Tracker — Sports Dashboard

> Source of truth across sessions. Read this first every session.
> Last updated: 2026-08-17 by Claude (PLAN phase — grill-me → PLAN-v3-modeling, D-032…D-047)

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

**State now:** **Phase 1 has its answer (ship criterion MET), and the second cycle is planned.** On
the sealed 2026 fold accuracy **.6762** against a .62 bar, log loss **.6020** against a
constant-predictor **.6870** — both halves of D-008. But **D-031**: `point_diff_diff` carries
essentially all of it. T-006/T-007/T-008/T-009 are `BUILT` and unreviewed; 28 commits sit unmerged on
`feat/phase-1-analytical-core`.

**D-031's open question is now answered, and the answer changed the plan.** A grill-me session on
2026-08-17 ran two diagnostics against the pinned corpus rather than reasoning from the ablation.
Rest signal is **large** — home on a back-to-back against a rested opponent wins .4393, the reverse
.6438, a **20.5-point swing** — but linearity was never the problem; **mass** is (91% of games sit at
|rest_diff| ≤ 1 where the effect is nil) and so is **cliff placement** (0→1 day is +7.1 points, 2→3+
is +1.0). And the finding that reframed everything: **`point_diff_diff`, Elo and `form_diff`
correlate at .87–.92 — three rulers for one latent variable, team strength.** That is why the
ablation found nothing and why 17.9% of games read as coin flips. **Moving AUC needs a second factor,
not a better ruler.** Also corrected: the ablation deltas (+.0076, +.0023, +.0015) are all inside a
season's ±2.6-point noise band, so "removing them helps" was never supportable — "they do nothing
measurable" is (D-034).

**Sixteen decisions resolved, D-032…D-047**, and **`docs/plans/PLAN-v3-modeling.md`** written (DRAFT,
40 user stories, T-021…T-035). Two of them change standing project facts: **D-038** moves bulk
history into the application Postgres, **superseding the separate-stores invariant** in *Architecture
snapshot* below; **D-043** amends `PHASE-1-RESULT.md` §6's reproducibility claim, which stops being
true once a running database is required. Measured expectations are on the record: Elo ≈ +.008 AUC,
rest re-encoded ≈ +.005–.015, travel ≈ +.002, **availability unknown and the only real headroom**.

**Next action, in order:** **(1)** one **batched** review of T-006/T-007/T-008/T-009 — four rounds on
T-006 produced only test-hygiene findings after round 2, so batch rather than repeat that;
**(2)** merge to `main` (**32** commits on `feat/phase-1-analytical-core`, measured — the "28" this
line carried was stale) and advance the reviewed tasks to `DONE` — **this is a hard prerequisite, not
housekeeping: `REVIEWED` is currency-checked and `DONE` is exempt, so T-005 and T-006 must reach
`DONE` before T-022 touches `loader.py` and T-028 touches `features.py`, or the gate fails and you
owe a re-review of Phase 1 code**; **(3)** mark
PLAN-v3-modeling ACTIVE, copy it to `PLAN-current.md`, and start **T-021**. The model must freeze
before **2026-09-30** (D-042) — measured review throughput in this repo is 3–4 rounds per task
against ~15 new tasks plus 4 awaiting review, which is the constraint to plan against.

**Canon reconciliation is still in flight** — the factory's **PLAN-v2**
(`Client Projects/Dev-System/docs/plans/PLAN-v2.md`, DRAFT) owns T-013..T-020; only T-011/T-012
execute here. Its `canon-debt` threshold (3 findings at `revisit-when: reconcile-canon`) is **red at
6** — F-015, F-100, F-101, F-102, F-106, F-107. Housekeeping: the status index has FIXED rows sitting
in the open/accepted table.

**Active plan:** docs/plans/PLAN-current.md (= PLAN-v1, ACTIVE)
**Next plan:** docs/plans/PLAN-v3-modeling.md (DRAFT — becomes current when Phase 1's review closes;
numbered v3 because "PLAN-v2" already means the *factory's* plan in this tracker)
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
- **The historical corpus lives in the application Postgres** (D-038, 2026-08-17). This **supersedes**
  the previous invariant ("the app DB and the modeling store are separate concerns; bulk history never
  enters Postgres wholesale"), which held through Phase 1 and was overturned deliberately. Bulk
  history still **never enters the git repo**. Three constraints replace it, and they are what make
  the change safe: **schema is owned by migrations, data by `model.ingest`** (D-045); **no query
  anywhere carries an as-of predicate** — SQL narrows by season or team, `features.py` filters (D-039);
  and **integrity is verified at the ingest boundary**, not assumed of the table (D-046).
  Consequence, recorded rather than absorbed: reproducing the reported numbers now requires a running
  Postgres (D-043), and CI needs a service container for corpus-touching tests (D-044).
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
- [x] **T-002** Ingest sportsdataverse NBA seasons 2022–2026 into a local modeling store — `DONE` — owner: `backend-engineer`
      - acceptance: seasons 2022–2026 land in the modeling store; game counts per season match the
        source files; ingest is idempotent (re-running does not duplicate); the store is gitignored
        — **all met, by T-005** (see outcome)
      - security note: pin the asset URLs by release tag and verify what is downloaded before
        parsing — this is third-party data fetched over the network into a parsing path — **honored
        by T-005, and hardened further there** (content-hash pinning, redirect allowlist, size cap)
      - outcome: **closed as superseded by T-005 (D-027).** Every acceptance clause above is
        satisfied by `backend/model/loader.py`: the five seasons land in the gitignored
        `data/raw/nba_schedules/`, per-season counts are pinned and asserted on every load, and a
        valid cached file short-circuits re-download. The reviewer ✅s in the ledger row are T-005's
        — the same code, reviewed there — which is why this row carries T-005's SHA rather than one
        of its own. The one clause never built is the play-by-play parquet, deliberately: Phase 1
        uses schedules only, and PBP is not needed until a feature set beyond team-level pre-game
        stats exists. If that day comes it is a new task, not this one.
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
- [ ] **T-007** `splits` fold generator + tests — `BUILT` — owner: `backend-engineer`
      - acceptance: yields exactly the three expanding-window folds (22-23→24, 22-24→25, 22-25→26);
        tests assert every fold's training seasons precede its test season and no season appears on
        both sides of a fold; **calls `corpus.assert_curated` on its input** (F-067) — **all met**
      - security note: integrity only — a fold must never train on its own future.
      - outcome: `backend/model/splits.py`, standard-library only (D-021). `Fold` validates at
        construction, so a fold that trains on its own future cannot be built and then caught later;
        `WALK_FORWARD_FOLDS` is written as three literals rather than generated, because the
        evaluation protocol should be auditable rather than derived. `split_games` verifies four
        things instead of trusting them: input is curated (F-067), every season the fold names is
        present (a missing one would yield a quietly smaller fold reporting a healthy number),
        out-of-fold seasons are dropped, and — the one that matters — **train strictly precedes test
        in time**.
      - **the security note, made temporal rather than nominal:** "every training season < test
        season" is a claim about *labels*, and labels can lie. `split_games` asserts the dates
        directly. Those coincide today as a **measured** fact, not an assumption: seasons are disjoint
        with 120–133 day offseasons (2022 ends 2022-06-17, 2023 opens 2022-10-18). A game backfilled
        into the wrong season passes the label check and fails the date check — there is a test that
        does exactly that.
      - verified on the real corpus: 6,605 curated games → fold 1 train 2,643 / test 1,319; fold 2
        train 3,962 / test 1,321; fold 3 (sealed) train 5,283 / test 1,322. **3,962 evaluation games
        across the three folds**, against D-013's predicted ~3,900, and fold 1's 2,643 matches D-024
        to the game. Per-season test home-win rates .5474 / .5450 / .5552.
      - 15 tests (`backend/tests/test_splits.py`), stdlib-only so they run in CI.
- [ ] **T-008** `evaluate` metrics module + tests — `BUILT` — owner: `backend-engineer`
      - acceptance: accuracy, log loss, AUC, calibration curve, and comparison against a constant
        base-rate predictor; tests assert each against hand-computed values on a small labelled set,
        plus the comparator's boundary behavior — **all met**
      - security note: none.
      - outcome: `backend/model/evaluate.py`, standard-library only (D-021) — no numpy, no
        scikit-learn — so it runs in the gate and the eventual serving image inherits nothing.
        `evaluate()` bundles a fold's metrics and exposes `meets_ship_criterion`, which requires
        **both** halves of D-008 and never either alone.
      - hand-computed on one 5-game set (`y_true` T,F,T,T,F / `y_prob` .9,.2,.6,.4,.7): accuracy 0.6,
        log loss 0.5919186453876236, AUC 2/3, and the exact calibration bins. The comparator is
        checked at `constant=0.5`, where a constant predictor's log loss is `ln 2` for any labels —
        so the assertion does not trust the implementation twice.
      - **three deliberate departures from a library implementation**, each because a sentinel would
        be indistinguishable from the conclusion T-009 is trying to draw: `roc_auc` **raises** on a
        single-class set rather than returning the conventional 0.5, which would read as "no signal";
        `log_loss` clips at 1e-15, so one confidently-wrong call cannot swamp a fold with infinite
        loss; and AUC uses Mann-Whitney ranks with half-credit ties, because a shrunk feature set
        emits repeated probabilities and a threshold sweep would score them by sort order.
      - **the comparator's boundary, which is the point of the task:** a model that merely reproduces
        the constant does **not** beat it — a tie is not a win, and that is exactly the null D-008
        tests against. Also asserted: the best possible constant *is* the observed base rate, so the
        comparison is against the strongest constant predictor rather than a straw man.
      - uses **D-026's 0.55534**, not D-007's 0.55556 — the latter predates F-042's exhibition removal,
        and T-010 requires the constant a report quotes to be the one its own corpus produces.
      - 27 tests, stdlib-only so they run in CI. Includes an independent cross-check: AUC agrees
        exactly with brute-force pair counting over 400 tie-heavy random sets (max diff 0.00e+00),
        which is evidence rather than a tautology in a way single fixtures cannot be. Suite 126 → 153
        (139 + 1 skipped in CI's environment).
- [ ] **T-009** `estimator` + walk-forward evaluation run — `BUILT` — owner: `backend-engineer`
      - acceptance: **calls `corpus.assert_curated` on its input** (F-067); logistic regression fit
        per fold; versioned artifact emitted; three folds run end to end; per-fold and headline
        accuracy/log loss/AUC reported with the fold-to-fold spread; states plainly whether **both**
        criteria are met; training-only deps confined to the training requirements file, served image
        unchanged — **all met**
      - security note: **the serialized artifact is an arbitrary-code-execution vector** … — **made
        inapplicable rather than mitigated, see D-030**: the artifact is JSON, so there is no
        deserialization step that can execute anything, and the fit is stdlib (no scikit-learn, no
        numpy). The served image gains nothing at all for Phase 2 inference.
      - **RESULT — the ship criterion is MET on the sealed 2026 fold.**

        | fold | train | test | accuracy | log loss | AUC | beats constant |
        |---|---|---|---|---|---|---|
        | 22-23 → 24 | 2,643 | 1,319 | .6444 | .6242 | .7083 | yes (+.0646) |
        | 22-24 → 25 | 3,962 | 1,321 | .6503 | .6129 | .7138 | yes (+.0764) |
        | **22-25 → 26 (sealed)** | 5,283 | 1,322 | **.6762** | **.6020** | **.7323** | **yes (+.0850)** |

        D-008 half 1 — accuracy ≥ .62: **MET** (.6762). Half 2 — log loss beats the constant .55534
        predictor: **MET** (.6020 vs .6870). **All three folds clear .62**; spread .0318, stdev .0169
        across 3,962 evaluation games, which is what D-013 bought by using three folds instead of one.
      - **calibration is good**, which matters because it is half the criterion: predicted vs observed
        by decile — .168/.171, .256/.202, .355/.310, .451/.469, .549/.575, .647/.638, .747/.792,
        .843/.830. A 70% call really is right about 75% of the time.
      - **two controls run because the headline is the number the project exists to produce.**
        *Label shuffle*: refitting on shuffled training labels collapses to accuracy .5552 — exactly
        the test-season base rate — and AUC .5494. No leakage. *Ablation*: see **D-031** — removing
        `point_diff_diff` costs 3.7 accuracy points; removing any of the other three slightly
        **improves** the model. The model ships on essentially one feature, and T-010 must say so.
      - artifacts: `models/logistic-{2024,2025,2026}.json`, versioned by **content hash** (D-012), in
        the gitignored `/models/` — confirmed with `git check-ignore`. 15 estimator tests; suite 153 →
        168. Gate 8/8.
- [ ] **T-010** Written analysis of the result — `BUILT` (draft; owner reviews) — owner: `human`
      - acceptance: records which features carried signal (coefficients + direction), where the model
        failed, whether probabilities are calibrated, how folds differed; states the ship/no-ship
        verdict against the paired criterion; every number reproducible from committed code + the
        pinned data release — **all met**
      - security note: publish nothing that cannot be reproduced from committed code — an
        unreproducible number in a portfolio artifact is a claim that cannot be audited.
      - outcome: **`docs/analysis/PHASE-1-RESULT.md`** — drafted by Claude for the human to own and
        edit. Verdict: **SHIP**, both halves of D-008 met on the sealed fold (.6762 accuracy, .6020
        log loss vs the constant predictor's .6870).
      - **the security note was enforced mechanically, not asserted.** Every numeric claim in the
        document was re-derived from a fresh pipeline run and string-matched against the text: **30
        of 31 verified**; the one miss was the checker looking for `.1210` where the prose says
        "+12.1 accuracy points" — the same number. That pass caught a real defect: the early-season
        uplift had been stated as 9.6 points, which compares that period's accuracy against the
        *season's* base rate rather than that period's own (.5707). Corrected to **8.1 points**, with
        the wrong baseline named so the error is not silently repaired.
      - also **`docs/analysis/PHASE-1-RESULT.html`** — a reading version of the same document, sat
        beside the markdown. Charts follow the dataviz method: form chosen before color, the palette
        validated with the skill's own script against this page's surfaces (all six checks PASS in
        both modes), diverging blue↔red for the signed ablation, a zero baseline on every bar, and a
        hover layer with keyboard-reachable marks. Audited before shipping: no color token is defined
        only inside a dark block (the unreadable-artifact bug), and **every one of 116 figures on the
        page traces back to the markdown** — the same provenance standard the document itself is held
        to, applied to its presentation.
      - the analysis reports the uncomfortable result rather than the flattering one: **D-031** — the
        model ships on `point_diff_diff` alone, and removing any other feature slightly improves it —
        is §3 of the document, not a footnote. It also refuses three things the numbers do not
        license: calling this a four-feature model, quoting a home-court coefficient (D-024), and
        assuming a backtest holds live (D-017's 2026-27 season is the real trial).

### Cycle 2 — second-factor features, Postgres corpus, prediction surface (PLAN-v3-modeling)

<!-- Numbered from T-021: T-013..T-020 are reserved for the factory's PLAN-v2. All fifteen are
     PLANNED, none started. Full acceptance criteria, security notes and UI/UX intent live in
     docs/plans/PLAN-v3-modeling.md — the rows below are the index, deliberately compact so this
     tracker stays cheap to read on every task (CLAUDE.md). Do not duplicate the plan here. -->

- [ ] **T-021** Corpus schema migrations + role grants — `PLANNED` — owner: `backend-engineer`
      - empty corpus + predictions tables; API role `SELECT`-only, ingest role writes; up/down both
        clean; **no migration inserts corpus rows** (D-045, D-047)
- [ ] **T-022** Extend loader: warm-up seasons 2016–2019 + player/team box parquet — `PLANNED` — owner: `backend-engineer`
      - new pinned counts + content hashes; 30 franchises per season; **verify against an empty data
        dir** (F-037); parquet is new in this parsing path (D-037)
- [ ] **T-023** `ingest` — verified loader→Postgres — `PLANNED` — owner: `backend-engineer`
      - idempotent on game id; hashes and counts verified **before** any write; refuses rather than
        repairs (D-046)
- [ ] **T-024** `store` — Postgres read layer — `PLANNED` — owner: `backend-engineer`
      - narrows by season/team; **contains no as-of predicate**, enforced mechanically; re-asserts
        curation on read (D-039)
- [ ] **T-025** `elo` deep module — `PLANNED` — owner: `backend-engineer`
      - MOV, K=20, home 100, carryover .75; stdlib; golden fixtures + 5 invariants (D-032)
- [ ] **T-026** `venues` deep module — `PLANNED` — owner: `backend-engineer`
      - static city table (coords + elevation); travel/altitude/tz; a missing venue **raises** (D-036)
- [ ] **T-027** `availability` deep module — `PLANNED` — owner: `backend-engineer`
      - lagged rotation participation; garbage-time zeroes are not absence; takes data through the
        Context, never queries directly (D-035)
- [ ] **T-028** `features` v2 — Context + new feature set — `PLANNED` — owner: `backend-engineer`
      - Context (games + players + venues), signature stays 3 args; emits `elo_diff`, `home_b2b`,
        `away_b2b`, `rest_edge`, `avail_diff`, `travel_diff`, `altitude`; `point_diff_diff`,
        `form_diff`, `home_advantage` removed; **leakage property test extended to future player
        rows**, non-vacuity control kept (D-033, D-034, D-039)
- [ ] **T-029** `splits` — warm-up isolation — `PLANNED` — owner: `backend-engineer`
      - a warm-up season reaching a training row must fail a test (D-037)
- [ ] **T-030** Refit, the **single** 2026 evaluation, freeze — `PLANNED` — owner: `backend-engineer`
      - selection on 2024/2025 only; set frozen and committed **before** 2026 is touched; exactly one
        run; JSON artifact (D-030, D-042)
- [ ] **T-031** `prediction` service + persistence + scheduled job — `PLANNED` — owner: `backend-engineer`
      - one interface (probability + vector + **per-feature logit contributions** + version) used by
        both job and API; append-only; 7-day horizon (D-011, D-012)
- [ ] **T-032** Predictions API — `PLANNED` — owner: `backend-engineer`
      - prediction + decomposition per game; upcoming; accuracy by confidence band; last pre-tip
        prediction is the one scored (D-047)
- [ ] **T-033** TypeScript scorer + gate-pinned contract check — `PLANNED` — owner: `frontend-engineer`
      - both implementations agree on probability **and** contributions; **runs in the gate**, not
        locally (D-041, F-091)
- [ ] **T-034** Game detail surface — `PLANNED` — owner: `frontend-engineer`
      - waterfall + confidence band + version/as-of; **fenced** what-if (distinct, unpersisted,
        uncounted); responsive; keyboard + screen-reader path; zero baseline; sign not carried by
        color alone (D-040)
- [ ] **T-035** Amend §6 + write the cycle-2 analysis — `PLANNED` — owner: `human`
      - §6's reproducibility claim amended for the Postgres dependency; analysis held to T-010's
        standard (every figure re-derived and string-matched); **a null result on availability is a
        reportable outcome** (D-043)

### Canon reconciliation (PLAN-v2 — factory-owned, executed here)

<!-- These two carry PLAN-v2's ids, not this project's sequence, because they are scheduled by the
     factory plan (Client Projects/Dev-System/docs/plans/PLAN-v2.md) and merely EXECUTED here — the
     files they correct live in this repo. T-013..T-020 are factory work and do not appear here. -->

- [ ] **T-011** Correct the false provenance in `.claude/CANON-VERSION` — `BUILT` — owner: `human`
      - acceptance: the `fe27280` entry no longer claims promotion via `26b360f`; F-103 recorded as
        STILL PROJECT-LOCAL; the stale `Sports/Dev-System/` clone deleted or marked
        non-authoritative — **all met**
      - security note: none — but this file is the baseline every later reconcile reads as ground
        truth, so a claim in it must be verified against the factory, never asserted from intent.
      - outcome: the false entry is removed and replaced with a dated correction naming the
        evidence (`git log -- checks/gate-completeness.mjs` returns only `bd58a22`; canon's copy is
        49 lines without `evaluateAgentIntegrity`, this project's 59 lines with it). F-103 moved to
        STILL PROJECT-LOCAL with T-014 named as its promotion path. Stale clone marked via
        `Sports/Dev-System/NOT-THE-FACTORY.md` rather than deleted — **verified safe to delete**
        (it holds nothing the factory clone lacks; both carry `canon/self-learning-batch-v1` at
        `ebb1dee`, tree-identical to the squashed `bd58a22`), but marking is the reversible choice.
      - **found while verifying:** `canon/self-learning-batch-v1` — the Sura Media reconciliation's
        9-commit development history — exists in two local clones and **not on `origin`**, which
        carries only `main`. No content is at risk (`bd58a22` has the same tree) but the history is
        unpushed. Filed as F-105.
- [ ] **T-012** Sync the reviewer agents to canon `26b360f` — `BUILT` — owner: `human`
      - acceptance: `.claude/agents/{security-auditor,logic-reviewer}.md` are byte-identical to
        canon at `26b360f`, closing the gap between `last-reconciled:` and the actual tree — **met**
      - security note: this changes what the review gate's two mandatory agents are instructed to
        do. Verify the sync target is the commit the lockfile names, not merely "latest canon".
      - outcome: both files replaced from `git show 26b360f:agents/<name>.md` and diff-verified
        identical. This closes divergence #1: the project promoted the reviewer-contract fix to
        canon on 2026-08-12, stamped `last-reconciled: 26b360f`, and never pulled it back — so for
        two days the agents the runtime loaded still said "write your Review ledger row and append
        every issue to Findings" while canon, and Tracker rule 5b, said the opposite.
      - **the stale text was unsatisfiable, not merely wrong.** All three reviewers are scoped
        `tools: Read, Grep, Glob`, so the old instruction to write and commit the tracker could
        never execute. Correct behaviour was held in place only by the main thread overriding canon
        in every spawn prompt — the undocumented divergence F-101 names.
      - **known defect accepted, deliberately:** the `26b360f` files now synced here instruct a
        `Read, Grep, Glob` agent to run `git worktree add`, `git archive`, `pytest` and set
        `PYTHONPYCACHEPREFIX`. None of it is executable. Syncing propagates that knowingly, because
        the alternative is a lockfile that lies about the tree. Filed as F-106; PLAN-v2 T-014 adds
        the coherence check that catches it, T-019 gives reviewers the isolation the prose assumes.
        Do **not** patch it locally — a third divergent copy is what T-012 exists to end.

## Decisions log

**Moved to [`docs/decisions.md`](decisions.md)** (append-only). It was 236 lines and growing, and
every agent was told to read it to act on a task that needed two entries from it.

`grep -n 'D-0NN' docs/decisions.md` for the ones a task actually cites — task entries name them.

## Review ledger

| Task  | security-auditor | logic-reviewer | ui-ux-reviewer | notes |
|-------|------------------|----------------|----------------|-------|
| T-002 | ✅ 34759ed       | ✅ 34759ed     | n/a            | Closed as superseded by T-005 (D-027) — the ✅s are T-005's, on the same code. Not a review of its own; the row exists so a DONE task carries a verdict, per canon. n/a: no user-facing surface |
| T-006 | pending          | pending        | n/a            | r2 ⛔⛔ @d8257b6: F-044..F-060 all verified CLOSED by both; both ⛔ on `corpus.py` (added in the remediation, never reviewed) — F-065 curation wiped a whole corpus silently, F-061 the safety default was untested, F-071 stale bytecode made local runs untrustworthy. Remediated. · r1 ⛔⛔ @34759ed: security F-044 (target's own result reachable via `history`) · logic F-051 HIGH (7 mutations survived; 3 change 5,000+ real vectors). Remediated — awaiting re-review. n/a: offline module, no user-facing surface |
| T-005 | ✅ 34759ed       | ✅ 34759ed     | n/a            | 4 rounds: ✅/⛔ @32110ce (F-026/F-027 HIGH) · ⛔⛔ @e2d2558 (F-037 broke real downloads) · ✅✅ @8eae86c · re-review ✅✅ @34759ed after `406dd09` touched loader.py (F-039 docstring; both reviewers proved it AST-identical, security re-ran the download from an empty dir). n/a: no user-facing surface |
| T-001 | ✅ 763101e       | ✅ 763101e     | n/a            | 4 rounds: r1 ⛔⛔@d8e3515 · r2 ✅✅@d0e661d · r3 logic ✅/security ⛔@fef3dc8 (F-019) · r4 ✅✅@763101e. n/a: no user-facing surface changed |

## Findings — status index

<!-- DERIVED VIEW, not history. The evidence behind each finding lives in docs/findings.md, which is
     append-only and stays as written; this index is the one place that may be rewritten, and it is
     what you read to answer "is F-0NN still open?" without opening that file at all.

     It exists because the ledger failed that question three times in one session (2026-08-10):
     F-024 was fixed in `32110ce` and still read OPEN; F-032 and F-035 were fixed and carried no
     `Status:` line at all. A finding closed in a commit message is not closed until the ledger says
     so — and until this table said so, "closed" was invisible to anyone who had not read the whole
     document. Adding a finding, or changing one's status, means updating this row too. -->

**Open / accepted — the live set (15):**

<!-- F-072..F-099 reserved for the in-flight round-3 reviewers; see the note above F-100. -->

| ID | Sev | Area | Status | Fires when |
|----|-----|------|--------|-----------|
| **F-001** | HIGH | security | ACCEPTED — no authorization boundary exists anywhere in the app | `first-user-scoped-data` |
| **F-057** | MEDIUM | data/design | RESOLVED BY REMOVAL — `home_advantage` is cut from the feature set entirely (D-033). Its `revisit-when: T-009` fired, D-031 confirmed the instability empirically (+0.112/+0.003/+0.037 across folds), and the feature is designed out rather than mitigated. Home-court advantage stays in the intercept | — |
| **F-015** | LOW | ops | ACCEPTED — `ui-ux-reviewer` declared but not mechanically enforced | `reconcile-canon` |
| **F-006** | LOW | ops | OPEN — `docker-compose.prod.yaml` is a 0-byte file | — |
| **F-007** | LOW | ops | OPEN — `ci.yml` duplicates every gate check | — |
| **F-092** | HIGH | logic/tests | FIXED — curation policy moved to stdlib `corpus.apply_default_curation`; behavioural test runs in the gate | — |
| **F-093** | HIGH | logic/tests | FIXED — `ast` check pins arg forwarding by name+order, runs in CI | — |
| **F-094** | MEDIUM | logic/tests | FIXED — `assert_called_once_with` on a two-season tuple | — |
| **F-095** | LOW | integrity | FIXED — `teams_per_season` honouring pinned | — |
| **F-096** | LOW | docs/evidence | FIXED — mis-stated gate evidence corrected in place; brace bug fixed | — |
| **F-090** | HIGH | logic/tests | FIXED — fixture asserts `call_args`; `seasons`/`data_dir` must reach the loader | — |
| **F-091** | HIGH | logic/tests | FIXED — stdlib `ast` check runs in CI; proven to fail there with the default flipped | — |
| **F-087** | MEDIUM | logic/tests | FIXED — `min_games` exercised end to end | — |
| **F-088** | MEDIUM | logic/tests | ACCEPTED — `expected` arg unpinned; every caller passes the constant or None | `first-hand-pinned-season` |
| **F-089** | MEDIUM | logic/tests | ACCEPTED — 30-team assertion never driven from above | `first-expansion-or-source-change` |
| **F-077** | MEDIUM | integrity | FIXED — `assert_curated` now checks both invariants | — |
| **F-078** | MEDIUM | integrity | FIXED — refuses a non-Sequence and an empty collection | — |
| **F-079** | LOW | usability | ACCEPTED — false negative on curated partial seasons | `first-partial-season-run` |
| **F-080** | LOW | integrity | ACCEPTED — unreachable in training; dup-id check pre-empts it | `phase-2-inference` |
| **F-081** | LOW | integrity | ACCEPTED — needs a deleted franchise AND an impostor | `first-expansion-or-source-change` |
| **F-072** | MEDIUM | process | FIXED by Tracker rule 5d — parallel reviewers in one tree corrupt each other | — |
| **F-069** | LOW | data | DOCUMENTED — curation cannot see an exhibition between two franchise ids | `new-historical-source` |
| **F-101** | MEDIUM | process/canon | OPEN — canon's reviewer contract breaks under parallel review | `reconcile-canon` |
| **F-100** | MEDIUM | process/canon | MITIGATED by Tracker rule 5a — no finding-number allocator in canon | `reconcile-canon` |
| **F-102** | MEDIUM | process/canon | MITIGATED by Tracker rule 5c — canon has no notion of a remediation adding code | `reconcile-canon` |
| **F-104** | LOW | process | OPEN — docs-only review-commit convention enforced by nothing | — |
| **F-105** | LOW | ops | OPEN — `canon/self-learning-batch-v1` (9 commits) exists in two local clones, never pushed to `origin` | — |
| **F-106** | MEDIUM | process/canon | OPEN — canon `26b360f` instructs `Read, Grep, Glob` reviewers to run `git worktree`/`pytest`; inert | `reconcile-canon` |
| **F-107** | MEDIUM | process/canon | OPEN — the factory runs none of its own hooks (no `.claude/`), so work dies in its working tree | `reconcile-canon` |

**Closed (66):** F-042 (FIXED — `corpus.py`, D-025) · F-103 (FIXED — agent-content integrity) · F-061 · F-062 · F-063 · F-064 · F-065 · F-066 · F-067 · F-068 · F-070 · F-071 (T-006 re-review round 2, all FIXED; F-070(b) ACCEPTED) · F-044 · F-045 · F-046 · F-047 · F-048 · F-049 · F-050 (T-006 security review, all FIXED in remediation) · F-051 · F-052 · F-053 · F-054 · F-055 · F-056 · F-058 · F-059 · F-060 (T-006 logic review, all FIXED; every surviving mutation re-run and now caught) · F-002 · F-003 · F-004 · F-005 (all CLOSED as moot or designed out by D-004/D-005) ·
F-008 · F-009 · F-010 · F-011 · F-012 · F-013 (FIXED @ `d0e661d`, T-001 round 1) · F-014 · F-016 ·
F-017 (CLOSED, T-001 round 3) · F-018 (FIXED, canon `be5f6dc`) · F-019 (FIXED, canon `6f49f29`) ·
F-020 · F-021 · F-022 · F-023 (FIXED @ `763101e`; F-021 project-local only — canon still ships the
unpinned range) · F-024 (CLOSED @ `32110ce`, recorded 2026-08-10) · F-025 (FIXED, canon) ·
F-026 · F-027 · F-028 · F-029 · F-030 · F-031 · F-032 · F-033 · F-034 (FIXED across `e2d2558`/
`8eae86c`, T-005 remediation) · F-035 · F-036 (FIXED, canon `c513a52`) · F-037 · F-038 (FIXED @
`8eae86c`) · F-039 (CLOSED @ `406dd09`) · F-040 · F-041 (FIXED @ `55dc586`) · F-043 (FIXED @
`34759ed`; **canon promotion still pending** — see *Future hardening*).

## Findings (from reviews)

**Moved to [`docs/findings.md`](findings.md)** (append-only, 785 lines and growing). The *status
index* above is the summary you read to answer "is F-0NN still open?"; the evidence, reproductions
and remediation notes live in the file.

`grep -n 'F-0NN' docs/findings.md` for a specific finding.

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
- ~~Promote the F-043 fix to canon, then re-stamp `.claude/CANON-VERSION`~~ — **done 2026-08-10**,
  Dev-System `1c52645`, pushed to `origin/main`. Verified in the factory before committing: canon's
  own suite passes on its **`vitest: ^2.1.0`** range (2.1.9, 53 tests), not merely on this project's
  4.1.10 pin, and both CLI modes were smoke-tested against the canon repo's own tracker.
  `checks/package.json` was deliberately **not** promoted — its only diffs are F-021's local vitest
  pin and a `§`-encoding regression in this project's copy, neither of which belongs in this change.
- **Promote D-028 (scoped re-reviews) and D-029 (LOW batching) to canon** — deliberately held back on
  2026-08-12 as policy rather than defect. They change how much scrutiny work receives, and were
  derived from one pure-offline task under a usage limit; promote once a task with a schema migration
  and an auth surface has exercised them. T-009 is the next task of genuinely different shape.
- **Promote F-021's vitest pin + lockfile to canon.** Still open, and now the only known canon gap.
  Canon ships `checks/package.json` with an unpinned `vitest: ^2.1.0` and **no lockfile**, so every
  project stamped from it inherits the advisory-bearing 2.x range (5 npm advisories, 1 critical, 1
  high — not reachable as configured, since every one needs a listening dev/UI server and
  `vitest run` starts none). Fixed project-locally in the F-018 cycle and flagged then as belonging
  to a canon promotion; deliberately left out of `1c52645` to keep that change to one concern.
  Also fix the `§` encoding while there. Recorded in `.claude/CANON-VERSION` under
  "STILL PROJECT-LOCAL".
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
   state*, append any decisions to `docs/decisions.md` and findings to `docs/findings.md` (updating the
   *status index* here for each), append one line to `docs/log.md` (the chronology), and `git commit`
   the change. (Enforced by a Stop hook.) Narrative goes in `log.md`, not *Current state*.
3. **Append-only logs stay append-only.** Decisions (`docs/decisions.md`) and findings
   (`docs/findings.md`) are superseded, never erased. The one exception is the *Findings — status
   index* above, which is a derived view and is rewritten in place: every new finding gets a row, and
   every status change updates one. A finding closed in a commit message is not closed until the
   index says so (F-024, F-032, F-035 each proved this).
   **Read on demand, not by default.** This tracker holds what you need to ACT and is meant to stay
   bounded; the two archives hold the record and are meant to grow. Grep them for the ids a task
   actually cites — do not read them end to end. That habit is what let the tracker reach 1,367 lines
   with 74% history while every agent was still told to read all of it.
4. **One tracker per project, committed to git.** The git history is the versioning of this state.
5. **Review-round protocol (added 2026-08-10 after F-100..F-102 caused real damage).** Reviewers are
   run in parallel — that is the efficient shape and it is not going to change — so the round must be
   set up for it *before* any reviewer is spawned:
   a. **Allocate a disjoint finding-number block to each reviewer, in writing, in the spawn prompt.**
      "Start at F-0NN" is not an allocation: two reviewers both given it will both use it, which is
      exactly what produced two conflicting F-061..F-066 sets. Allocate e.g. security-auditor
      F-072–F-081, logic-reviewer F-082–F-091, and record the allocation here in the round's heading
      before spawning. The main thread keeps its own block, well clear of both.
   b. **Reviewers report; the main thread transcribes.** Canon's agent files tell reviewers to write
      the tracker themselves, which is safe only when they run one at a time. Two concurrent writers
      to one file will interleave or clobber. The cost of transcription is that fidelity depends on
      the main thread — so transcribe verdicts and findings *before* starting remediation, while the
      report is still in front of you, and never paraphrase a reproduction step.
   f. **LOW findings batch; one class of them does not** (D-029). Report every observation, but
      collect the genuinely cosmetic ones into a single test-hygiene backlog entry per round rather
      than tracking each individually. **Exception:** "this test does not pin what it claims to pin"
      is promoted to MEDIUM and tracked on its own — that class has twice been a real safety gap
      wearing a LOW label (F-059 → F-061; F-055).
   e. **A re-review is scoped by default** (D-028): the diff since the last verdict for this task's
      files, plus the additions declared under 5c. A **full** re-review of the whole task happens when
      the **reviewer** calls for it — not the builder. The builder describes what it did; the reviewer
      decides how much scrutiny that earns. The party with the incentive to finish does not get to
      set its own scrutiny level.
   d. **Anything that mutates source runs in an isolated copy of the tree** — `git worktree add` or
      `git archive` to a temp dir — never the shared working tree, and snapshots
      `git status --porcelain` around every test run. Two round-3 slices running concurrently
      corrupted each other within minutes of this parallel design being introduced: one was patching
      `features.py` in place while the other ran the suite, and the second nearly filed **three
      phantom HIGH findings** off runs that landed inside a patch window (F-072). A red suite in a
      shared tree is not evidence until the tree is confirmed clean.
   c. **A remediation declares what it ADDED, not just what it changed.** Twice now the code that
      failed the next round was code that arrived with the previous remediation and had never been
      reviewed by anyone (`corpus.py` in round 1's fix; `assert_curated` and the opponent-matched
      exclusion in round 2's). List additions explicitly in the round's hand-off so the next review
      scopes them as new code rather than discovering them.

