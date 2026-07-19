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
