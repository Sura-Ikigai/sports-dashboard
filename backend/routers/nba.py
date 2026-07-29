from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import Favorite, Game, Team
from schemas import FavoriteOut, GameOut, TeamOut
from services import nba_service

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
