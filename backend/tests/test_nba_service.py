import pytest
from unittest.mock import AsyncMock, patch
from models import Team


RAW_TEAMS = [
    {"id": "1", "displayName": "Atlanta Hawks", "abbreviation": "ATL",
     "logos": [{"href": "https://a.espncdn.com/i/teamlogos/nba/500/atl.png"}]},
    {"id": "2", "displayName": "Boston Celtics", "abbreviation": "BOS",
     "logos": [{"href": "https://a.espncdn.com/i/teamlogos/nba/500/bos.png"}]},
]


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_sync_teams_updates_existing_team_instead_of_duplicating(db_session):
    from services import nba_service

    db_session.add(Team(external_id="1", name="Old Name", abbreviation="OLD", source="espn"))
    db_session.commit()

    with patch("services.espn_client.get_all_teams", new=AsyncMock(return_value=RAW_TEAMS)):
        await nba_service.sync_teams(db_session)

    teams = db_session.query(Team).filter(Team.external_id == "1").all()
    assert len(teams) == 1
    assert teams[0].name == "Atlanta Hawks"
