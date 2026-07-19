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
