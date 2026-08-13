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

**State now:** **T-006 is `BUILT`, remediated through two full review rounds, awaiting round 3.**
Round 2 (`d8257b6`) was ⛔⛔ again, and correctly: both reviewers independently verified every round-1
finding (F-044..F-060) was genuinely closed, then both ⛔'d on `backend/model/corpus.py` — code that
arrived *with* the round-1 remediation and that no reviewer had ever seen. That found a real bug
(**F-065**: curation silently returned **0 games from a 5,439-game input** on a partial season, using
its own documented escape hatch), an untested safety default (**F-061**), a fail-open control the
F-044 fix had introduced (**F-068**), and a stale-bytecode hazard that made local test results
untrustworthy and threatened this task's own evidence (**F-071**). All 11 addressed; 19 mutations
re-run under a hardened harness, all caught. Suite 90 → 103. **T-005 is `REVIEWED` at `34759ed`
(✅✅)**. F-042 closed (D-025); the F-043 fix is canon at Dev-System `1c52645`, pushed.

**Round 3 is INCOMPLETE.** Slices A (closure verification: 8/8 CLOSED) and C (mutation regression:
17/17 CAUGHT) are done and clean. Slices B and D — a fresh review of what the round-2 remediation
*added* — have not run, and per F-102 that is precisely the surface that broke rounds 2 and 3.

**Next action:** run round-3 slices B and D (security F-077–F-081, logic F-087–F-091), scoped to the
round-2 additions only: `assert_curated`, the opponent-matched exclusion + `_TeamGame.opponent_id`,
the sentinel default for `expected`, and per-`(season, team_id)` identification. Then re-review T-006 (`Use the security-auditor subagent on T-006`, then `logic-reviewer`)
against the remediation, then **T-007** (`Use the backend-engineer subagent on T-007`). F-042 is
closed ahead of T-007 as planned, so the fold generator can build on a clean 6,605-game corpus.
T-007 must read **D-026** (the constant-predictor baseline is 55.534%, not 55.556%) and **D-024/F-057**
(`home_advantage` is not identifiable in fold 1 — 1 neutral game in 2,643 rows).

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
- [ ] **T-007** `splits` fold generator + tests — `PLANNED` — owner: `backend-engineer`
      - acceptance: yields exactly the three expanding-window folds (22-23→24, 22-24→25, 22-25→26);
        tests assert every fold's training seasons precede its test season and no season appears on
        both sides of a fold; **calls `corpus.assert_curated` on its input** (F-067 — a default on
        the producer is not a guarantee at the consumer, and three documented paths reach here
        uncurated with no symptom)
      - read first: **D-026** (the constant-predictor baseline is 55.534%, not 55.556%) and
        **D-024/F-057** (`home_advantage` is 1.0 in 2,642 of fold 1's 2,643 rows, so its coefficient
        is not identifiable in the early folds)
      - security note: integrity only — a fold must never train on its own future.
- [ ] **T-008** `evaluate` metrics module + tests — `PLANNED` — owner: `backend-engineer`
      - acceptance: accuracy, log loss, AUC, calibration curve, and comparison against a constant
        base-rate predictor; tests assert each against hand-computed values on a small labelled set,
        plus the comparator's boundary behavior
      - security note: none.
- [ ] **T-009** `estimator` + walk-forward evaluation run — `PLANNED` — owner: `backend-engineer`
      - acceptance: **calls `corpus.assert_curated` on its input** (F-067); logistic regression fit
        per fold; versioned artifact emitted; three folds run end to end; per-fold and headline accuracy/log loss/AUC reported with the fold-to-fold spread;
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

**Open / accepted — the live set (10):**

<!-- F-072..F-099 reserved for the in-flight round-3 reviewers; see the note above F-100. -->

| ID | Sev | Area | Status | Fires when |
|----|-----|------|--------|-----------|
| **F-001** | HIGH | security | ACCEPTED — no authorization boundary exists anywhere in the app | `first-user-scoped-data` |
| **F-057** | MEDIUM | data/design | OPEN — `home_advantage` collinear with the intercept in early folds (see D-024) | `T-009` |
| **F-015** | LOW | ops | ACCEPTED — `ui-ux-reviewer` declared but not mechanically enforced | `reconcile-canon` |
| **F-006** | LOW | ops | OPEN — `docker-compose.prod.yaml` is a 0-byte file | — |
| **F-007** | LOW | ops | OPEN — `ci.yml` duplicates every gate check | — |
| **F-077** | MEDIUM | integrity | OPEN — `assert_curated` certifies shapes `exclude_exhibitions` refuses | T-006 r3 |
| **F-078** | MEDIUM | integrity | OPEN — `assert_curated` consumes a generator and passes; F-045 reintroduced | T-006 r3 |
| **F-079** | LOW | usability | OPEN — false negative on curated partial seasons; `min_games` has no floor | T-006 r3 |
| **F-080** | LOW | integrity | OPEN — same-pair id collision still silently drops a real game | T-006 r3 |
| **F-081** | LOW | integrity | OPEN — team check counts thirty ids, not which thirty | T-006 r3 |
| **F-072** | MEDIUM | process | FIXED by Tracker rule 5d — parallel reviewers in one tree corrupt each other | — |
| **F-069** | LOW | data | DOCUMENTED — curation cannot see an exhibition between two franchise ids | `new-historical-source` |
| **F-101** | MEDIUM | process/canon | OPEN — canon's reviewer contract breaks under parallel review | `reconcile-canon` |
| **F-100** | MEDIUM | process/canon | MITIGATED by Tracker rule 5a — no finding-number allocator in canon | `reconcile-canon` |
| **F-102** | MEDIUM | process/canon | MITIGATED by Tracker rule 5c — canon has no notion of a remediation adding code | `reconcile-canon` |
| **F-104** | LOW | process | OPEN — docs-only review-commit convention enforced by nothing | — |

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

