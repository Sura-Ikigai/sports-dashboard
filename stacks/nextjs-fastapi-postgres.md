# Stack overlay — Next.js · FastAPI · Postgres

<!--
Sibling of `nextjs-fastapi-supabase.md` for projects that run **plain Postgres** (Docker Compose,
Railway, RDS) instead of Supabase. The difference is not cosmetic: with no Supabase there is no RLS,
so the database enforces NOTHING about who may read or write a row. FastAPI is the ONLY authorization
boundary, which makes the API-level authz test load-bearing rather than defense-in-depth.

Drop the **Stack** and **Invariants** sections into a project's CLAUDE.md (SYSTEM.md §6.2).
-->

## Stack
Next.js (App Router) · FastAPI · Postgres (Docker Compose) · Alembic

## Invariants (never violate)
- FastAPI is the only DB writer. The frontend calls FastAPI, never Postgres directly.
- FastAPI owns all outbound third-party API calls. The browser never talks to an upstream provider.
- DB credentials and provider keys are server-side only, sourced from env. Never `NEXT_PUBLIC_*`.
- Every schema change ships as an Alembic migration. Never `Base.metadata.create_all()` outside tests.
- Validate all input at the boundary (Pydantic, server-side); parameterized queries / ORM only.
- **There is no RLS.** Postgres will not save you from a missing authorization check — every endpoint
  that reads or mutates user-scoped data must assert the caller's right to it, in FastAPI, explicitly.

## Builder domain notes (§6.2 — per-stack knowledge for the builder agents)
- **backend-engineer:** FastAPI routers / services / data-access; Pydantic request-response models;
  SQLAlchemy sessions via dependency injection; Alembic migrations; upsert patterns for sync jobs;
  scheduled work (APScheduler) kept idempotent so a re-run is harmless.
- **frontend-engineer:** Next.js App Router (server vs client components); data fetching via FastAPI
  only, through `NEXT_PUBLIC_API_URL` (never a hardcoded host); loading / error / empty states;
  accessibility; optimistic UI that reconciles against the server response.
- **db / migration work:** schema + Alembic; indexes ship with the query that needs them; a migration
  is reversible or explicitly documented as one-way.

## Required gates (manifest)

<!-- SYSTEM.md §5.4. No RLS in this stack, so `rls-coverage` is deliberately ABSENT — it would be a
     gate over a mechanism that does not exist. Its safety role transfers to `authz-deny`: a two-user
     deny test THROUGH the API. Add that check the moment the project grows an auth boundary; until
     then a project on this stack has NO authorization gate, which the tracker must carry as an open
     finding rather than silently omit. Universal meta-checks (review-ledger-current,
     gate-completeness) run automatically and are not listed. -->

```yaml
required-gates:
  reviewers:
    - security-auditor
    - logic-reviewer
    - ui-ux-reviewer
  checks:
    - { id: fe-typecheck, run: "cd frontend && npx tsc --noEmit", blocking: true }
    - { id: fe-lint,      run: "cd frontend && npm run lint",     blocking: true }
    - { id: fe-unit,      run: "cd frontend && npm test",         blocking: true }
    - { id: be-lint,      run: "cd backend && ruff check .",      blocking: true }
    - { id: be-unit,      run: "cd backend && pytest",            blocking: true }
```

<!-- Checks to ADD as the project earns them (declare only what is installed — an undeclared gate is
     honest, a declared-but-missing one fails `gate-completeness` and is what canon exists to prevent):
     - { id: authz-deny, run: "cd backend && pytest tests/authz", blocking: true }  # once auth exists
     - { id: a11y,       run: "npx playwright test tests/a11y",   blocking: true }  # once Playwright is installed
     - { id: perf,       run: "node checks/perf-budgets.mjs",     blocking: true }  # once perf-budgets.json is tuned
-->
