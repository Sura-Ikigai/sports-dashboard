import pytest
from unittest.mock import AsyncMock, patch
from models import Team, Game


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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_sync_games_scheduled_game_has_null_scores_and_scheduled_status(db_session):
    from services import nba_service

    _seed_two_teams(db_session)

    with patch("services.espn_client.get_games", new=AsyncMock(return_value=[RAW_GAME_SCHEDULED])):
        await nba_service.sync_games(db_session)

    game = db_session.query(Game).filter(Game.external_id == "401810999").first()
    assert game is not None
    assert game.status == "scheduled"


@pytest.mark.asyncio
async def test_sync_games_skips_game_whose_teams_are_not_synced_yet(db_session):
    from services import nba_service

    # No teams seeded this time.
    with patch("services.espn_client.get_games", new=AsyncMock(return_value=[RAW_GAME_FINAL])):
        await nba_service.sync_games(db_session)

    assert db_session.query(Game).count() == 0


@pytest.mark.asyncio
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
