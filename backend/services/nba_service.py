import datetime

from sqlalchemy.orm import Session

from models import Game, Team
from services import espn_client


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
            existing.last_synced = datetime.datetime.now(datetime.UTC)
        else:
            db.add(Game(
                external_id=event["id"],
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                home_score=home_score,
                away_score=away_score,
                status=status,
                game_time=game_time,
                last_synced=datetime.datetime.now(datetime.UTC),
            ))

    db.commit()
