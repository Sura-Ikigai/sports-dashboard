# Stage 3 NBA Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Stage 3 NBA dashboard: a FastAPI backend that syncs real NBA team/game data from ESPN's unofficial API into Postgres, and a Next.js frontend with custom hooks, a polling dashboard, debounced team search, and an optimistic-UI favorite button with score-change animation.

**Architecture:** FastAPI backend owns all external API calls (never the frontend), normalizes ESPN's JSON into two SQLAlchemy-backed tables (`teams`, `games`) via an upsert pattern, and exposes its own REST API. The Next.js frontend only ever talks to this backend, never to ESPN directly. Four custom React hooks (`useFetch`, `usePolling`, `useDebounce`, `useLocalStorage`) provide the frontend's data-fetching and UX primitives.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, httpx, APScheduler, pytest + respx (backend); Next.js 16 (App Router) + React 19, Framer Motion, Vitest + React Testing Library (frontend); Postgres 16, Docker Compose.

## Global Constraints

- Data source is ESPN's unofficial API (`https://site.api.espn.com/apis/site/v2/sports/basketball/nba`) exclusively — no TheSportsDB, no API key, no adapter abstraction (see design doc `docs/superpowers/specs/2026-07-18-stage3-nba-dashboard-design.md` for why).
- Backend code changes are picked up live by the running `sports_backend` container (bind-mounted volume + `uvicorn --reload`) — no rebuild needed unless `requirements.txt` changes.
- Frontend code changes are picked up live by the running `sports_frontend` container the same way.
- Use real Alembic migrations for every schema change — never `Base.metadata.create_all()`.
- `GET /nba/games` returns at most 20 rows, most recent `game_time` first.
- Game `status` values stored in the DB are always one of `"scheduled"` / `"live"` / `"final"` (normalized from ESPN's `status.type.state`), never ESPN's raw status strings.
- Frontend hook tasks (Tasks 9–12) get automated Vitest unit tests. Dashboard/component-level UI (Tasks 13, 14, 15, 16) is verified manually via the running dev server — no component/integration test framework is being added this stage.
- `NEXT_PUBLIC_API_URL` (already set in `docker-compose.yaml`) is used for all frontend API calls — never hardcode `http://localhost:8000`.
- All commands below assume Windows PowerShell as the shell and are run from `sports-dashboard/` unless a `cd` is shown. Docker CLI is invoked as `docker` (already confirmed on PATH).

---

### Task 1: Backend dependencies + Docker image rebuild

**Files:**
- Modify: `backend/requirements.txt` (regenerated via `pip freeze`)

**Interfaces:**
- Produces: `httpx`, `apscheduler`, `sqlalchemy`, `alembic` importable inside the `sports_backend` container from Task 2 onward.

- [ ] **Step 1: Install the new packages into the existing venv**

```powershell
cd backend
.\venv\Scripts\pip.exe install httpx apscheduler sqlalchemy alembic
```

- [ ] **Step 2: Regenerate requirements.txt**

```powershell
.\venv\Scripts\pip.exe freeze > requirements.txt
```

Verify it's plain UTF-8/ASCII, not UTF-16 (the Stage 1 gotcha): open it in the editor and confirm no null-byte-looking garbled text, or run:

```powershell
cd ..
```
```bash
file backend/requirements.txt
```
Expected: `ASCII text` or `UTF-8 text`, not anything mentioning UTF-16.

- [ ] **Step 3: Rebuild the backend image so the new packages are baked in**

```bash
docker compose up --build -d backend
```

- [ ] **Step 4: Verify the packages import inside the running container**

```bash
docker compose exec backend python -c "import httpx, apscheduler, sqlalchemy, alembic; print('ok')"
```
Expected output: `ok`

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt
git commit -m "Add httpx, apscheduler, sqlalchemy, alembic dependencies for Stage 3"
```

---

### Task 2: Database connection module + SQLAlchemy models

**Files:**
- Create: `backend/database.py`
- Create: `backend/models.py`

**Interfaces:**
- Produces: `Base` (declarative base), `SessionLocal` (sessionmaker), `get_db()` (FastAPI dependency) from `database.py`. `Team`, `Game` model classes from `models.py`, both registered on `Base.metadata`.
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write `database.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# Falls back to a syntactically valid (but unused) URL so `create_engine`
# never raises on import when DATABASE_URL isn't set -- e.g. running
# pytest locally outside Docker, where nothing ever calls `engine.connect()`
# because tests use their own separate in-memory SQLite session (see conftest.py).
DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite:///./_unused_local_fallback.db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """Dependency-injected DB session -- FastAPI closes this automatically after each request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 2: Write `models.py`**

```python
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
import datetime

class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String, unique=True, index=True, nullable=False)   # ESPN team.id
    name = Column(String, nullable=False)
    abbreviation = Column(String)
    logo_url = Column(String)
    source = Column(String, default="espn")

class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String, unique=True, index=True, nullable=False)   # ESPN event.id
    home_team_id = Column(Integer, ForeignKey("teams.id"))
    away_team_id = Column(Integer, ForeignKey("teams.id"))
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    status = Column(String)          # normalized: "scheduled" | "live" | "final"
    game_time = Column(DateTime)
    last_synced = Column(DateTime, default=datetime.datetime.utcnow)

    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])
```

- [ ] **Step 3: Verify both modules import cleanly inside the container**

```bash
docker compose exec backend python -c "from models import Team, Game; from database import Base; print(list(Base.metadata.tables.keys()))"
```
Expected output: `['teams', 'games']`

- [ ] **Step 4: Commit**

```bash
git add backend/database.py backend/models.py
git commit -m "Add SQLAlchemy Team and Game models"
```

---

### Task 3: Alembic setup + initial migration

**Files:**
- Create: `backend/alembic.ini` (generated by `alembic init`)
- Create: `backend/alembic/env.py` (generated, then edited)
- Create: `backend/alembic/versions/<hash>_create_teams_and_games_tables.py` (autogenerated)

**Interfaces:**
- Consumes: `Base` from `database.py`, `Team`/`Game` from `models.py` (Task 2)
- Produces: a real, applied `teams`/`games` schema in the running Postgres container — every later backend task that touches the DB depends on this having run.

- [ ] **Step 1: Scaffold Alembic**

```powershell
cd backend
.\venv\Scripts\alembic.exe init alembic
cd ..
```

This creates `backend/alembic.ini` and `backend/alembic/` (`env.py`, `script.py.mako`, `versions/`).

- [ ] **Step 2: Edit `backend/alembic/env.py` to target our models and read `DATABASE_URL`**

Find this block near the top of the generated file:

```python
config = context.config
```

Immediately after it, add:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import Base
from models import Team, Game  # noqa: F401 -- registers models on Base.metadata

config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
```

Then find:

```python
target_metadata = None
```

Change it to:

```python
target_metadata = Base.metadata
```

- [ ] **Step 3: Generate the initial migration (run inside the container, so `DATABASE_URL` resolves the `db` hostname correctly)**

```bash
docker compose exec backend alembic revision --autogenerate -m "create teams and games tables"
```

Expected output includes lines like:
```
Generating /app/alembic/versions/<hash>_create_teams_and_games_tables.py ...  done
```

- [ ] **Step 4: Review the generated migration file**

Open `backend/alembic/versions/<hash>_create_teams_and_games_tables.py` (path from Step 3's output) and confirm the `upgrade()` function contains two `op.create_table(...)` calls — one for `teams`, one for `games` — with columns matching `models.py`. This is autogenerated, not hand-written, but reviewing it before applying is the entire point of using a real migration tool instead of `create_all()`.

- [ ] **Step 5: Apply the migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected output ends with:
```
Running upgrade  -> <hash>, create teams and games tables
```

- [ ] **Step 6: Verify the tables exist in Postgres**

```bash
docker compose exec db psql -U sports_user -d sports_dashboard -c "\dt"
```
Expected: a table listing including `teams`, `games`, and `alembic_version`.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic.ini backend/alembic/
git commit -m "Add Alembic migrations, initial teams/games schema"
```

---

### Task 4: ESPN API client + tests

**Files:**
- Create: `backend/services/__init__.py` (empty)
- Create: `backend/services/espn_client.py`
- Create: `backend/tests/test_espn_client.py`
- Modify: `.github/workflows/ci.yml:508-510` (add `respx` to the backend test-install step)

**Interfaces:**
- Produces: `espn_client.get_all_teams() -> list[dict]` (each dict is ESPN's raw `team` object: `id`, `displayName`, `abbreviation`, `logos`), `espn_client.get_games(date: str | None = None) -> list[dict]` (each dict is ESPN's raw `event` object).
- Consumes: nothing from earlier tasks (pure API wrapper).

- [ ] **Step 1: Install respx locally for writing/running these tests**

```powershell
cd backend
.\venv\Scripts\pip.exe install respx
cd ..
```

- [ ] **Step 2: Create the services package**

```powershell
New-Item -ItemType File backend\services\__init__.py
```

- [ ] **Step 3: Write the failing tests**

Create `backend/tests/test_espn_client.py`:

```python
import pytest
import respx
import httpx
from services import espn_client

TEAMS_RESPONSE = {
    "sports": [{
        "leagues": [{
            "teams": [
                {"team": {"id": "1", "displayName": "Atlanta Hawks", "abbreviation": "ATL",
                          "logos": [{"href": "https://a.espncdn.com/i/teamlogos/nba/500/atl.png"}]}},
                {"team": {"id": "2", "displayName": "Boston Celtics", "abbreviation": "BOS",
                          "logos": [{"href": "https://a.espncdn.com/i/teamlogos/nba/500/bos.png"}]}},
            ]
        }]
    }]
}

SCOREBOARD_RESPONSE = {
    "events": [
        {
            "id": "401810433",
            "date": "2026-01-15T19:00Z",
            "competitions": [{
                "status": {"type": {"name": "STATUS_FINAL", "state": "post"}},
                "competitors": [
                    {"id": "19", "homeAway": "home", "score": "118", "team": {"id": "19"}},
                    {"id": "29", "homeAway": "away", "score": "111", "team": {"id": "29"}},
                ],
            }],
        }
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_get_all_teams_returns_flattened_team_list():
    respx.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams").mock(
        return_value=httpx.Response(200, json=TEAMS_RESPONSE)
    )

    teams = await espn_client.get_all_teams()

    assert len(teams) == 2
    assert teams[0]["displayName"] == "Atlanta Hawks"
    assert teams[0]["id"] == "1"


@pytest.mark.asyncio
@respx.mock
async def test_get_games_without_date_hits_scoreboard_with_no_params():
    route = respx.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard").mock(
        return_value=httpx.Response(200, json=SCOREBOARD_RESPONSE)
    )

    events = await espn_client.get_games()

    assert route.calls.last.request.url.params.get("dates") is None
    assert len(events) == 1
    assert events[0]["id"] == "401810433"


@pytest.mark.asyncio
@respx.mock
async def test_get_games_with_date_passes_dates_param():
    route = respx.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard").mock(
        return_value=httpx.Response(200, json=SCOREBOARD_RESPONSE)
    )

    await espn_client.get_games(date="20260115")

    assert route.calls.last.request.url.params.get("dates") == "20260115"


@pytest.mark.asyncio
@respx.mock
async def test_get_all_teams_raises_on_http_error():
    respx.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await espn_client.get_all_teams()
```

- [ ] **Step 4: Run the tests to verify they fail (module doesn't exist yet)**

```powershell
cd backend
.\venv\Scripts\pytest.exe tests/test_espn_client.py -v
cd ..
```
Expected: `ModuleNotFoundError: No module named 'services.espn_client'` (or similar import error) for every test.

- [ ] **Step 5: Write `backend/services/espn_client.py`**

```python
import httpx

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

async def get_all_teams():
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/teams")
        response.raise_for_status()
        data = response.json()
        return [t["team"] for t in data["sports"][0]["leagues"][0]["teams"]]

async def get_games(date: str | None = None):
    params = {"dates": date} if date else {}
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/scoreboard", params=params)
        response.raise_for_status()
        return response.json().get("events", [])
```

- [ ] **Step 6: Run the tests to verify they pass**

```powershell
cd backend
.\venv\Scripts\pytest.exe tests/test_espn_client.py -v
cd ..
```
Expected: `4 passed`

- [ ] **Step 7: Add `respx` to the CI test-install step**

In `.github/workflows/ci.yml`, find:
```yaml
      - name: Run tests
        working-directory: backend
        env:
          DATABASE_URL: postgresql://${{ secrets.DB_USER }}:${{ secrets.DB_PASSWORD }}@localhost:5432/${{ secrets.DB_NAME }}
        run: |
          pip install pytest pytest-asyncio httpx
          pytest tests/ -v        # Will pass if no tests yet — add a /tests folder
```
Change the `pip install` line to:
```yaml
          pip install pytest pytest-asyncio httpx respx
```

- [ ] **Step 8: Commit**

```bash
git add backend/services/__init__.py backend/services/espn_client.py backend/tests/test_espn_client.py .github/workflows/ci.yml
git commit -m "Add ESPN API client with respx-mocked tests"
```

---

### Task 5: In-memory test DB fixture + `sync_teams`

**Files:**
- Modify: `backend/conftest.py` (currently empty, from Stage 1)
- Create: `backend/services/nba_service.py`
- Create: `backend/tests/test_nba_service.py`

**Interfaces:**
- Consumes: `Base` from `database.py`, `Team`/`Game` from `models.py` (Task 2), `espn_client.get_all_teams`/`get_games` (Task 4)
- Produces: `nba_service.sync_teams(db: Session) -> None`, a pytest fixture `db_session` yielding a `Session` bound to a fresh in-memory SQLite DB per test.

- [ ] **Step 1: Add the test DB fixture to `backend/conftest.py`**

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models  # noqa: F401 -- registers Team/Game on Base.metadata before create_all


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_nba_service.py`:

```python
from unittest.mock import AsyncMock, patch
from models import Team


RAW_TEAMS = [
    {"id": "1", "displayName": "Atlanta Hawks", "abbreviation": "ATL",
     "logos": [{"href": "https://a.espncdn.com/i/teamlogos/nba/500/atl.png"}]},
    {"id": "2", "displayName": "Boston Celtics", "abbreviation": "BOS",
     "logos": [{"href": "https://a.espncdn.com/i/teamlogos/nba/500/bos.png"}]},
]


async def test_sync_teams_inserts_new_teams(db_session):
    from services import nba_service

    with patch("services.espn_client.get_all_teams", new=AsyncMock(return_value=RAW_TEAMS)):
        await nba_service.sync_teams(db_session)

    teams = db_session.query(Team).order_by(Team.external_id).all()
    assert len(teams) == 2
    assert teams[0].external_id == "1"
    assert teams[0].name == "Atlanta Hawks"
    assert teams[0].logo_url == "https://a.espncdn.com/i/teamlogos/nba/500/atl.png"
    assert teams[0].source == "espn"


async def test_sync_teams_updates_existing_team_instead_of_duplicating(db_session):
    from services import nba_service

    db_session.add(Team(external_id="1", name="Old Name", abbreviation="OLD", source="espn"))
    db_session.commit()

    with patch("services.espn_client.get_all_teams", new=AsyncMock(return_value=RAW_TEAMS)):
        await nba_service.sync_teams(db_session)

    teams = db_session.query(Team).filter(Team.external_id == "1").all()
    assert len(teams) == 1
    assert teams[0].name == "Atlanta Hawks"
```

- [ ] **Step 3: Run to verify it fails**

```powershell
cd backend
.\venv\Scripts\pytest.exe tests/test_nba_service.py -v
cd ..
```
Expected: `ModuleNotFoundError: No module named 'services.nba_service'`

- [ ] **Step 4: Write `backend/services/nba_service.py` (sync_teams only for now)**

```python
from sqlalchemy.orm import Session
from services import espn_client
from models import Team


async def sync_teams(db: Session):
    """Pulls teams from ESPN, upserts into local DB."""
    raw_teams = await espn_client.get_all_teams()

    for t in raw_teams:
        logos = t.get("logos") or []
        logo_url = logos[0]["href"] if logos else None

        existing = db.query(Team).filter(Team.external_id == t["id"]).first()
        if existing:
            existing.name = t["displayName"]
            existing.abbreviation = t.get("abbreviation")
            existing.logo_url = logo_url
        else:
            db.add(Team(
                external_id=t["id"],
                name=t["displayName"],
                abbreviation=t.get("abbreviation"),
                logo_url=logo_url,
                source="espn",
            ))

    db.commit()
```

- [ ] **Step 5: Run to verify it passes**

```powershell
cd backend
.\venv\Scripts\pytest.exe tests/test_nba_service.py -v
cd ..
```
Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/conftest.py backend/services/nba_service.py backend/tests/test_nba_service.py
git commit -m "Add sync_teams with in-memory SQLite test fixture"
```

---

### Task 6: `sync_games` with status normalization

**Files:**
- Modify: `backend/services/nba_service.py`
- Modify: `backend/tests/test_nba_service.py`

**Interfaces:**
- Consumes: `Team`, `Game` from `models.py`; `espn_client.get_games` (Task 4)
- Produces: `nba_service.sync_games(db: Session, date: str | None = None) -> None`, `nba_service._normalize_status(state: str) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_nba_service.py`:

```python
from models import Game

RAW_GAME_FINAL = {
    "id": "401810433",
    "date": "2026-01-15T19:00Z",
    "competitions": [{
        "status": {"type": {"name": "STATUS_FINAL", "state": "post"}},
        "competitors": [
            {"homeAway": "home", "score": "118", "team": {"id": "19"}},
            {"homeAway": "away", "score": "111", "team": {"id": "29"}},
        ],
    }],
}

RAW_GAME_SCHEDULED = {
    "id": "401810999",
    "date": "2026-01-20T19:00Z",
    "competitions": [{
        "status": {"type": {"name": "STATUS_SCHEDULED", "state": "pre"}},
        "competitors": [
            {"homeAway": "home", "score": "0", "team": {"id": "19"}},
            {"homeAway": "away", "score": "0", "team": {"id": "29"}},
        ],
    }],
}


def _seed_two_teams(db_session):
    db_session.add(Team(external_id="19", name="Orlando Magic", source="espn"))
    db_session.add(Team(external_id="29", name="Memphis Grizzlies", source="espn"))
    db_session.commit()


async def test_sync_games_inserts_final_game_with_scores(db_session):
    from services import nba_service

    _seed_two_teams(db_session)

    with patch("services.espn_client.get_games", new=AsyncMock(return_value=[RAW_GAME_FINAL])):
        await nba_service.sync_games(db_session)

    game = db_session.query(Game).filter(Game.external_id == "401810433").first()
    assert game is not None
    assert game.status == "final"
    assert game.home_score == 118
    assert game.away_score == 111


async def test_sync_games_scheduled_game_has_null_scores_and_scheduled_status(db_session):
    from services import nba_service

    _seed_two_teams(db_session)

    with patch("services.espn_client.get_games", new=AsyncMock(return_value=[RAW_GAME_SCHEDULED])):
        await nba_service.sync_games(db_session)

    game = db_session.query(Game).filter(Game.external_id == "401810999").first()
    assert game is not None
    assert game.status == "scheduled"


async def test_sync_games_skips_game_whose_teams_are_not_synced_yet(db_session):
    from services import nba_service

    # No teams seeded this time.
    with patch("services.espn_client.get_games", new=AsyncMock(return_value=[RAW_GAME_FINAL])):
        await nba_service.sync_games(db_session)

    assert db_session.query(Game).count() == 0


async def test_sync_games_updates_existing_game_instead_of_duplicating(db_session):
    from services import nba_service

    _seed_two_teams(db_session)

    with patch("services.espn_client.get_games", new=AsyncMock(return_value=[RAW_GAME_SCHEDULED])):
        await nba_service.sync_games(db_session)
    with patch("services.espn_client.get_games", new=AsyncMock(return_value=[RAW_GAME_FINAL])):
        await nba_service.sync_games(db_session, date="20260115")

    games = db_session.query(Game).all()
    assert len(games) == 2  # the scheduled game (different external_id) plus the final one
    final_game = db_session.query(Game).filter(Game.external_id == "401810433").first()
    assert final_game.status == "final"
```

- [ ] **Step 2: Run to verify it fails**

```powershell
cd backend
.\venv\Scripts\pytest.exe tests/test_nba_service.py -v
cd ..
```
Expected: `AttributeError: module 'services.nba_service' has no attribute 'sync_games'` for the four new tests (the two `sync_teams` tests still pass).

- [ ] **Step 3: Add `sync_games` to `backend/services/nba_service.py`**

Add these imports at the top of the file (alongside the existing ones):

```python
import datetime
from models import Game
```

Append to the file:

```python
_STATUS_STATE_MAP = {"pre": "scheduled", "in": "live", "post": "final"}


def _normalize_status(state: str) -> str:
    return _STATUS_STATE_MAP.get(state, "scheduled")


async def sync_games(db: Session, date: str | None = None):
    """Pulls games from ESPN, upserts into local DB. Skips games whose teams haven't been synced."""
    raw_events = await espn_client.get_games(date)

    for event in raw_events:
        competition = event["competitions"][0]
        status = _normalize_status(competition["status"]["type"]["state"])

        home = next(c for c in competition["competitors"] if c["homeAway"] == "home")
        away = next(c for c in competition["competitors"] if c["homeAway"] == "away")

        home_team = db.query(Team).filter(Team.external_id == home["team"]["id"]).first()
        away_team = db.query(Team).filter(Team.external_id == away["team"]["id"]).first()
        if not home_team or not away_team:
            continue  # run sync_teams first -- this game's teams aren't in the DB yet

        home_score = int(home["score"]) if home.get("score") not in (None, "") else None
        away_score = int(away["score"]) if away.get("score") not in (None, "") else None
        game_time = datetime.datetime.fromisoformat(event["date"])

        existing = db.query(Game).filter(Game.external_id == event["id"]).first()
        if existing:
            existing.home_score = home_score
            existing.away_score = away_score
            existing.status = status
            existing.last_synced = datetime.datetime.utcnow()
        else:
            db.add(Game(
                external_id=event["id"],
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                home_score=home_score,
                away_score=away_score,
                status=status,
                game_time=game_time,
                last_synced=datetime.datetime.utcnow(),
            ))

    db.commit()
```

- [ ] **Step 4: Run to verify it passes**

```powershell
cd backend
.\venv\Scripts\pytest.exe tests/test_nba_service.py -v
cd ..
```
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/services/nba_service.py backend/tests/test_nba_service.py
git commit -m "Add sync_games with status normalization and team-resolution guard"
```

---

### Task 7: Response schemas + routes + main.py wiring

**Files:**
- Create: `backend/schemas.py`
- Create: `backend/routers/__init__.py` (empty)
- Create: `backend/routers/nba.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `Team`, `Game` from `models.py`; `get_db` from `database.py`; `nba_service.sync_teams`/`sync_games` (Tasks 5, 6)
- Produces: live HTTP routes `POST /nba/sync/teams`, `POST /nba/sync/games`, `GET /nba/teams`, `GET /nba/games` — the frontend (Tasks 13–16) depends on these existing and returning JSON matching `TeamOut`/`GameOut`.

- [ ] **Step 1: Write `backend/schemas.py`**

```python
from pydantic import BaseModel
from datetime import datetime


class TeamOut(BaseModel):
    id: int
    external_id: str
    name: str
    abbreviation: str | None
    logo_url: str | None
    source: str | None

    model_config = {"from_attributes": True}


class GameOut(BaseModel):
    id: int
    external_id: str
    home_team: TeamOut
    away_team: TeamOut
    home_score: int | None
    away_score: int | None
    status: str
    game_time: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Create the routers package**

```powershell
New-Item -ItemType File backend\routers\__init__.py
```

- [ ] **Step 3: Write `backend/routers/nba.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from services import nba_service
from models import Team, Game
from schemas import TeamOut, GameOut

router = APIRouter(prefix="/nba", tags=["nba"])


@router.post("/sync/teams")
async def sync_teams(db: Session = Depends(get_db)):
    await nba_service.sync_teams(db)
    return {"status": "synced"}


@router.post("/sync/games")
async def sync_games(date: str | None = None, db: Session = Depends(get_db)):
    await nba_service.sync_games(db, date)
    return {"status": "synced"}


@router.get("/teams", response_model=list[TeamOut])
def list_teams(q: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Team)
    if q:
        query = query.filter(Team.name.ilike(f"%{q}%"))
    return query.all()


@router.get("/games", response_model=list[GameOut])
def list_games(db: Session = Depends(get_db)):
    return db.query(Game).order_by(Game.game_time.desc()).limit(20).all()
```

- [ ] **Step 4: Modify `backend/main.py` to include the router**

Current content:
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "ok", "environment": os.getenv("ENVIRONMENT", "local")}

@app.get("/")
def root():
    return {"message": "Sports Dashboard API"}
```

Replace with:
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from routers import nba

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(nba.router)

@app.get("/health")
def health_check():
    return {"status": "ok", "environment": os.getenv("ENVIRONMENT", "local")}

@app.get("/")
def root():
    return {"message": "Sports Dashboard API"}
```

(The scheduler/lifespan wiring is a separate task — Task 8 — so `main.py` gets touched again there.)

- [ ] **Step 5: Manually verify via the running container**

```bash
docker compose exec backend curl -s -X POST http://localhost:8000/nba/sync/teams
```
Expected: `{"status":"synced"}`

```bash
docker compose exec backend curl -s "http://localhost:8000/nba/teams" | head -c 300
```
Expected: a JSON array of 30 team objects with `id`, `external_id`, `name`, `abbreviation`, `logo_url`, `source` fields.

```bash
docker compose exec backend curl -s -X POST "http://localhost:8000/nba/sync/games?date=20260115"
```
Expected: `{"status":"synced"}`

```bash
docker compose exec backend curl -s "http://localhost:8000/nba/games" | head -c 500
```
Expected: a JSON array of game objects, each with nested `home_team`/`away_team` objects, `home_score`/`away_score` as integers, `status: "final"`.

Also open `http://localhost:8000/docs` in a browser and confirm all four `/nba/*` routes appear with their request/response shapes — this is the FastAPI auto-generated docs UI, worth getting comfortable with per the original spec.

- [ ] **Step 6: Commit**

```bash
git add backend/schemas.py backend/routers/__init__.py backend/routers/nba.py backend/main.py
git commit -m "Add /nba routes with Pydantic response schemas"
```

---

### Task 8: Scheduled background sync via lifespan

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `SessionLocal` from `database.py`; `nba_service.sync_teams`/`sync_games` (Tasks 5, 6)
- Produces: a running `AsyncIOScheduler` job that calls `sync_teams`/`sync_games` every 15 minutes while the app is up.

- [ ] **Step 1: Confirm whether `@app.on_event` is deprecated/removed on the installed FastAPI version**

```bash
docker compose exec backend python -c "import fastapi; print(fastapi.__version__)"
docker compose exec backend python -c "from fastapi import FastAPI; import warnings; warnings.simplefilter('error'); FastAPI().on_event('startup')"
```
If the second command raises a `DeprecationWarning`-turned-error (expected), that confirms `lifespan` is the correct choice — proceed to Step 2 regardless of the result, since `lifespan` is the current non-deprecated pattern either way.

- [ ] **Step 2: Rewrite `backend/main.py` to use `lifespan`**

Replace the full file with:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os

from database import SessionLocal
from services import nba_service
from routers import nba

scheduler = AsyncIOScheduler()


async def scheduled_sync():
    db = SessionLocal()
    try:
        await nba_service.sync_teams(db)
        await nba_service.sync_games(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(scheduled_sync, "interval", minutes=15)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(nba.router)

@app.get("/health")
def health_check():
    return {"status": "ok", "environment": os.getenv("ENVIRONMENT", "local")}

@app.get("/")
def root():
    return {"message": "Sports Dashboard API"}
```

- [ ] **Step 3: Verify the app starts cleanly with the scheduler registered**

```bash
docker compose restart backend
docker compose logs backend --tail 30
```
Expected: normal FastAPI/uvicorn startup log lines, `Application startup complete`, and no exceptions or tracebacks. There's no practical way to observe a 15-minute interval firing within this session — if you want to confirm it fires later, leave the stack running and check `docker compose logs backend` after 15+ minutes for a second sync happening without you calling the endpoint manually.

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "Wire 15-minute background sync via FastAPI lifespan"
```

---

### Task 9: Frontend test infrastructure (Vitest + React Testing Library)

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/vitest.setup.ts`
- Create: `frontend/hooks/__smoke__.test.ts` (deleted again at the end of this task — it only exists to prove the runner works before Task 10 depends on it)
- Modify: `.github/workflows/ci.yml` (add a frontend test step)

**Interfaces:**
- Produces: `npm test` runs Vitest once (CI-friendly, non-watch mode) inside `frontend/`.

- [ ] **Step 1: Install the test dependencies**

```powershell
cd frontend
npm install --save-dev vitest @vitejs/plugin-react jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
cd ..
```

- [ ] **Step 2: Write `frontend/vitest.config.ts`**

```typescript
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
});
```

- [ ] **Step 3: Write `frontend/vitest.setup.ts`**

```typescript
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 4: Add the `test` script to `frontend/package.json`**

Current `scripts` block:
```json
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint"
  },
```

Replace with:
```json
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint",
    "test": "vitest run"
  },
```

- [ ] **Step 5: Write a throwaway smoke test to prove the runner works**

Create `frontend/hooks/__smoke__.test.ts`:
```typescript
import { describe, it, expect } from "vitest";

describe("vitest smoke test", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 6: Run it**

```powershell
cd frontend
npm test
cd ..
```
Expected: `1 passed`

- [ ] **Step 7: Delete the smoke test**

```powershell
Remove-Item frontend\hooks\__smoke__.test.ts
```

- [ ] **Step 8: Add a frontend test step to CI**

In `.github/workflows/ci.yml`, in the `frontend` job, find:
```yaml
      # Step 5: Lint
      - name: Lint
        working-directory: frontend
        run: npm run lint
```
Add immediately after it:
```yaml

      # Step 6: Unit test the custom hooks
      - name: Test
        working-directory: frontend
        run: npm test
```

- [ ] **Step 9: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/vitest.setup.ts .github/workflows/ci.yml
git commit -m "Add Vitest + React Testing Library for frontend hook tests"
```

---

### Task 10: `useFetch` hook (with abort-cleanup fix) + tests

**Files:**
- Create: `frontend/hooks/useFetch.ts`
- Create: `frontend/hooks/useFetch.test.ts`

**Interfaces:**
- Produces: `useFetch<T>(url: string): { data: T | null; loading: boolean; error: string | null; refetch: () => void }`

- [ ] **Step 1: Write the failing tests**

Create `frontend/hooks/useFetch.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useFetch } from "./useFetch";

describe("useFetch", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns data on a successful fetch", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ hello: "world" }),
    }) as unknown as typeof fetch;

    const { result } = renderHook(() => useFetch<{ hello: string }>("/api/test"));

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ hello: "world" });
    expect(result.current.error).toBeNull();
  });

  it("sets an error message on a non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    }) as unknown as typeof fetch;

    const { result } = renderHook(() => useFetch<unknown>("/api/test"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("HTTP 500");
    expect(result.current.data).toBeNull();
  });

  it("aborts the in-flight request when the component unmounts", async () => {
    let capturedSignal: AbortSignal | undefined;
    global.fetch = vi.fn((_url: RequestInfo | URL, opts?: RequestInit) => {
      capturedSignal = opts?.signal ?? undefined;
      return new Promise(() => {}); // never resolves -- simulates an in-flight request
    }) as unknown as typeof fetch;

    const { unmount } = renderHook(() => useFetch<unknown>("/api/test"));
    unmount();

    expect(capturedSignal?.aborted).toBe(true);
  });

  it("refetch() triggers a new request", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ count: 1 }),
    }) as unknown as typeof fetch;

    const { result } = renderHook(() => useFetch<{ count: number }>("/api/test"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    result.current.refetch();

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```powershell
cd frontend
npm test -- useFetch
cd ..
```
Expected: fails with a module-not-found error for `./useFetch`.

- [ ] **Step 3: Write `frontend/hooks/useFetch.ts`**

```typescript
import { useState, useEffect, useCallback } from "react";

interface UseFetchState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useFetch<T>(url: string): UseFetchState<T> & { refetch: () => void } {
  const [state, setState] = useState<UseFetchState<T>>({
    data: null,
    loading: true,
    error: null,
  });
  const [refetchToken, setRefetchToken] = useState(0);

  const refetch = useCallback(() => setRefetchToken((t) => t + 1), []);

  useEffect(() => {
    // Created synchronously, before the async work starts, so cleanup
    // can actually cancel an in-flight request on unmount or URL change.
    const controller = new AbortController();

    setState((prev) => ({ ...prev, loading: true, error: null }));

    fetch(url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((json: T) => setState({ data: json, loading: false, error: null }))
      .catch((err) => {
        if (err instanceof Error && err.name !== "AbortError") {
          setState({ data: null, loading: false, error: err.message });
        }
      });

    return () => controller.abort();
  }, [url, refetchToken]);

  return { ...state, refetch };
}
```

- [ ] **Step 4: Run to verify it passes**

```powershell
cd frontend
npm test -- useFetch
cd ..
```
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add frontend/hooks/useFetch.ts frontend/hooks/useFetch.test.ts
git commit -m "Add useFetch hook with working abort-on-unmount cleanup"
```

---

### Task 11: `usePolling` hook + tests

**Files:**
- Create: `frontend/hooks/usePolling.ts`
- Create: `frontend/hooks/usePolling.test.ts`

**Interfaces:**
- Produces: `usePolling(callback: () => void, intervalMs: number, enabled?: boolean): void`

- [ ] **Step 1: Write the failing tests**

Create `frontend/hooks/usePolling.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { usePolling } from "./usePolling";

describe("usePolling", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("calls the callback repeatedly at the given interval", () => {
    const callback = vi.fn();
    renderHook(() => usePolling(callback, 1000));

    expect(callback).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(callback).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(2000);
    expect(callback).toHaveBeenCalledTimes(3);
  });

  it("does not poll when enabled is false", () => {
    const callback = vi.fn();
    renderHook(() => usePolling(callback, 1000, false));

    vi.advanceTimersByTime(5000);
    expect(callback).not.toHaveBeenCalled();
  });

  it("always calls the latest callback, not a stale closure", () => {
    let renderedCallback = vi.fn();
    const { rerender } = renderHook(
      ({ cb }) => usePolling(cb, 1000),
      { initialProps: { cb: renderedCallback } }
    );

    const newCallback = vi.fn();
    rerender({ cb: newCallback });

    vi.advanceTimersByTime(1000);
    expect(newCallback).toHaveBeenCalledTimes(1);
    expect(renderedCallback).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```powershell
cd frontend
npm test -- usePolling
cd ..
```
Expected: module-not-found error.

- [ ] **Step 3: Write `frontend/hooks/usePolling.ts`**

```typescript
import { useEffect, useRef } from "react";

export function usePolling(callback: () => void, intervalMs: number, enabled = true) {
  const savedCallback = useRef(callback);

  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled) return;

    const tick = () => savedCallback.current();
    const id = setInterval(tick, intervalMs);

    return () => clearInterval(id);
  }, [intervalMs, enabled]);
}
```

- [ ] **Step 4: Run to verify it passes**

```powershell
cd frontend
npm test -- usePolling
cd ..
```
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add frontend/hooks/usePolling.ts frontend/hooks/usePolling.test.ts
git commit -m "Add usePolling hook"
```

---

### Task 12: `useDebounce` and `useLocalStorage` hooks + tests

**Files:**
- Create: `frontend/hooks/useDebounce.ts`
- Create: `frontend/hooks/useDebounce.test.ts`
- Create: `frontend/hooks/useLocalStorage.ts`
- Create: `frontend/hooks/useLocalStorage.test.ts`

**Interfaces:**
- Produces: `useDebounce<T>(value: T, delayMs: number): T`, `useLocalStorage<T>(key: string, initialValue: T): [T, (value: T) => void]`

- [ ] **Step 1: Write the failing tests**

Create `frontend/hooks/useDebounce.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach, act } from "vitest";
import { renderHook } from "@testing-library/react";
import { useDebounce } from "./useDebounce";

describe("useDebounce", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("only updates after the delay elapses with no further changes", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 400),
      { initialProps: { value: "a" } }
    );

    expect(result.current).toBe("a");

    rerender({ value: "ab" });
    act(() => vi.advanceTimersByTime(200));
    expect(result.current).toBe("a");

    rerender({ value: "abc" });
    act(() => vi.advanceTimersByTime(200));
    expect(result.current).toBe("a"); // reset by the second change before 400ms elapsed

    act(() => vi.advanceTimersByTime(400));
    expect(result.current).toBe("abc");
  });
});
```

Create `frontend/hooks/useLocalStorage.test.ts`:

```typescript
import { describe, it, expect, beforeEach, act } from "vitest";
import { renderHook } from "@testing-library/react";
import { useLocalStorage } from "./useLocalStorage";

describe("useLocalStorage", () => {
  beforeEach(() => window.localStorage.clear());

  it("initializes from an existing localStorage value", () => {
    window.localStorage.setItem("count", JSON.stringify(5));
    const { result } = renderHook(() => useLocalStorage("count", 0));
    expect(result.current[0]).toBe(5);
  });

  it("falls back to the initial value when nothing is stored", () => {
    const { result } = renderHook(() => useLocalStorage("missing-key", 42));
    expect(result.current[0]).toBe(42);
  });

  it("persists updates to localStorage", () => {
    const { result } = renderHook(() => useLocalStorage("count", 0));
    act(() => result.current[1](10));
    expect(JSON.parse(window.localStorage.getItem("count")!)).toBe(10);
  });
});
```

- [ ] **Step 2: Run to verify both fail**

```powershell
cd frontend
npm test -- useDebounce useLocalStorage
cd ..
```
Expected: module-not-found errors for both.

- [ ] **Step 3: Write `frontend/hooks/useDebounce.ts`**

```typescript
import { useState, useEffect } from "react";

export function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
```

- [ ] **Step 4: Write `frontend/hooks/useLocalStorage.ts`**

```typescript
import { useState, useEffect } from "react";

export function useLocalStorage<T>(key: string, initialValue: T) {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") return initialValue;
    try {
      const stored = window.localStorage.getItem(key);
      return stored ? JSON.parse(stored) : initialValue;
    } catch {
      return initialValue;
    }
  });

  useEffect(() => {
    window.localStorage.setItem(key, JSON.stringify(value));
  }, [key, value]);

  return [value, setValue] as const;
}
```

- [ ] **Step 5: Run to verify both pass**

```powershell
cd frontend
npm test -- useDebounce useLocalStorage
cd ..
```
Expected: `1 passed` (useDebounce) and `3 passed` (useLocalStorage).

- [ ] **Step 6: Commit**

```bash
git add frontend/hooks/useDebounce.ts frontend/hooks/useDebounce.test.ts frontend/hooks/useLocalStorage.ts frontend/hooks/useLocalStorage.test.ts
git commit -m "Add useDebounce and useLocalStorage hooks"
```

---

### Task 13: Dashboard page + GameCard with score animation

**Files:**
- Create: `frontend/components/GameCard.tsx`
- Modify: `frontend/app/page.tsx`
- Modify: `sports-dashboard/.env` and `docker-compose.yaml` are already correct (`NEXT_PUBLIC_API_URL` set in Stage 1) — no change needed, just confirming the dependency.

**Interfaces:**
- Consumes: `useFetch` (Task 10), `usePolling` (Task 11), `GET /nba/games` (Task 7)
- Produces: the dashboard's `GameCard` component, reused by later tasks (favorites button gets added to it in Task 16).

No automated test for this task — verified manually per the Global Constraints section.

- [ ] **Step 1: Install Framer Motion**

```powershell
cd frontend
npm install framer-motion
cd ..
```

(`recharts`, mentioned in the original spec, isn't used by anything built this stage — skipping it per YAGNI; add it when an actual chart is needed.)

- [ ] **Step 2: Write `frontend/components/GameCard.tsx`**

```typescript
"use client";

import { motion, AnimatePresence } from "framer-motion";

interface Team {
  id: number;
  name: string;
  logo_url: string | null;
}

interface Game {
  id: number;
  home_team: Team;
  away_team: Team;
  home_score: number | null;
  away_score: number | null;
  status: string;
}

function TeamRow({ team, score }: { team: Team; score: number | null }) {
  return (
    <div className="flex justify-between items-center">
      <span>{team.name}</span>
      <AnimatePresence mode="popLayout">
        <motion.span
          key={score}
          initial={{ y: -10, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 10, opacity: 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
          className="text-2xl font-bold"
        >
          {score ?? "-"}
        </motion.span>
      </AnimatePresence>
    </div>
  );
}

export function GameCard({ game }: { game: Game }) {
  return (
    <div className="border rounded-lg p-4 bg-gray-900 text-white">
      <TeamRow team={game.away_team} score={game.away_score} />
      <TeamRow team={game.home_team} score={game.home_score} />
      <span className="text-xs text-gray-400 uppercase">{game.status}</span>
    </div>
  );
}
```

- [ ] **Step 3: Replace `frontend/app/page.tsx`**

```typescript
"use client";

import { useFetch } from "@/hooks/useFetch";
import { usePolling } from "@/hooks/usePolling";
import { GameCard } from "@/components/GameCard";

interface Team {
  id: number;
  name: string;
  logo_url: string | null;
}

interface Game {
  id: number;
  home_team: Team;
  away_team: Team;
  home_score: number | null;
  away_score: number | null;
  status: string;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL;

export default function Dashboard() {
  const { data: games, loading, error, refetch } = useFetch<Game[]>(`${API_URL}/nba/games`);

  usePolling(refetch, 30000);

  if (loading) return <div className="p-8">Loading games...</div>;
  if (error) return <div className="p-8">Error: {error}</div>;

  return (
    <main className="p-8 grid grid-cols-1 md:grid-cols-3 gap-4">
      {games?.map((game) => (
        <GameCard key={game.id} game={game} />
      ))}
    </main>
  );
}
```

- [ ] **Step 4: Manual verification**

```bash
docker compose exec backend curl -s -X POST http://localhost:8000/nba/sync/teams
docker compose exec backend curl -s -X POST "http://localhost:8000/nba/sync/games?date=20260115"
```

Open `http://localhost:3000` in a browser. Expected: a grid of game cards, each showing two team names and their final scores from the January 15, 2026 slate, with the score numbers visible (no "-" placeholders, since these are `STATUS_FINAL` games).

To see the spring animation fire, manually change a score in Postgres and wait for the next 30-second poll:
```bash
docker compose exec db psql -U sports_user -d sports_dashboard -c "UPDATE games SET home_score = 200 WHERE external_id = '401810433';"
```
Watch the browser tab — within 30 seconds, that game's home score should pop in with the spring animation.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/GameCard.tsx frontend/app/page.tsx frontend/package.json frontend/package-lock.json
git commit -m "Add dashboard with polling GameCard grid and score animation"
```

---

### Task 14: Team search with debounce

**Files:**
- Create: `frontend/components/TeamSearch.tsx`
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes: `useDebounce` (Task 12), `useFetch` (Task 10), `GET /nba/teams?q=` (Task 7)

No automated test — manual verification per Global Constraints.

- [ ] **Step 1: Write `frontend/components/TeamSearch.tsx`**

```typescript
"use client";

import { useState } from "react";
import { useDebounce } from "@/hooks/useDebounce";
import { useFetch } from "@/hooks/useFetch";

interface Team {
  id: number;
  name: string;
  abbreviation: string | null;
  logo_url: string | null;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL;

export function TeamSearch() {
  const [input, setInput] = useState("");
  const debouncedInput = useDebounce(input, 400);

  const { data: results, loading } = useFetch<Team[]>(
    `${API_URL}/nba/teams${debouncedInput ? `?q=${encodeURIComponent(debouncedInput)}` : ""}`
  );

  return (
    <div className="p-4">
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder="Search teams..."
        className="border rounded px-3 py-2 text-black"
      />
      {debouncedInput && (
        <ul className="mt-2">
          {loading && <li>Searching...</li>}
          {results?.map((team) => (
            <li key={team.id}>{team.name} ({team.abbreviation})</li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add it to `frontend/app/page.tsx`**

Add the import:
```typescript
import { TeamSearch } from "@/components/TeamSearch";
```

Add `<TeamSearch />` right after the opening `<main ...>` tag, before the `{games?.map(...)}` line, so the return block reads:
```typescript
  return (
    <main className="p-8 grid grid-cols-1 md:grid-cols-3 gap-4">
      <TeamSearch />
      {games?.map((game) => (
        <GameCard key={game.id} game={game} />
      ))}
    </main>
  );
```

- [ ] **Step 3: Manual verification**

Open `http://localhost:3000`. Open the browser's Network tab, filter to `/nba/teams`. Type "Lak" quickly (all three keystrokes within under 400ms). Expected: **one** request fires, for `?q=Lak`, about 400ms after the last keystroke — not one request per keystroke. Confirm the result list shows "Los Angeles Lakers".

- [ ] **Step 4: Commit**

```bash
git add frontend/components/TeamSearch.tsx frontend/app/page.tsx
git commit -m "Add debounced team search"
```

---

### Task 15: Favorites Phase 1 — optimistic UI teaching exercise

**Files:**
- Modify: `backend/routers/nba.py`
- Create: `frontend/components/FavoriteButton.tsx`
- Modify: `frontend/components/GameCard.tsx`

**Interfaces:**
- Produces (temporary, replaced in Task 16): `POST /nba/favorite/{team_id}` returning `{"status": "favorited", "team_id": ...}` on success, HTTP 500 on ~20% of calls.

- [ ] **Step 1: Add the fake-failure endpoint to `backend/routers/nba.py`**

Add this import at the top:
```python
import random
from fastapi import HTTPException
```
(combine with the existing `from fastapi import APIRouter, Depends` line: `from fastapi import APIRouter, Depends, HTTPException`)

Append to the file:
```python
@router.post("/favorite/{team_id}")
async def favorite_team(team_id: str):
    """TEMPORARY -- replaced with real persistence in the next task.
    Exists only to let the optimistic-UI rollback path be observed firsthand."""
    if random.random() < 0.2:
        raise HTTPException(status_code=500, detail="Simulated failure")
    return {"status": "favorited", "team_id": team_id}
```

- [ ] **Step 2: Write `frontend/components/FavoriteButton.tsx`**

```typescript
"use client";

import { useState } from "react";
import { motion } from "framer-motion";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

export function FavoriteButton({ teamId, initialFavorited }: { teamId: string; initialFavorited: boolean }) {
  const [favorited, setFavorited] = useState(initialFavorited);
  const [error, setError] = useState(false);

  async function handleClick() {
    const previous = favorited;

    setFavorited(!previous);
    setError(false);

    try {
      const response = await fetch(`${API_URL}/nba/favorite/${teamId}`, { method: "POST" });
      if (!response.ok) throw new Error("Failed to save favorite");
    } catch {
      setFavorited(previous);
      setError(true);
      setTimeout(() => setError(false), 2000);
    }
  }

  return (
    <motion.button
      onClick={handleClick}
      whileTap={{ scale: 0.85 }}
      animate={error ? { x: [-4, 4, -4, 4, 0] } : {}}
      transition={{ duration: 0.3 }}
      className={`text-2xl ${favorited ? "text-yellow-400" : "text-gray-400"}`}
    >
      {favorited ? "★" : "☆"}
    </motion.button>
  );
}
```

- [ ] **Step 3: Wire it into `GameCard.tsx`**

Add the import:
```typescript
import { FavoriteButton } from "./FavoriteButton";
```

Modify the `TeamRow` function to accept and render a favorite button:
```typescript
function TeamRow({ team, score }: { team: Team; score: number | null }) {
  return (
    <div className="flex justify-between items-center">
      <div className="flex items-center gap-2">
        <FavoriteButton teamId={String(team.id)} initialFavorited={false} />
        <span>{team.name}</span>
      </div>
      <AnimatePresence mode="popLayout">
        <motion.span
          key={score}
          initial={{ y: -10, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 10, opacity: 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
          className="text-2xl font-bold"
        >
          {score ?? "-"}
        </motion.span>
      </AnimatePresence>
    </div>
  );
}
```

- [ ] **Step 4: Manual verification**

Open `http://localhost:3000`. Click a star icon ~15-20 times in a row (backend picks up the code change automatically via the bind mount + `--reload`, no rebuild needed). Expected: the star flips color instantly on every click (optimistic update), and roughly 1 in 5 clicks reverts back with a visible left-right shake within a couple hundred milliseconds (the simulated failure + rollback).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/nba.py frontend/components/FavoriteButton.tsx frontend/components/GameCard.tsx
git commit -m "Add favorite button with optimistic update and simulated-failure rollback demo"
```

---

### Task 16: Favorites Phase 2 — real persistence

**Files:**
- Modify: `backend/models.py`
- Create: `backend/alembic/versions/<hash>_create_favorites_table.py` (autogenerated)
- Modify: `backend/schemas.py`
- Modify: `backend/routers/nba.py`
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/components/GameCard.tsx`
- Modify: `frontend/components/FavoriteButton.tsx`

**Interfaces:**
- Produces: `POST /nba/favorite/{team_id}` now toggles real persistence (no more fake failure), `GET /nba/favorites` returning `list[FavoriteOut]`.

- [ ] **Step 1: Add the `Favorite` model**

Append to `backend/models.py`:
```python
class Favorite(Base):
    __tablename__ = "favorites"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    team = relationship("Team")
```

- [ ] **Step 2: Generate and apply the migration**

```bash
docker compose exec backend alembic revision --autogenerate -m "create favorites table"
docker compose exec backend alembic upgrade head
```

Review the generated file (same as Task 3, Step 4) to confirm it only adds the `favorites` table, then verify:
```bash
docker compose exec db psql -U sports_user -d sports_dashboard -c "\dt"
```
Expected: `favorites` now appears alongside `teams`, `games`, `alembic_version`.

- [ ] **Step 3: Add `FavoriteOut` to `backend/schemas.py`**

Append:
```python
class FavoriteOut(BaseModel):
    team_id: int

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Replace the fake-failure endpoint in `backend/routers/nba.py`**

Remove the `import random` line and the `favorite_team` function added in Task 15, Step 1. Also remove `HTTPException` from the fastapi import if nothing else uses it (nothing else does — revert that import line back to `from fastapi import APIRouter, Depends`).

Add this import:
```python
from models import Team, Game, Favorite
from schemas import TeamOut, GameOut, FavoriteOut
```
(replacing the existing `from models import Team, Game` and `from schemas import TeamOut, GameOut` lines)

Append the new real endpoints:
```python
@router.post("/favorite/{team_id}")
def toggle_favorite(team_id: int, db: Session = Depends(get_db)):
    existing = db.query(Favorite).filter(Favorite.team_id == team_id).first()
    if existing:
        db.delete(existing)
        db.commit()
        return {"status": "unfavorited", "team_id": team_id}
    db.add(Favorite(team_id=team_id))
    db.commit()
    return {"status": "favorited", "team_id": team_id}


@router.get("/favorites", response_model=list[FavoriteOut])
def list_favorites(db: Session = Depends(get_db)):
    return db.query(Favorite).all()
```

- [ ] **Step 5: Manual backend verification**

```bash
docker compose exec backend curl -s -X POST http://localhost:8000/nba/favorite/1
docker compose exec backend curl -s http://localhost:8000/nba/favorites
docker compose exec backend curl -s -X POST http://localhost:8000/nba/favorite/1
docker compose exec backend curl -s http://localhost:8000/nba/favorites
```
Expected: first `favorites` call returns `[{"team_id":1}]`, second returns `[]` (toggled off).

- [ ] **Step 6: Update `FavoriteButton.tsx` — remove the "temporary" framing, keep the same rollback mechanics**

Since the endpoint no longer fails randomly, the rollback path won't fire under normal conditions anymore — that's expected, it already did its job in Task 15. No code change needed here; the component's try/catch still correctly handles a real network failure if one ever happens. Just remove the comment that called the old endpoint "temporary" if one was added — there wasn't one in this component, so this step is a no-op confirmation.

- [ ] **Step 7: Hydrate initial favorited state in `frontend/app/page.tsx`**

Add a `Favorite` interface and a second `useFetch` call. Replace the full file with:

```typescript
"use client";

import { useFetch } from "@/hooks/useFetch";
import { usePolling } from "@/hooks/usePolling";
import { GameCard } from "@/components/GameCard";
import { TeamSearch } from "@/components/TeamSearch";

interface Team {
  id: number;
  name: string;
  logo_url: string | null;
}

interface Game {
  id: number;
  home_team: Team;
  away_team: Team;
  home_score: number | null;
  away_score: number | null;
  status: string;
}

interface Favorite {
  team_id: number;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL;

export default function Dashboard() {
  const { data: games, loading, error, refetch } = useFetch<Game[]>(`${API_URL}/nba/games`);
  const { data: favorites } = useFetch<Favorite[]>(`${API_URL}/nba/favorites`);

  usePolling(refetch, 30000);

  if (loading) return <div className="p-8">Loading games...</div>;
  if (error) return <div className="p-8">Error: {error}</div>;

  const favoritedTeamIds = new Set(favorites?.map((f) => f.team_id) ?? []);

  return (
    <main className="p-8 grid grid-cols-1 md:grid-cols-3 gap-4">
      <TeamSearch />
      {games?.map((game) => (
        <GameCard key={game.id} game={game} favoritedTeamIds={favoritedTeamIds} />
      ))}
    </main>
  );
}
```

- [ ] **Step 8: Thread `favoritedTeamIds` through `GameCard.tsx` and `FavoriteButton.tsx`**

Replace `frontend/components/GameCard.tsx` in full:
```typescript
"use client";

import { motion, AnimatePresence } from "framer-motion";
import { FavoriteButton } from "./FavoriteButton";

interface Team {
  id: number;
  name: string;
  logo_url: string | null;
}

interface Game {
  id: number;
  home_team: Team;
  away_team: Team;
  home_score: number | null;
  away_score: number | null;
  status: string;
}

function TeamRow({ team, score, favorited }: { team: Team; score: number | null; favorited: boolean }) {
  return (
    <div className="flex justify-between items-center">
      <div className="flex items-center gap-2">
        <FavoriteButton teamId={String(team.id)} initialFavorited={favorited} />
        <span>{team.name}</span>
      </div>
      <AnimatePresence mode="popLayout">
        <motion.span
          key={score}
          initial={{ y: -10, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 10, opacity: 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
          className="text-2xl font-bold"
        >
          {score ?? "-"}
        </motion.span>
      </AnimatePresence>
    </div>
  );
}

export function GameCard({ game, favoritedTeamIds }: { game: Game; favoritedTeamIds: Set<number> }) {
  return (
    <div className="border rounded-lg p-4 bg-gray-900 text-white">
      <TeamRow team={game.away_team} score={game.away_score} favorited={favoritedTeamIds.has(game.away_team.id)} />
      <TeamRow team={game.home_team} score={game.home_score} favorited={favoritedTeamIds.has(game.home_team.id)} />
      <span className="text-xs text-gray-400 uppercase">{game.status}</span>
    </div>
  );
}
```

`FavoriteButton.tsx` itself needs no change — it already accepts `initialFavorited` as a prop; it just now receives a real value instead of a hardcoded `false`.

- [ ] **Step 9: Manual end-to-end verification**

Open `http://localhost:3000`. Click a star to favorite a team. Reload the page (full browser refresh, not a poll). Expected: the star is still gold after reload — confirming the favorite persisted server-side and the dashboard correctly hydrates it via `GET /nba/favorites`, closing the gap the original spec left open.

- [ ] **Step 10: Commit**

```bash
git add backend/models.py backend/alembic/versions/ backend/schemas.py backend/routers/nba.py frontend/app/page.tsx frontend/components/GameCard.tsx
git commit -m "Replace fake-failure favorite endpoint with real persistence and dashboard hydration"
```

---

## Self-review notes

- **Spec coverage:** every checklist item from the design doc's "What to verify by end of Stage 3" maps to a task: teams sync (Task 5/7), games sync (Task 6/7), DB-not-proxy responses (Task 7's `response_model`), dashboard polling (Task 13), debounced search (Task 14), favorites phase 1 rollback (Task 15), favorites phase 2 persistence (Task 16), score animation (Task 13), Alembic migrations as real files (Tasks 3, 16).
- **Placeholder scan:** no TBD/TODO markers; every code block is complete, runnable code, not a description of code.
- **Type consistency:** `Game`/`Team`/`Favorite` interfaces on the frontend match the backend `GameOut`/`TeamOut`/`FavoriteOut` Pydantic field names exactly (`home_team`, `away_team`, `home_score`, `away_score`, `logo_url`, `team_id`) — checked task-by-task while writing rather than after the fact, since a mismatch here is exactly the kind of bug that only shows up at runtime.
