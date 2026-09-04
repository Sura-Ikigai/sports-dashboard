"""Shared Postgres support for the tests that need a real database.

Roles, grants, JSONB, `ON CONFLICT` and `DROP ROLE` have no SQLite equivalent, so `conftest.py`'s
in-memory session cannot host them. D-044 provisions the CI service container for exactly this.

## The skip guard is asymmetric on purpose

A test that silently skips in CI is not a test, and this project has paid for that lesson twice
(F-037, F-091). So:

  - locally, with no Postgres configured, these **skip** with a message saying how to get one;
  - under CI, an unreachable Postgres **fails**. It never degrades to green.

`CI` is set to "true" by GitHub Actions on every runner, which is what makes the asymmetry work
without a project-specific flag anyone has to remember to set.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]


def derive_url(base: str, **overrides: str) -> str:
    """A connection URL with fields swapped out, password intact.

    `str(URL)` renders the password as `***` -- a sensible default for logs, and a silent
    authentication failure when the result is fed back to `create_engine`, which is exactly what
    happened the first time these fixtures ran. `render_as_string(hide_password=False)` is the only
    form safe to reconnect with.
    """
    return make_url(base).set(**overrides).render_as_string(hide_password=False)


def require_postgres() -> str:
    """The Postgres these suites may create scratch databases on -- or a skip, or a failure."""
    url = os.getenv("DATABASE_URL", "")
    in_ci = os.getenv("CI", "").lower() == "true"

    if not url or not url.startswith("postgresql"):
        message = (
            "this suite requires Postgres: DATABASE_URL is unset or is not a postgresql:// URL. "
            "Locally: `docker compose up -d db` and export DATABASE_URL."
        )
        if in_ci:
            pytest.fail(
                f"{message} Under CI this is a failure, not a skip -- D-044 provisions a service "
                "container precisely so these run, and a silently skipped integrity test would "
                "report the corpus as verified without ever having checked (F-037, F-091)."
            )
        pytest.skip(message)

    try:
        sa.create_engine(url).connect().close()
    except Exception as exc:  # noqa: BLE001 -- any connection failure means the same thing here
        message = f"Postgres at DATABASE_URL is unreachable: {exc}"
        if in_ci:
            pytest.fail(f"{message} Under CI this is a failure, not a skip (F-037, F-091).")
        pytest.skip(message)

    return url


@pytest.fixture(scope="session")
def pg_admin_url() -> str:
    return require_postgres()


def make_scratch_database(admin_url: str, name: str) -> Iterator[str]:
    """Create a throwaway database, yield its URL, drop it afterwards.

    CREATE/DROP DATABASE cannot run inside a transaction block, hence AUTOCOMMIT.
    """
    maintenance = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maintenance.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))

    yield derive_url(admin_url, database=name)

    with maintenance.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    maintenance.dispose()


def alembic_config(url: str):
    """Alembic pointed at `url`.

    `alembic/env.py` reads DATABASE_URL from the environment and overrides whatever the Config
    carries, so setting the env var is not belt-and-braces -- it is the only setting that takes
    effect. Both are set so a future env.py that stops doing that still works.
    """
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    os.environ["DATABASE_URL"] = url
    return config
