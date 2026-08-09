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

**State now:** Stage 3 shipped — the dashboard syncs live NBA teams/games from ESPN into Postgres and
renders them. The Dev-System is instantiated on top of it (tracker, agents, gate, hooks) and the gate
runs green on all 7 checks; T-001 is `BUILT` and awaiting the review gate. The historical source is
decided (D-004: sportsdataverse, ESPN-keyed) and the Kaggle corpus is deleted (D-005). The next body
of work is the **game-modeling layer**, not yet planned.

**Next action:** Run the review gate on T-001 (`security-auditor`, then `logic-reviewer`), then the
PLAN phase (grill-me → to-PRD → `docs/plans/PLAN-v1.md`) for the modeling layer.

**Active plan:** none yet — `docs/plans/PLAN-current.md` is created by the first planning session.
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
- [ ] **T-001** Instantiate the Dev-System into this repo — `BUILT` — owner: `human`
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
- [ ] **T-003** PLAN the modeling layer (grill-me → to-PRD → `PLAN-v1.md`) — `BACKLOG` — owner: `human`
      - acceptance: `docs/plans/PLAN-v1.md` exists, is copied to `PLAN-current.md`, and decomposes
        into tracker tasks each carrying acceptance criteria + a security note
      - blocked on: T-002

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

## Review ledger

| Task  | security-auditor | logic-reviewer | ui-ux-reviewer | notes |
|-------|------------------|----------------|----------------|-------|
| T-001 | pending          | pending        | n/a            | instantiation is config + docs; no user-facing surface changed |

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

## Future hardening (review output → next-cycle backlog)

- Install Playwright and declare the `a11y` check; tune `checks/reference/perf-budgets.json` for this
  app and declare `perf`. Both are canon gates this project currently runs without.
- `GET /nba/games` returns a bare `LIMIT 20` with no pagination or date filter — fine for a scoreboard,
  insufficient once historical games are queryable.
- The APScheduler sync runs in-process in the API container; a second replica would double-sync. Move
  to a single scheduled worker before scaling out.
- Wire the gate's own tests (F-014): generate `checks/package-lock.json`, pin vitest to the frontend's
  `4.1.10`, and add `- { id: meta-unit, run: "npm --prefix checks ci && npm --prefix checks test" }`.
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
