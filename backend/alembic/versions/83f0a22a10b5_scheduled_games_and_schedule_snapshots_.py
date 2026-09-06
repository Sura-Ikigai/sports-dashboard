"""scheduled games and schedule snapshots (T-031, D-048)

Revision ID: 83f0a22a10b5
Revises: a1c4f7e29b30
Create Date: 2026-09-06

Creates the tables that hold **live** schedule data, kept deliberately apart from the verified
training corpus, plus the provenance trail D-048 requires.

## Why `scheduled_games` is a separate table and not nullable scores on `corpus_games`

`corpus_games` is the verified corpus. Its scores are `NOT NULL`, `store` and `features` rely on
that, and `corpus.assert_curated` re-checks its shape on every read. Relaxing those columns to
admit unplayed games would put rows carrying D-048's *weaker* guarantee inside the table that
carries D-046's stronger one, and nothing in a query would show which was which.

A separate table makes the trust boundary a thing you can see in the schema rather than a thing you
have to remember. A row crosses from `scheduled_games` to `corpus_games` only by being played and
then passing the verified ingest -- there is no UPDATE path that promotes it in place.

## Why postponement needs no column on `predictions`

A prediction is void exactly when its game is postponed, and `scheduled_games.status` already says
so (the source carries `status_type_name`, and a postponed row stays in the file permanently rather
than vanishing). Deriving it by join keeps one source of truth; a `void` flag on `predictions` would
be a second place for the same fact to be wrong, and `predictions` is append-only so it could never
be corrected.

`predictions.schedule_snapshot` is a genuinely new fact and does get a column: which snapshot of the
schedule the prediction was made against. Nullable, because the historical evaluation rows T-030
produced were not made against a snapshot at all.

## Roles

| role            | scheduled_games | schedule_snapshots |
|-----------------|-----------------|--------------------|
| `sports_api`    | SELECT          | SELECT             |
| `sports_ingest` | SELECT/INSERT/UPDATE/DELETE | SELECT/INSERT/UPDATE/DELETE |
| `sports_job`    | SELECT          | SELECT             |

`sports_ingest` holds UPDATE here, unlike on the corpus tables where it also does -- but the reason
differs and is worth stating. Corpus rows are updated only to be re-asserted identically (the ingest
is idempotent on game id). Scheduled rows are updated because they genuinely change: a tip-off moves,
a status flips, an NBA Cup placeholder resolves into a real matchup under the same game id. That
mutability is the whole reason D-048 exists as a separate decision.

`sports_job` gets SELECT and nothing more. The job writes predictions, never schedule rows.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '83f0a22a10b5'
down_revision: str | Sequence[str] | None = 'a1c4f7e29b30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIVE_TABLES = ("scheduled_games", "schedule_snapshots")


def upgrade() -> None:
    # ── the snapshot trail ────────────────────────────────────────────────────
    # D-048's provenance half. The bytes are not pinned in advance, so what makes a prediction
    # reproducible is knowing exactly which fetch produced the schedule it was made from. One row
    # per accepted fetch; `sha256` is over the raw file, so re-fetching an unchanged file collides
    # on the primary key and the ingest recognises "nothing moved" without a second copy.
    op.create_table(
        "schedule_snapshots",
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("scheduled_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("postponed_count", sa.Integer(), nullable=False),
        # Continuity is measured against the previous accepted snapshot, so the numbers that decision
        # was made from are stored beside it. A threshold that fired is auditable after the fact;
        # one that was recomputed from today's data would not be.
        sa.Column("added_game_ids", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_game_ids", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("sha256"),
        sa.CheckConstraint("row_count >= 0", name="ck_schedule_snapshots_row_count"),
    )
    op.execute(
        "CREATE INDEX ix_schedule_snapshots_season_fetched"
        " ON schedule_snapshots (season, fetched_at DESC)"
    )

    # ── the live schedule ─────────────────────────────────────────────────────
    op.create_table(
        "scheduled_games",
        sa.Column("game_id", sa.String(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("season_type", sa.Integer(), nullable=False),
        sa.Column("game_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_id", sa.String(), nullable=False),
        sa.Column("away_id", sa.String(), nullable=False),
        sa.Column("neutral_site", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Nullable, unlike everything else here. A venue is genuinely unknown for some rows -- the
        # NBA Cup semi-finals carry no arena until the bracket resolves -- and T-030 removed the
        # only features that read one, so a missing venue is now a display gap rather than a
        # prediction failure. It was a blocker for this task until the ablation dropped `altitude`
        # and `travel_diff`.
        sa.Column("venue_id", sa.String(), nullable=True),
        # `STATUS_SCHEDULED` | `STATUS_POSTPONED` | `STATUS_FINAL`, from the source's
        # `status_type_name`. Postponement is an explicit state here, not something inferred from a
        # row disappearing -- verified across all four postponements of the completed 2026 season.
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("game_id"),
        sa.CheckConstraint("home_id <> away_id", name="ck_scheduled_games_distinct_teams"),
        sa.ForeignKeyConstraint(
            ["snapshot_sha256"], ["schedule_snapshots.sha256"],
            name="fk_scheduled_games_snapshot",
        ),
    )
    # The job's query is "everything scheduled in the next seven days", so the index leads on date.
    op.execute(
        "CREATE INDEX ix_scheduled_games_date_status ON scheduled_games (game_date, status)"
    )

    # ── provenance on predictions ─────────────────────────────────────────────
    # Nullable: T-030's evaluation rows were not made against a snapshot at all, and backfilling a
    # value for them would be inventing one.
    op.add_column(
        "predictions",
        sa.Column("schedule_snapshot", sa.String(64), nullable=True),
    )

    # ── grants ────────────────────────────────────────────────────────────────
    # The roles already exist (a1c4f7e29b30). New tables default to no access for them, so a
    # migration that adds a table without adding its grants leaves the job unable to read it -- a
    # failure that shows up at runtime rather than here.
    for table in LIVE_TABLES:
        op.execute(f"GRANT SELECT ON TABLE {table} TO sports_api")
        op.execute(f"GRANT SELECT ON TABLE {table} TO sports_job")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO sports_ingest")


def downgrade() -> None:
    # Reverse creation order: `scheduled_games` references `schedule_snapshots`.
    op.drop_column("predictions", "schedule_snapshot")
    op.drop_table("scheduled_games")
    op.drop_table("schedule_snapshots")
