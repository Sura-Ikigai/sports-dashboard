# Sports Dashboard — CLAUDE.md

## Read these first
- `docs/IMPLEMENTATION.md` ← current state + tasks + review ledger, **ALWAYS read before acting**.
  Deliberately kept small (~350 lines) so reading it is cheap on every task.
- `docs/plans/PLAN-current.md` ← the active plan

## Read on demand — do NOT read end to end
- `docs/findings.md` — every finding's evidence and remediation (append-only, large and growing).
  The *status index* in `IMPLEMENTATION.md` tells you whether a finding is open; come here only for
  the ones a task cites. `grep -n 'F-0NN' docs/findings.md`
- `docs/decisions.md` — the decisions log (append-only). `grep -n 'D-0NN' docs/decisions.md`
- `docs/log.md` — session-by-session chronology.

These three were split out of the tracker on 2026-08-10: it had reached 1,367 lines, 74% of it
history, and every agent was told to read all of it — 2,176 lines of process to review 2,206 lines
of code. Splitting cut mandatory reading by 74% and, more importantly, stopped it growing per task.

## Stack
Next.js 16 App Router (React 19) · FastAPI · Postgres 16 (Docker Compose) · Alembic
Stack overlay: `stacks/nextjs-fastapi-postgres.md`

## Invariants (never violate)
- FastAPI is the only DB writer. The frontend calls FastAPI, never Postgres directly.
- FastAPI owns all outbound third-party API calls. The browser never talks to ESPN.
- DB credentials and provider keys are server-side only. Never `NEXT_PUBLIC_*`.
- Every schema change ships as an Alembic migration. Never `Base.metadata.create_all()` outside tests.
- Game `status` in the DB is exactly `scheduled` | `live` | `final` — never ESPN's raw strings.
- Frontend API calls go through `NEXT_PUBLIC_API_URL`. Never hardcode `http://localhost:8000`.
- **There is no RLS and no auth.** Postgres enforces nothing about who may read or write a row.
  Every endpoint touching user-scoped data must check the caller in FastAPI, explicitly. See F-001.

## Data boundary (this project has two stores — keep them separate)
- **Postgres** is the *application* store: live teams/games synced from ESPN, and model outputs.
- The **historical store** holds bulk history from sportsdataverse (D-004), seasons 2022–2026. It
  never enters Postgres wholesale and never enters git — `.gitignore` blocks
  `*.sqlite`/`*.duckdb`/`*.parquet`/`data/`. Its shape is a planning decision (T-002/T-003).
- **Everything is ESPN-keyed — keep it that way.** The app's `teams.external_id` and the historical
  source share one ID space, so no translation layer exists or is needed. Adding a stats.nba.com-keyed
  source reintroduces a real join problem (F-005) — read that finding first, and use the published
  `nba_team_crosswalk` rather than hand-rolling a mapping on abbreviations.

## Agents available
backend-engineer · frontend-engineer · security-auditor · logic-reviewer · ui-ux-reviewer

## The gate

**Local setup (required — the gate exits 1 without it).** `ruff` and `pytest` are not in
`requirements.txt` and are not installed globally; they live in `backend/venv`, which must be built
on **Python 3.11** (`nba_service` uses `datetime.UTC`, which is 3.11+):

```bash
python3.11 -m venv backend/venv
backend/venv/bin/pip install -r backend/requirements.txt
backend/venv/bin/pip install ruff==0.16.0 pytest==9.1.1 pytest-asyncio==1.4.0 respx==0.23.1
```

Then run the gate with that venv on `PATH` (activate it, or prefix the command):

```bash
PATH="$PWD/backend/venv/bin:$PATH" node checks/run-gate.mjs --stack stacks/nextjs-fastapi-postgres.md
```

CI provisions the same tools itself via `pip install`, so `.github/workflows/gate.yml` needs no venv.

It runs the two meta-checks (`gate-completeness`, `review-ledger-current`) plus fe-typecheck,
fe-lint, fe-unit, be-lint, be-unit — same runner locally and in CI, so "green" means one thing.
Canon gates this project does **not** yet run: `a11y`, `perf`, `authz-deny` (tooling/auth absent —
tracked in *Future hardening*, deliberately undeclared so `gate-completeness` stays honest).

## Workflow
Plan (grill-me → to-PRD → versioned plan → tracker tasks) →
Build (delegate by owner) → Review (gate) → **update the tracker and `git commit` on completion**.
Each milestone (§5.4): capture candidate learnings to `docs/learning-notes.md`.

## Note on `docs/superpowers/`
Stage 3 was built with a different (superpowers) workflow; its plan and design doc live there as a
historical record. `docs/plans/` is the Dev-System's home for plans going forward — don't add to
`docs/superpowers/`.
