"""T-021 -- the corpus schema migration and, more importantly, the role split it establishes.

D-047 makes the grant split the only thing standing between an unauthenticated write endpoint and
the training corpus. There is no RLS and no auth in this application (F-001), so "the API role is
SELECT-only on corpus tables" is not a statement about rows -- it is a statement about privileges,
and privileges that are never exercised are privileges that are assumed. Every test below that
claims a refusal **connects as the role in question** and reads the SQLSTATE back.

## Why these tests need a real Postgres, and why they must not skip in CI

Roles, grants, JSONB and `DROP ROLE` have no SQLite equivalent, so `conftest.py`'s in-memory
session cannot host them (D-044 provisions the CI service container for exactly this). But a test
that silently skips in CI is not a test -- this project has paid for that lesson twice already
(F-037, F-091). So the guard below is asymmetric on purpose:

  - locally, with no Postgres configured, these skip with a message telling you how to get one;
  - under CI, an unreachable Postgres **fails**. It never degrades to green.

## Scratch database, and why there is only one

The migration is run against a throwaway database rather than the application's. Roles, however,
are cluster-scoped -- a `downgrade` that drops them affects every database in the cluster, not just
the scratch one. Rather than pretend otherwise with per-test databases that would silently pull the
roles out from under each other, there is one scratch database at `head`, and the single test that
exercises `downgrade` restores it before yielding. That also buys a property worth having: the
migration is proven re-runnable after a downgrade, which is what exercises the idempotent role
creation.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic import command
from conftest import alembic_config, derive_url, make_scratch_database

# Tables the corpus lands in. Mirrors the migration's own list; duplicated deliberately, because a
# test that imported the constant from the code under test could not catch the list being wrong.
CORPUS_TABLES = ("corpus_venues", "corpus_games", "corpus_player_box", "corpus_team_box")
ALL_NEW_TABLES = (*CORPUS_TABLES, "predictions")

# One real, writable column per corpus table. An UPDATE naming a column that does not exist fails
# with `42703` before Postgres ever consults the grant, which looks exactly like a refusal and is
# not one -- see `test_api_role_cannot_write_the_corpus`.
CORPUS_TABLE_COLUMNS: dict[str, str] = {
    "corpus_venues": "city",
    "corpus_games": "season",
    "corpus_player_box": "minutes",
    "corpus_team_box": "points",
}
ROLES = ("sports_api", "sports_ingest", "sports_job")

# Postgres SQLSTATE for insufficient_privilege. Asserted by code rather than by message text so the
# tests do not break on a Postgres wording change.
INSUFFICIENT_PRIVILEGE = "42501"

SCRATCH_DB = "t021_corpus_schema_test"

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _sqlstate(exc: Exception) -> str | None:
    """Pull the SQLSTATE off a SQLAlchemy-wrapped psycopg2 error, if there is one."""
    orig = getattr(exc, "orig", None)
    return getattr(orig, "pgcode", None)


@pytest.fixture(scope="session")
def scratch_url(pg_admin_url: str) -> Iterator[str]:
    """A throwaway database for this suite, dropped when the session ends.

    The skip-or-fail guard, the password-preserving URL helper and the create/drop machinery all
    live in `conftest.py` and are shared with `test_ingest.py`. Two copies of a safety guard is two
    guards that can drift, and the whole point of this one is that it must not quietly stop firing.
    """
    yield from make_scratch_database(pg_admin_url, SCRATCH_DB)


@pytest.fixture(scope="session")
def migrated(scratch_url: str) -> Iterator[str]:
    """The scratch database at `head`. Torn down by a full downgrade, which also drops the roles."""
    previous = os.getenv("DATABASE_URL")
    command.upgrade(alembic_config(scratch_url), "head")

    yield scratch_url

    # `alembic_config` is re-called here rather than reusing the Config built at setup, and that is
    # load-bearing: `alembic/env.py` overrides the Config's URL with `os.environ["DATABASE_URL"]`
    # *at run time*, so a Config object does not actually pin its target. Between setup and teardown
    # another session-scoped fixture (`test_ingest.py`'s) legitimately points that variable at its
    # own scratch database -- and by teardown time has already dropped it, so this downgrade went
    # looking for a database that no longer existed. Re-establishing the variable immediately before
    # the call is what makes the target unambiguous.
    command.downgrade(alembic_config(scratch_url), "base")
    if previous is not None:
        os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def role_logins(migrated: str) -> Iterator[dict[str, str]]:
    """A throwaway LOGIN user per role, so a connection can actually be made *as* that role.

    The migration creates NOLOGIN group roles on purpose -- a login role with a password in a
    migration is a password in git. Granting each group role to a scratch login user is how
    deployment is meant to wire this up, so testing through that path tests the real mechanism.
    Passwords are generated per run and never leave this process.
    """
    admin = sa.create_engine(migrated, isolation_level="AUTOCOMMIT")
    urls: dict[str, str] = {}
    logins = {role: f"{role}_login_test" for role in ROLES}

    with admin.connect() as conn:
        for role, login in logins.items():
            password = secrets.token_hex(16)
            conn.execute(sa.text(f'DROP ROLE IF EXISTS "{login}"'))
            conn.execute(sa.text(f"CREATE ROLE \"{login}\" LOGIN PASSWORD '{password}'"))
            conn.execute(sa.text(f'GRANT "{role}" TO "{login}"'))
            urls[role] = derive_url(migrated, username=login, password=password)

    yield urls

    with admin.connect() as conn:
        for login in logins.values():
            conn.execute(sa.text(f'DROP ROLE IF EXISTS "{login}"'))
    admin.dispose()


@pytest.fixture
def as_role(role_logins: dict[str, str]):
    """Open a connection authenticated as one of the three roles."""
    engines: list[sa.Engine] = []

    def _connect(role: str):
        engine = sa.create_engine(role_logins[role])
        engines.append(engine)
        return engine.connect()

    yield _connect

    for engine in engines:
        engine.dispose()


# ── SCHEMA ────────────────────────────────────────────────────────────────────


def test_migration_creates_every_table_the_plan_names(migrated: str) -> None:
    """T-021 acceptance: historical games, player participation, team box, venues, predictions."""
    engine = sa.create_engine(migrated)
    tables = set(sa.inspect(engine).get_table_names())
    engine.dispose()

    assert set(ALL_NEW_TABLES) <= tables, f"missing: {set(ALL_NEW_TABLES) - tables}"


def test_no_migration_inserts_corpus_rows(migrated: str) -> None:
    """D-045: migrations own schema, `model.ingest` owns data.

    The tables must be empty at `head`. A migration that seeded even one row would make the
    migration chain a data-loading path -- with no coherent `downgrade()`, and with CI paying to
    populate a corpus on every run.
    """
    engine = sa.create_engine(migrated)
    with engine.connect() as conn:
        counts = {
            table: conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in ALL_NEW_TABLES
        }
    engine.dispose()

    assert counts == dict.fromkeys(ALL_NEW_TABLES, 0)


def test_corpus_games_refuses_a_tie(migrated: str, as_role) -> None:
    """`Game.__post_init__` refuses a tied NBA game (F-049); the storage layer refuses it too.

    Defence in depth against the one corrupt-input shape that would otherwise pass every type check
    and quietly bias every form feature the team appears in.
    """
    with as_role("sports_ingest") as conn:
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO corpus_games (game_id, season, season_type, game_date, home_id,"
                    " away_id, home_score, away_score) VALUES ('tie', 2024, 2, now(), '1', '2',"
                    " 100, 100)"
                )
            )
        conn.rollback()


def test_corpus_games_refuses_a_team_playing_itself(migrated: str, as_role) -> None:
    with as_role("sports_ingest") as conn:
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO corpus_games (game_id, season, season_type, game_date, home_id,"
                    " away_id, home_score, away_score) VALUES ('self', 2024, 2, now(), '1', '1',"
                    " 110, 100)"
                )
            )
        conn.rollback()


def test_predictions_refuses_a_probability_outside_zero_one(migrated: str, as_role) -> None:
    with as_role("sports_job") as conn:
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO predictions (game_id, model_version, as_of, home_win_prob,"
                    " features, contributions) VALUES ('g', 'v1', now(), 1.5, '{}', '{}')"
                )
            )
        conn.rollback()


# ── THE GRANT SPLIT (D-047) ───────────────────────────────────────────────────


def test_api_role_can_read_the_corpus(migrated: str, as_role) -> None:
    with as_role("sports_api") as conn:
        for table in CORPUS_TABLES:
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


@pytest.mark.parametrize("table,column", CORPUS_TABLE_COLUMNS.items(), ids=CORPUS_TABLES)
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO {table} DEFAULT VALUES",
        "UPDATE {table} SET {column} = {column}",
        "DELETE FROM {table}",
    ],
    ids=["insert", "update", "delete"],
)
def test_api_role_cannot_write_the_corpus(
    migrated: str, as_role, table: str, column: str, statement: str
) -> None:
    """The heart of T-021's security note.

    Every write verb, against every corpus table, connected as the role the API will actually use.
    Postgres checks privileges before it evaluates constraints or scans rows, so an empty table
    still produces the refusal.

    The SQLSTATE is asserted rather than merely "it raised" because the two are not the same test,
    and this suite has already proved it: the first draft used a placeholder column name in the
    UPDATE, got `42703` (undefined_column), and would have reported the corpus as privilege-protected
    on the strength of a typo. Hence `CORPUS_TABLE_COLUMNS` -- a real, writable column per table, so
    the only thing left to refuse the statement is the grant.
    """
    with as_role("sports_api") as conn:
        with pytest.raises(sa.exc.ProgrammingError) as excinfo:
            conn.execute(sa.text(statement.format(table=table, column=column)))
        assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE
        conn.rollback()


def test_ingest_role_can_write_the_corpus(migrated: str, as_role) -> None:
    """The other half: a grant split that refused everyone would also pass the test above."""
    with as_role("sports_ingest") as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corpus_venues (venue_id, full_name, city) "
                "VALUES ('v-test', 'Test Arena', 'Denver')"
            )
        )
        assert conn.execute(
            sa.text("SELECT city FROM corpus_venues WHERE venue_id = 'v-test'")
        ).scalar_one() == "Denver"
        conn.rollback()


def test_api_role_cannot_write_predictions(migrated: str, as_role) -> None:
    """T-031: the job writes, the API reads. The API path must never acquire a write."""
    with as_role("sports_api") as conn:
        with pytest.raises(sa.exc.ProgrammingError) as excinfo:
            conn.execute(
                sa.text(
                    "INSERT INTO predictions (game_id, model_version, as_of, home_win_prob,"
                    " features, contributions) VALUES ('g', 'v1', now(), 0.5, '{}', '{}')"
                )
            )
        assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE
        conn.rollback()


def test_job_role_can_append_a_prediction(migrated: str, as_role) -> None:
    with as_role("sports_job") as conn:
        conn.execute(
            sa.text(
                "INSERT INTO predictions (game_id, model_version, as_of, home_win_prob,"
                " features, contributions)"
                " VALUES ('g-1', 'v1', now(), 0.61, '{\"elo_diff\": 42.0}', '{\"elo_diff\": 0.2}')"
            )
        )
        assert conn.execute(sa.text("SELECT count(*) FROM predictions")).scalar_one() == 1
        conn.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE predictions SET home_win_prob = 0.9",
        "DELETE FROM predictions",
    ],
    ids=["update", "delete"],
)
def test_predictions_are_append_only_for_the_job_role(
    migrated: str, as_role, statement: str
) -> None:
    """User story 38's "append-only" is a grant, not a trigger and not a convention.

    `sports_job` holds INSERT and SELECT and nothing else, so the model's track record cannot be
    revised after the fact by the process that writes it.
    """
    with as_role("sports_job") as conn:
        with pytest.raises(sa.exc.ProgrammingError) as excinfo:
            conn.execute(sa.text(statement))
        assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE
        conn.rollback()


def test_ingest_role_has_no_reach_into_predictions(migrated: str, as_role) -> None:
    """Ingest writes the corpus. It has no business in the prediction audit trail."""
    with as_role("sports_ingest") as conn:
        with pytest.raises(sa.exc.ProgrammingError) as excinfo:
            conn.execute(sa.text("SELECT count(*) FROM predictions"))
        assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE
        conn.rollback()


def test_api_role_retains_write_access_to_the_application_tables(migrated: str, as_role) -> None:
    """FastAPI is the only DB writer for the app's own tables -- restricting the corpus must not
    have cost it that."""
    with as_role("sports_api") as conn:
        conn.execute(
            sa.text(
                "INSERT INTO teams (external_id, name) VALUES ('t-test', 'Test Team')"
            )
        )
        assert conn.execute(sa.text("SELECT count(*) FROM teams")).scalar_one() == 1
        conn.rollback()


# ── ROUND TRIP ────────────────────────────────────────────────────────────────
# Defined last because it is the one test that downgrades. It restores `head` before returning --
# see the module docstring on why the roles' cluster scope makes that the honest arrangement.


def test_downgrade_then_upgrade_round_trips_on_an_empty_database(migrated: str) -> None:
    """T-021 acceptance: `upgrade head` and `downgrade` both succeed on an empty database.

    Also proves the migration is re-runnable after a downgrade, which is the only thing that
    exercises the idempotent role creation -- roles are cluster-scoped and can outlive the database
    that created them.
    """
    config = alembic_config(migrated)
    engine = sa.create_engine(migrated)

    command.downgrade(config, "base")
    tables = set(sa.inspect(engine).get_table_names())
    assert not (set(ALL_NEW_TABLES) & tables), f"downgrade left: {set(ALL_NEW_TABLES) & tables}"

    # Asserts that the downgrade revoked THIS DATABASE's grants -- not that the roles are gone from
    # the cluster.
    #
    # The distinction is not pedantry, it is the actual scope of what the migration owns. Roles are
    # cluster-scoped: another database at `head` in the same cluster legitimately holds grants for
    # the same three roles, and Postgres correctly refuses to drop a role still in use. Asserting
    # global absence made this test fail the moment a second suite (`test_ingest.py`) migrated its
    # own scratch database -- and the failure was in the assertion, not in the migration.
    with engine.connect() as conn:
        lingering = conn.execute(
            sa.text(
                "SELECT DISTINCT grantee FROM information_schema.role_table_grants "
                "WHERE grantee = ANY(:names)"
            ),
            {"names": list(ROLES)},
        ).scalars().all()
    assert lingering == [], f"downgrade left grants behind in this database: {lingering}"

    command.upgrade(config, "head")
    restored = set(sa.inspect(engine).get_table_names())
    assert set(ALL_NEW_TABLES) <= restored
    engine.dispose()
