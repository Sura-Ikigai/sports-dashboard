# Sports Dashboard — CLAUDE.md

## Workflow

`grill-me` → `to-prd` → build (main session) → `/review` → debug (`/caveman`) → lessons.
Full description: `../../Client Projects/Dev-System/WORKFLOW.md`. Skills and the two review agents
are global, in `~/.claude/`.

- **`docs/plans/<feature>.md`** — one file per feature: intent, decisions, phases, findings,
  remediations. Read the active one before acting.
  - `modeling-second-cycle.md` — **ACTIVE** (T-021…T-035). This is the one to read before acting.
  - `phase-1-analytical-core.md` — **MERGED** 2026-08-25 (PR #3). History, plus 12 carried findings
    that are still open — each names the cycle-2 task that absorbs it.
- **`docs/archive/`** — history only, do not read by default. `decisions.md` (D-001…D-050),
  `findings.md` (F-001…F-142, full evidence), `log.md`, the old tracker, superseded plans.
  **Grep it, never read it end to end:** `grep -n 'D-0NN' docs/archive/decisions.md`.
- `docs/superpowers/` — Stage 3 was built with a different workflow; historical record only.

Review findings are **advisory** and land in the plan file as a checklist. What blocks is
deterministic: `ci.yml`'s two jobs, which are the required status checks on `main`.

## Stack
Next.js 16 App Router (React 19) · FastAPI · Postgres 16 (Docker Compose) · Alembic
Invariants and domain notes are below — there is no separate stack overlay file.

## Invariants (never violate)
- FastAPI is the only DB writer. The frontend calls FastAPI, never Postgres directly.
- FastAPI owns all outbound third-party API calls. The browser never talks to ESPN.
- DB credentials and provider keys are server-side only. Never `NEXT_PUBLIC_*`.
- Every schema change ships as an Alembic migration. Never `Base.metadata.create_all()` outside tests.
- Game `status` in the DB is exactly `scheduled` | `live` | `final` — never ESPN's raw strings.
- Frontend API calls go through `NEXT_PUBLIC_API_URL`. Never hardcode `http://localhost:8000`.
- **There is no RLS and no auth.** Postgres enforces nothing about who may read or write a row.
  Every endpoint touching user-scoped data must check the caller in FastAPI, explicitly. See F-001.

## Data boundary (one store as of D-038 — read the three constraints)
- **Postgres holds everything**: live teams/games synced from ESPN, model outputs, and — since
  **D-038** (2026-08-17) — the bulk historical corpus. This **supersedes** the previous two-store
  rule ("bulk history never enters Postgres wholesale"), which governed Phase 1.
- Bulk history still **never enters git** — `.gitignore` blocks `*.sqlite`/`*.duckdb`/`*.parquet`/`data/`.
- Three constraints make the single store safe, and none is optional:
  - **Migrations own schema; `model.ingest` owns data** (D-045). No migration inserts corpus rows.
  - **No query carries an as-of predicate** (D-039). SQL narrows by season or team; the as-of filter
    stays inside `features.py`, where T-006's property test proves it. Writing `WHERE date < :as_of`
    anywhere moves an integrity control into call sites and voids that proof. Enforced by an AST
    check over `store.py` that refuses the string `as_of` outright — **no exceptions, ever**;
    `load_upcoming` complied by moving its window into Python rather than arguing its case was
    different. `track_record.py` reads a table that *has* an `as_of` column, so it cannot satisfy
    that rule and carries its own: naming the column is allowed, comparing it in SQL is not.
  - **Integrity is verified at the ingest boundary** (D-046) — content hashes over source bytes
    before parsing, pinned counts, and `corpus.assert_curated` re-run on read.
- Consequences, recorded not absorbed: reproducing reported numbers needs a running Postgres (D-043),
  CI needs a service container for corpus-touching tests (D-044), and the API's DB role is
  `SELECT`-only on corpus tables while ingest holds a separate writing role (D-047).
- **Everything is ESPN-keyed — keep it that way.** The app's `teams.external_id` and the historical
  source share one ID space, so no translation layer exists or is needed. Adding a stats.nba.com-keyed
  source reintroduces a real join problem (F-005) — read that finding first, and use the published
  `nba_team_crosswalk` rather than hand-rolling a mapping on abbreviations.

## Agents available
backend-engineer · frontend-engineer · security-auditor · logic-reviewer · ui-ux-reviewer

## Domain notes

- **Backend** — FastAPI routes, services, data access. It is the only DB writer and the only caller
  of third-party APIs. Schema changes ship as Alembic migrations.
- **Frontend** — Next.js 16 App Router, React 19, server-vs-client component split. Calls FastAPI
  through `NEXT_PUBLIC_API_URL`; never Postgres, never ESPN.
- **Modeling** — pure modules under `backend/model/`, fixture-tested. Assert *which outputs* a
  fixture produces, never the internal formula. The analytical core is deliberately shaped this way
  so defects surface at the unit boundary.
- **Bytecode cache when mutation-testing.** CPython validates `.pyc` by `(mtime, size)`, so a
  size-preserving mutation reverted within the same second leaves valid-looking stale bytecode and
  you score a mutation against the *previous* mutant. Use a fresh `PYTHONPYCACHEPREFIX` per run and
  re-assert a green baseline between mutations. (This is how F-125 was confirmed.)

## Checks

Local setup is required — `ruff` and `pytest` are not in `requirements.txt` and not installed
globally. They live in `backend/venv`, which must be built on **Python 3.11** (`nba_service` uses
`datetime.UTC`, 3.11+):

```bash
python3.11 -m venv backend/venv
backend/venv/bin/pip install -r backend/requirements.txt
backend/venv/bin/pip install ruff==0.16.0 pytest==9.1.1 pytest-asyncio==1.4.0 respx==0.23.1
```

Run them with that venv on `PATH`:

```bash
PATH="$PWD/backend/venv/bin:$PATH" backend/venv/bin/pytest backend/tests
PATH="$PWD/backend/venv/bin:$PATH" backend/venv/bin/ruff check backend
cd frontend && npm run lint && npx tsc --noEmit && npm test
```

**`.github/workflows/ci.yml` is what blocks.** Its two jobs — `Frontend — Lint & Type Check` and
`Backend — Lint & Tests` — are the required status checks on `main`. CI provisions its own tools, so
no venv there.

> The canon `gate.yml` and `checks/` were removed 2026-08-24. The `gate` job was never a required
> status check; `ci.yml` always was. Two of the checks it ran were meta-checks over the tracker and
> review ledger, both of which no longer exist.

## Environments

**local → prod.** `docker-compose.yaml` is local; `docker-compose.prod.yaml` is production —
note it is currently a **0-byte file** (F-006, open in `docs/plans/phase-1-analytical-core.md`).

There is no Supabase and no Vercel here, so the Supabase/Vercel MCP warnings in `WORKFLOW.md` do not
apply to this project. Debugging tools that do: the local stack's logs, `pytest -k`, and Playwright
MCP against the local frontend.
