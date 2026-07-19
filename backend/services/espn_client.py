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
