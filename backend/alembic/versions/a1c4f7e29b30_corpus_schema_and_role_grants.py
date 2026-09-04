"""corpus schema and role grants (T-021)

Revision ID: a1c4f7e29b30
Revises: 505b6f1088ee
Create Date: 2026-09-04

Creates the empty tables the historical corpus lands in (D-038), the append-only predictions
table, and the role split that keeps the API away from training data (D-047).

## Why schema and grants ship in ONE migration

Splitting them would leave a revision at which the corpus tables exist with default ownership and
no grant split -- a window where the authorization boundary this migration exists to create is
absent from a database that already has the tables to protect. Schema and its authorization land
and leave together.

## What this migration deliberately does NOT do

- **It inserts no rows.** Migrations own schema; `model.ingest` owns data (D-045). A data load
  inside a migration has no coherent `downgrade()`, makes CI pay to populate a corpus on every
  run, and puts regenerable derived data into an audit trail meant for irreversible structural
  change. `test_corpus_schema.py` asserts every corpus table is empty at head.
- **It defines no SQLAlchemy ORM models.** The corpus is read by `model.store` (T-024) with SQL
  that returns the record types the pipeline already uses. Keeping these tables out of
  `Base.metadata` means `conftest.py`'s `create_all` can never conjure a corpus table in SQLite
  and let a test pass against a table shape no migration ever produced.
- **It does not change the application's connection string.** The roles below are NOLOGIN group
  roles. Pointing the API at `sports_api` is a deployment step (grant it to the login user that
  the API connects as); this migration only establishes what that role may do.

## Roles

| role            | corpus tables      | predictions    | app tables (teams/games/favorites) |
|-----------------|--------------------|----------------|------------------------------------|
| `sports_api`    | SELECT             | SELECT         | SELECT/INSERT/UPDATE/DELETE        |
| `sports_ingest` | SELECT/INSERT/UPDATE/DELETE | --    | --                                 |
| `sports_job`    | SELECT             | SELECT, INSERT | --                                 |

`sports_job` holding INSERT but not UPDATE or DELETE is what makes `predictions` append-only
(user story 38). That is a grant, not a trigger and not RLS -- consistent with D-047.

This is grants, not row-level security. Postgres still enforces nothing about *which* rows a
caller may read; there is no authorization boundary in this application (F-001), and model
outputs are public by decision.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c4f7e29b30'
down_revision: str | Sequence[str] | None = '505b6f1088ee'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The four tables the historical corpus lands in. `sports_api` gets SELECT on these and nothing
# more -- that restriction is the whole point of D-047, so the list is named once and reused by
# both the grant block and the downgrade's revoke block.
CORPUS_TABLES: tuple[str, ...] = (
    "corpus_venues",
    "corpus_games",
    "corpus_player_box",
    "corpus_team_box",
)

# Application tables that already exist and that the API writes. Listed so `sports_api` is a
# usable role the day someone points the API at it, rather than a role that can read the corpus
# and nothing else.
APP_TABLES: tuple[str, ...] = ("teams", "games", "favorites")

ROLES: tuple[str, ...] = ("sports_api", "sports_ingest", "sports_job")


def _create_roles() -> None:
    """Create the three NOLOGIN group roles, idempotently.

    Roles are cluster-scoped, not database-scoped, so a second database in the same cluster (or a
    re-run after a partial downgrade) can find them already present. Postgres has no
    `CREATE ROLE IF NOT EXISTS`, hence the catalog check.

    No passwords and no LOGIN: a login user is granted these roles at deploy time. A password in a
    migration file is a password in git.
    """
    for role in ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} NOLOGIN;
                END IF;
            END
            $$;
            """
        )


def upgrade() -> None:
    """Upgrade schema."""
    # ── VENUES ────────────────────────────────────────────────────────────────
    # What the source said about a venue. The *geography* -- coordinates, elevation, timezone --
    # lives in the `venues` deep module (T-026) as a static table, not here: it is reference data
    # that does not come from the corpus and must not be silently re-derivable per ingest. This
    # table is the join from a game to a city; the module turns a city into distance and altitude.
    op.create_table(
        "corpus_venues",
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("city", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("indoor", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("venue_id"),
    )

    # ── GAMES ─────────────────────────────────────────────────────────────────
    # Mirrors `model.features.Game` field-for-field, plus the venue and season_type the frame
    # carries. Keyed by ESPN game id: the app's `teams.external_id` and this share one id space,
    # and keeping it that way is why no translation layer exists (F-005).
    #
    # Warm-up seasons (2016-2019, D-037) live in this same table and are distinguished by `season`
    # alone. No `is_warmup` column: the season IS the fact, and a derived flag would be a second
    # source of truth that could disagree with it. T-029 enforces that a warm-up season never
    # becomes a training row.
    #
    # Scores are NOT NULL because only completed games are ingested -- upcoming games live in the
    # application's `games` table, synced from ESPN. The tie CHECK encodes the same invariant
    # `Game.__post_init__` enforces (F-049): an NBA game cannot end tied, so a tie is corrupt
    # input, not a drawable result, and the storage layer refuses it rather than letting it
    # quietly bias every form feature the team appears in.
    op.create_table(
        "corpus_games",
        sa.Column("game_id", sa.String(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("season_type", sa.Integer(), nullable=False),
        sa.Column("game_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_id", sa.String(), nullable=False),
        sa.Column("away_id", sa.String(), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=False),
        sa.Column("away_score", sa.Integer(), nullable=False),
        sa.Column("neutral_site", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("venue_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["venue_id"], ["corpus_venues.venue_id"]),
        sa.PrimaryKeyConstraint("game_id"),
        sa.CheckConstraint("home_id <> away_id", name="ck_corpus_games_distinct_teams"),
        sa.CheckConstraint("home_score <> away_score", name="ck_corpus_games_no_tie"),
    )
    # D-039: SQL narrows by season or by team, never by as-of. These are the indexes that make
    # those two narrowings cheap -- and there is deliberately no index on `game_date` alone,
    # because the only reason to want one is the as-of predicate this project forbids in SQL.
    op.create_index("ix_corpus_games_season", "corpus_games", ["season"])
    op.create_index("ix_corpus_games_home_id", "corpus_games", ["season", "home_id"])
    op.create_index("ix_corpus_games_away_id", "corpus_games", ["season", "away_id"])

    # ── PLAYER PARTICIPATION ──────────────────────────────────────────────────
    # The input to `availability` (T-027, D-035). `minutes` is nullable and `did_not_play` is
    # explicit because the two are different facts and the distinction is exactly what user story
    # 12 turns on: a player who logged zero minutes in a blowout is not a player who was absent.
    # Storage records what the source said; the deep module decides what counts as absence.
    #
    # UNIQUE (game_id, player_id) is T-023's idempotence key -- running ingest twice yields one
    # row per player per game.
    op.create_table(
        "corpus_player_box",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.String(), nullable=False),
        sa.Column("team_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("minutes", sa.Float(), nullable=True),
        sa.Column("started", sa.Boolean(), nullable=True),
        sa.Column("did_not_play", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["corpus_games.game_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "player_id", name="uq_corpus_player_box_game_player"),
        sa.CheckConstraint("minutes IS NULL OR minutes >= 0", name="ck_corpus_player_box_minutes"),
    )
    # Availability is a per-team lookback over prior games, so it reads by team-within-game and by
    # player across games. Both directions get an index.
    op.create_index("ix_corpus_player_box_team", "corpus_player_box", ["team_id", "game_id"])
    op.create_index("ix_corpus_player_box_player", "corpus_player_box", ["player_id", "game_id"])

    # ── TEAM BOX ──────────────────────────────────────────────────────────────
    # UNIQUE (game_id, team_id) is the idempotence key, same as above.
    op.create_table(
        "corpus_team_box",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.String(), nullable=False),
        sa.Column("team_id", sa.String(), nullable=False),
        sa.Column("is_home", sa.Boolean(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["corpus_games.game_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "team_id", name="uq_corpus_team_box_game_team"),
    )
    op.create_index("ix_corpus_team_box_team", "corpus_team_box", ["team_id", "game_id"])

    # ── PREDICTIONS ───────────────────────────────────────────────────────────
    # An append-only audit trail (user story 38), keyed by game, model version and as-of moment.
    # Appended daily over a seven-day horizon; accuracy is scored against the last prediction made
    # before tip-off, which is what the (game_id, as_of DESC) index serves.
    #
    # Deliberately NOT a corpus table and deliberately WITHOUT a foreign key to `games`: a
    # prediction is about an upcoming game in the application's ESPN-synced table, and an audit
    # record must survive the row it describes being resynced or removed. A cascade here would
    # let a routine sync erase the model's track record.
    #
    # `features` and `contributions` are JSONB rather than columns-per-feature because the feature
    # SET is what T-030 freezes; pinning it into DDL would mean a migration every time selection
    # moves during T-028, and the model_version column is what makes a row interpretable anyway.
    op.create_table(
        "predictions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_win_prob", sa.Float(), nullable=False),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.Column("contributions", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id", "model_version", "as_of", name="uq_predictions_game_version_asof"
        ),
        sa.CheckConstraint(
            "home_win_prob >= 0 AND home_win_prob <= 1", name="ck_predictions_prob_range"
        ),
    )
    op.execute(
        "CREATE INDEX ix_predictions_game_as_of ON predictions (game_id, as_of DESC)"
    )

    # ── ROLES AND GRANTS ──────────────────────────────────────────────────────
    # The grant split is the only thing standing between an unauthenticated write endpoint and the
    # training corpus. `test_corpus_schema.py` connects AS each role and proves the refusals --
    # a grant that is never exercised is a grant that is assumed.
    _create_roles()

    all_tables = CORPUS_TABLES + ("predictions",)
    for table in all_tables:
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")

    for table in CORPUS_TABLES:
        op.execute(f"GRANT SELECT ON TABLE {table} TO sports_api")
        op.execute(f"GRANT SELECT ON TABLE {table} TO sports_job")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO sports_ingest")

    op.execute("GRANT SELECT ON TABLE predictions TO sports_api")
    # INSERT but not UPDATE and not DELETE. This is what "append-only" means here.
    op.execute("GRANT SELECT, INSERT ON TABLE predictions TO sports_job")

    # Sequence usage for the three tables with generated ids, granted only to roles that INSERT.
    op.execute("GRANT USAGE, SELECT ON SEQUENCE corpus_player_box_id_seq TO sports_ingest")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE corpus_team_box_id_seq TO sports_ingest")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE predictions_id_seq TO sports_job")

    for table in APP_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO sports_api")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE teams_id_seq TO sports_api")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE games_id_seq TO sports_api")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE favorites_id_seq TO sports_api")


def downgrade() -> None:
    """Downgrade schema.

    Order matters twice over. Tables go first (children before parents, so the foreign keys do not
    block), which also disposes of every grant made against them. Then the grants on the
    pre-existing application tables must be revoked explicitly -- those tables survive this
    downgrade, so their grants would survive too and `DROP ROLE` would fail on the dependency.
    """
    op.drop_table("predictions")
    op.drop_table("corpus_team_box")
    op.drop_table("corpus_player_box")
    op.drop_table("corpus_games")
    op.drop_table("corpus_venues")

    for table in APP_TABLES:
        op.execute(f"REVOKE ALL ON TABLE {table} FROM sports_api")
    op.execute("REVOKE ALL ON SEQUENCE teams_id_seq FROM sports_api")
    op.execute("REVOKE ALL ON SEQUENCE games_id_seq FROM sports_api")
    op.execute("REVOKE ALL ON SEQUENCE favorites_id_seq FROM sports_api")

    # Roles are CLUSTER-scoped, not database-scoped, so one of these can still hold privileges
    # granted outside this migration -- most commonly another database in the same cluster that is
    # also at `head`. Postgres refuses `DROP ROLE` in that case, and it is right to: the role is in
    # use, and destroying it would break the other database's API user.
    #
    # A bare `DROP ROLE IF EXISTS` therefore makes `downgrade` fail outright in any cluster hosting
    # two migrated databases -- which is not hypothetical, it is what the test suite does, and it is
    # how this was found. Neither available extreme is acceptable: aborting means the migration is
    # not reversible, and forcing the drop means one database's downgrade silently breaks another.
    #
    # So: drop the role when nothing depends on it, and RAISE NOTICE when something does. Not
    # silent, not destructive, and `downgrade` completes either way. What this migration is actually
    # responsible for -- this database's tables and this database's grants -- is gone regardless,
    # which is what the round-trip test asserts.
    for role in ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                DROP ROLE IF EXISTS {role};
            EXCEPTION
                WHEN dependent_objects_still_exist OR insufficient_privilege THEN
                    RAISE NOTICE 'role {role} retained: it still holds privileges elsewhere in '
                                 'this cluster, or this session may not drop it. This database''s '
                                 'grants have been revoked.';
            END
            $$;
            """
        )
