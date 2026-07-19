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
