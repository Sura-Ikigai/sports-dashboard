# Stage 3 Design: NBA Data Dashboard

**Status:** Approved, ready for implementation planning
**Source spec:** `../../../../stage 3 spec.md` (original stage document, superseded in the areas below)

## Why this doc exists

The original Stage 3 spec is a strong teaching document but was written without testing the actual upstream APIs, and has a few gaps that would have blocked implementation or led to design mismatches if followed as-is. This doc records the decisions made to close those gaps, reached collaboratively before writing any code. Where this doc conflicts with `stage 3 spec.md`, this doc wins.

## Working style for this stage

Claude writes the implementation code (backend and frontend), explaining key decisions as it goes. The user runs servers, hits endpoints, and verifies behavior — same collaborative rhythm as Stage 1 & 2, just with more actual application code this time instead of config/infra.

## Data source: ESPN, not TheSportsDB

The original spec named TheSportsDB as primary and ESPN as a secondary/optional source, with an "adapter pattern" abstraction to swap between them. Live-testing both before writing any schema code found:

- TheSportsDB's shared public test key (`/3/` in the URL — not a personal key) returns **real but hard-capped data**: only 10 of 30 NBA teams from `search_all_teams.php?l=NBA`, only 1 past event from `eventspastleague.php`, and 0 events from `eventsnextleague.php`. `lookup_all_teams.php?id=4387` ignores the league ID entirely and returns unrelated English football teams under this key.
- A personal TheSportsDB key with the real free-tier rate limit (30 req/min) requires a **paid/premium account** — not actually free despite the spec's description.
- ESPN's unofficial API (`site.api.espn.com/apis/site/v2/sports/basketball/nba`), while undocumented, returned **complete real data** with no auth: all 30 teams from `/teams`, and `/scoreboard?dates=YYYYMMDD` (single date) or `?dates=YYYYMMDD-YYYYMMDD` (range) returns full, real game slates — verified with both a January 2026 regular-season date (9 games) and a June 2026 range (5 games).

**Decision:** ESPN is the sole data source for Stage 3. TheSportsDB is dropped, not deferred-with-an-adapter — building an adapter abstraction for a single real implementation is premature. If a second source becomes worth adding later (e.g., after a paid TheSportsDB key, or a different provider), that's a new adapter-pattern exercise at that time, not now.

This also means: no API key, no secret to manage, no `.env` addition needed for this stage's data source.

## Backend

### New dependencies (per original spec, unchanged)

```bash
pip install httpx apscheduler sqlalchemy alembic
pip freeze > requirements.txt
```

`httpx` for async-native external calls, `sqlalchemy` + `alembic` for the DB layer and its migrations. (`apscheduler` is used differently than the spec describes — see Scheduler section below.)

### Folder structure

```
backend/
├── main.py
├── models.py
├── database.py
├── schemas.py                 # Pydantic response models -- see "Response serialization" below
├── services/
│   ├── __init__.py
│   ├── espn_client.py          # ESPN API wrapper (replaces sportsdb_client.py)
│   └── nba_service.py          # Normalizes ESPN data, writes to DB (upsert)
├── routers/
│   ├── __init__.py
│   └── nba.py
├── alembic/                     # scaffolded via `alembic init alembic`
├── conftest.py                  # already exists from Stage 1 fix
└── tests/
    ├── test_health.py
    └── test_nba_service.py
```

### Schema, based on real ESPN fields

**`Team`**
| field | source |
|---|---|
| `id` | own PK |
| `external_id` | ESPN `team.id` (e.g. `"1"`) |
| `name` | ESPN `team.displayName` |
| `abbreviation` | ESPN `team.abbreviation` |
| `logo_url` | ESPN `team.logos[0].href` |
| `source` | literal `"espn"` |

**`Game`**
| field | source |
|---|---|
| `id` | own PK |
| `external_id` | ESPN `event.id` |
| `home_team_id` / `away_team_id` | FK to `teams.id`, resolved by matching competitor `team.id` against `Team.external_id` |
| `home_score` / `away_score` | nullable int, from competitor `score` |
| `status` | normalized (see below), not ESPN's raw string |
| `game_time` | ESPN `event.date` |
| `last_synced` | `datetime.utcnow()` on every sync |

**Status normalization:** ESPN's `competitions[0].status.type.name` has many specific values (`STATUS_FINAL`, `STATUS_SCHEDULED`, `STATUS_IN_PROGRESS`, `STATUS_HALFTIME`, etc.). Instead of storing that raw string, map from the more stable `status.type.state` field (`"pre"` / `"in"` / `"post"`) to our own `"scheduled"` / `"live"` / `"final"`. This matches what the frontend already expects to render and won't break if ESPN adds new in-progress status variants.

This schema is deliberately minimal (teams + games + final scores) per the user's explicit scope call. Extending later (venue, period-by-period scores, odds, etc.) is just another Alembic migration on top — not a redesign.

### Response serialization (gap in original spec)

The original spec's route code (`return db.query(Team).all()`) returns raw SQLAlchemy model instances, which FastAPI cannot JSON-serialize without a Pydantic schema — this would throw at request time. `schemas.py` (listed in the spec's folder structure but never actually shown wired in) will define `TeamOut` / `GameOut` Pydantic models with `model_config = {"from_attributes": True}`, and routes will declare `response_model=list[TeamOut]` etc.

### Migrations: real Alembic, not `create_all()`

Per the user's choice, we use Alembic properly rather than a shortcut `Base.metadata.create_all()`:

1. `alembic init alembic`
2. Edit `alembic/env.py` to import `Base` from `database.py` and read `DATABASE_URL` from the environment (not hardcoded in `alembic.ini` — that value differs between local venv and the Docker container)
3. `alembic revision --autogenerate -m "create teams and games tables"` — user runs this, reviews the generated migration file before it touches the DB
4. `alembic upgrade head` — user runs this

Phase 2 of the favorites feature (below) adds a second migration on top of this same setup.

### API client + service layer

`services/espn_client.py`: async httpx wrapper with `get_all_teams()` (`GET /teams`) and `get_games(date: str | None = None)` (`GET /scoreboard`, with `?dates=...` if a date is passed, otherwise ESPN's default of "today").

`services/nba_service.py`: `sync_teams(db)` and `sync_games(db, date=None)`, both following the spec's upsert pattern (check `external_id`, update if exists, insert if not, single `db.commit()` per sync).

### Routes

```
POST /nba/sync/teams
POST /nba/sync/games?date=YYYYMMDD   (date optional, defaults to today)
GET  /nba/teams?q=<search>            (q optional, case-insensitive partial match on name)
GET  /nba/games                       (most recent 20 by game_time, descending -- per original spec)
```

The `q` param on `/nba/teams` is what the frontend's debounced search box calls. `/nba/games` takes no date param (that's `sync/games`'s job) — it just lists what's already in the local DB, capped at 20 rows so the dashboard doesn't try to render an unbounded, ever-growing list as syncs accumulate history over time.

### Scheduler: `lifespan`, not `@app.on_event`

The spec uses `@app.on_event("startup")`, which is deprecated in FastAPI in favor of the `lifespan` context manager. We'll confirm which the installed FastAPI version (0.139.2, from Stage 1) actually supports, and use `lifespan` regardless since it's the current non-deprecated pattern — same 15-minute `apscheduler` interval the spec describes, just wired in via `lifespan` instead of `on_event`.

## Frontend

### Custom hooks (`frontend/hooks/`)

`useFetch`, `usePolling`, `useDebounce`, `useLocalStorage` — following the spec's implementations, with one fix:

**`useFetch` cleanup bug:** the spec creates the `AbortController` *inside* the async `fetchData` function and only returns the abort callback after `await fetch(...)` already resolved — meaning cleanup calls `abort()` on a request that's already finished, which does nothing. The controller needs to be created synchronously in the `useEffect`, before the async work starts, so unmount/URL-change during an in-flight request actually cancels it.

`useLocalStorage` is still built (generically useful, demonstrates the Next.js SSR `typeof window` guard) but is **not** wired to the favorites feature — see Favorites section below for why.

### API base URL

Use the `NEXT_PUBLIC_API_URL` env var (already set in `docker-compose.yaml` from Stage 1) instead of the spec's hardcoded `http://localhost:8000` string repeated across files.

### Dashboard

`app/page.tsx`: `useFetch<Game[]>` against `/nba/games`, `usePolling(refetch, 30000)`. `components/GameCard.tsx` renders each game with the Framer Motion spring animation on score change, keyed on the score value, per the spec.

### Team search (in scope this stage)

A search input on the dashboard, `useDebounce(searchInput, 400)`, firing `GET /nba/teams?q=...` only after the user stops typing for 400ms.

## Favorites: two-phase build

**Phase 1 — optimistic UI teaching exercise (temporary, spec's approach):**
`POST /nba/favorite/{team_id}` with a 20% random failure, no DB model. `FavoriteButton.tsx` per the spec: optimistic flip → fire request → rollback + shake animation on failure. Built specifically so the user can click repeatedly and *see* the rollback/shake fire on ~1 in 5 clicks before it's replaced.

**Phase 2 — real persistence (replaces phase 1's endpoint):**
- New `Favorite` model (`team_id` FK, `created_at`), added via a second Alembic migration
- `POST /nba/favorite/{team_id}` now actually toggles a row (insert if absent, delete if present), no more fake failure
- `GET /nba/favorites` added so the dashboard can hydrate each button's initial starred state on load — the original spec never explains where its `initialFavorited` prop value comes from; this closes that gap
- Favorites are global to the app, not per-user — there's no user-account system yet, and adding one is out of scope for this stage

**Why not `useLocalStorage` for this?** The original spec shows both a `useLocalStorage` usage example ("remembering favorite teams") *and* the backend fake-failure endpoint for the same feature, without reconciling them. Using both would reintroduce a real desync bug (local storage says favorited, backend disagrees). Real backend persistence via the `Favorite` table is the single source of truth.

## What to verify by end of Stage 3

- [ ] `POST /nba/sync/teams` populates `teams` with all 30 real NBA teams from ESPN
- [ ] `POST /nba/sync/games` populates `games` with real games (test against a historical date, since it's NBA off-season)
- [ ] `GET /nba/teams` and `GET /nba/games` return data from the local DB (via `schemas.py` response models), not directly proxying ESPN
- [ ] Dashboard loads, displays games, polls every 30s
- [ ] Search box only fires a request 400ms after the user stops typing
- [ ] Favorite button (phase 1): optimistic update fires instantly, rollback + shake fires on ~20% of clicks
- [ ] Favorite button (phase 2): after replacing phase 1, favorited state persists across a page reload (via `GET /nba/favorites` hydration)
- [ ] Score changes animate with the spring effect (fake via `psql` `UPDATE` on a game row, watch the next poll pick it up)
- [ ] Alembic: initial migration and the favorites migration both exist as real reviewed files, `alembic upgrade head` applied cleanly

## Explicitly out of scope this stage

- TheSportsDB integration (dropped, not deferred-with-a-hook)
- An actual multi-source adapter abstraction (no second real source exists yet to justify it)
- Per-user favorites / any user-account system
- Real Vercel/Railway deploys (still Stage 3's placeholder echo commands from `deploy.yml`, unchanged from Stage 1 & 2)
