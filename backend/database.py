import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

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


def get_connection():
    """Dependency-injected raw SQLAlchemy connection, for the read-only model endpoints.

    A `Session` is the ORM's unit of work and the model package has no ORM mapping -- `model.store`
    and `model.track_record` take a `Connection` and issue their own SQL. Handing them
    `session.connection()` would work and would also enlist their reads in a transaction the ORM
    might later flush writes into, which is precisely the coupling D-047's grant split exists to
    avoid. This opens its own connection, never commits, and closes it after the request.
    """
    with engine.connect() as conn:
        yield conn
