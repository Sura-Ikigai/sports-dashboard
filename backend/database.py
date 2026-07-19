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
