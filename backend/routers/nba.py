import random
from fastapi import APIRouter, Depends, HTTPException
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


@router.post("/favorite/{team_id}")
async def favorite_team(team_id: str):
    """TEMPORARY -- replaced with real persistence in the next task.
    Exists only to let the optimistic-UI rollback path be observed firsthand."""
    if random.random() < 0.2:
        raise HTTPException(status_code=500, detail="Simulated failure")
    return {"status": "favorited", "team_id": team_id}
