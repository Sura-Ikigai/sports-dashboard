"""Verified loader-to-Postgres ingest (T-023).

This is the boundary where verification moves from files to a database (**D-046**). Everything
downstream of it -- `store` (T-024), `features` (T-028), the estimator, the reported numbers --
trusts these tables without re-deriving them from source bytes. So the contract here is narrow and
absolute: **refuse rather than repair.**

## What is verified, and where

Nothing in this module re-implements verification. It cannot: the content hashes are over *source
bytes*, and by the time rows exist those bytes are gone. The ordering is what makes that safe --

    loader.load_season / load_box          <- size cap, framing/header, SHA-256 over exact bytes,
        |                                     pinned per-season counts, game_id uniqueness
        v
    corpus.partition_exhibitions           <- the franchise invariant (D-046's third check)
        |
        v
    one transaction, upsert                <- this module

-- so **every byte-level and count-level check has already run and raised before a single row is
written.** A hash mismatch does not "abort partway"; it aborts before the first INSERT, because the
loader raises while the file is still a file. The transaction below covers the remaining failure
mode: a database error midway through a multi-table write.

## Idempotence (D-045)

Every write is an upsert keyed the way T-021's schema is keyed -- `venue_id`, `game_id`,
`(game_id, player_id)`, `(game_id, team_id)`. Running twice yields one row per game, and a re-run
after a corrected upstream file **repairs** the affected rows rather than duplicating them. That is
the one sense in which this module repairs anything, and it is deliberate: the alternative is an
operator hand-deleting rows before every re-ingest, which is how partial states are created.

## What lands here, and what does not

The **verified** set lands -- exactly the games the pinned counts describe, All-Star exhibitions
included. Curation is applied on *read* (`store`, T-024), not on write, for a reason worth stating:
the pinned counts are the tripwire, and they count 6,615 games for 2022-2026, not the curated 6,605.
Writing the curated set would make the database's own row count disagree with the number that
verifies it, and the tripwire would stop being checkable against the table. The franchise invariant
is still asserted here (D-046) -- exhibitions are *identified* at ingest and *excluded* at read.

Warm-up seasons (D-037) land in the same table, distinguished by `season`. They carry no box scores;
`BOX_SEASONS` is the modeling window only.

## Roles (D-047)

Meant to run as `sports_ingest`, which holds write on the corpus tables and nothing on
`predictions`. This module does not choose its own credentials -- it uses the connection it is
given, so the deployment decides. The grant split is what makes that safe rather than trusting.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import sqlalchemy as sa

from . import corpus, dataset, loader

# Rows per executemany batch. Large enough that 175k player rows do not become 175k round trips,
# small enough that a batch's parameter list stays well inside the driver's limits.
_BATCH_ROWS = 2_000

# Player-box rows the corpus structurally cannot represent: `athlete_id` is null, so there is no
# player to key the row to. Found by running this ingest against the real files on 2026-09-04 --
# 33 rows in 2026 and none in any other season, all on one team, all with a null display name too,
# all with null minutes, all captioned "COACH'S DECISION". They carry no player and no playing time,
# so there is nothing in them for T-027's availability to read.
#
# **Dropping a row is a repair, and this module's contract is to refuse rather than repair.** The
# reconciliation is that the repair is *pinned*: the exact number of unrepresentable rows is
# declared per season, and a season that produces a different number is refused outright. So the
# known 33 pass silently while a 34th -- or a new one in 2024 -- stops the ingest. That is the same
# discipline as EXPECTED_COMPLETED_COUNTS: a known quantity is not the same as an unbounded licence.
#
# A season absent from this dict is not assumed clean; `_identifiable_players` refuses it, exactly
# as `_verify_season` refuses a season with no pinned count.
#
# Note this deliberately does NOT change PLAYER_BOX_EXPECTED_ROWS. That pin verifies the *file* as
# published, and it should keep counting every row the file contains; this pin governs what the
# *corpus* can hold. Two different questions, two different numbers.
UNIDENTIFIED_PLAYER_ROWS: dict[int, int] = {
    2022: 0,
    2023: 0,
    2024: 0,
    2025: 0,
    2026: 33,
}


class IngestError(RuntimeError):
    """Raised when ingest refuses to write -- an unpinned season, a broken invariant, a shape the
    corpus tables cannot represent. Never raised to mean "wrote something questionable"."""


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What an ingest run did, per table. Returned rather than printed so tests can assert on it and
    the CLI can format it."""

    venues: int = 0
    games: int = 0
    player_box: int = 0
    team_box: int = 0
    seasons: tuple[int, ...] = ()
    box_seasons: tuple[int, ...] = ()
    exhibitions_identified: int = 0

    def total_rows(self) -> int:
        return self.venues + self.games + self.player_box + self.team_box


@dataclass
class _Tables:
    """The corpus tables, reflected from the live database.

    Reflected rather than re-declared. T-021 deliberately kept these out of `Base.metadata` so a
    test could never `create_all` a corpus table that no migration produced; re-declaring their
    columns here would reintroduce exactly that -- a second source of truth able to drift from the
    migration silently. Reflection fails loudly instead, on a database that has not been migrated.
    """

    metadata: sa.MetaData = field(default_factory=sa.MetaData)

    def load(self, conn: sa.Connection) -> None:
        names = ("corpus_venues", "corpus_games", "corpus_player_box", "corpus_team_box")
        # Checked before reflecting, not after: `reflect(only=...)` raises its own
        # `InvalidRequestError` on a missing table, which reads as a SQLAlchemy bug rather than as
        # "this database has not been migrated". The instruction is the useful part of the failure.
        present = set(sa.inspect(conn).get_table_names())
        missing = [n for n in names if n not in present]
        if missing:
            raise IngestError(
                f"corpus table(s) {missing} are not present -- run `alembic upgrade head` first. "
                "Migrations own schema; this command owns data (D-045)."
            )
        self.metadata.reflect(bind=conn, only=names)

    def __getitem__(self, name: str) -> sa.Table:
        return self.metadata.tables[name]


def _none_if_nan(value: Any) -> Any:
    """pandas uses NaN for "absent" in float columns; Postgres wants NULL.

    Written as an explicit conversion rather than relying on the driver, because the two disagree in
    a way that matters here: a NaN reaching a `double precision` column stores the float NaN, which
    is not NULL, compares unequal to itself, and would make `minutes IS NULL` -- the query T-027's
    availability rests on -- silently return nothing.
    """
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if value is pd.NaT:
        return None
    return value


def _int_or_none(value: Any) -> int | None:
    value = _none_if_nan(value)
    return None if value is None else int(value)


def _str_or_none(value: Any) -> str | None:
    """Box-score ids arrive as int32 and schedule ids as str; the corpus keys them as text.

    Normalizing here rather than in the schema is deliberate -- everything is ESPN-keyed and must
    stay that way, so `401584689` from a parquet file and `"401584689"` from a CSV have to become
    the same key or the box scores silently join to nothing.
    """
    value = _none_if_nan(value)
    return None if value is None else str(value)


def _upsert(
    conn: sa.Connection, table: sa.Table, rows: Sequence[dict], *, key: Sequence[str]
) -> int:
    """Insert `rows`, updating on conflict against `key`. Returns the number of rows submitted.

    The update set deliberately covers every non-key column: a re-ingest after a corrected upstream
    file must leave the row matching the new source, not a merge of both.
    """
    if not rows:
        return 0

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    written = 0
    for start in range(0, len(rows), _BATCH_ROWS):
        batch = rows[start : start + _BATCH_ROWS]
        stmt = pg_insert(table).values(batch)
        updatable = [c.name for c in table.columns if c.name not in key and not c.primary_key]
        stmt = stmt.on_conflict_do_update(
            index_elements=list(key),
            set_={name: getattr(stmt.excluded, name) for name in updatable},
        )
        conn.execute(stmt)
        written += len(batch)
    return written


def _venue_rows(frame: pd.DataFrame) -> list[dict]:
    """One row per distinct venue in the frame.

    De-duplicated on `venue_id` keeping the last occurrence, so a venue renamed mid-corpus (arenas
    change sponsors constantly) lands with its most recent name rather than whichever row happened
    to sort first.
    """
    venues = frame.dropna(subset=["venue_id"]).drop_duplicates(subset=["venue_id"], keep="last")
    return [
        {
            "venue_id": _str_or_none(row.venue_id),
            "full_name": _str_or_none(row.venue_name) or "unknown",
            "city": _str_or_none(row.venue_city) or "unknown",
            "state": _str_or_none(row.venue_state),
            "indoor": bool(row.venue_indoor),
        }
        for row in venues.itertuples(index=False)
    ]


def _game_rows(frame: pd.DataFrame) -> list[dict]:
    return [
        {
            "game_id": _str_or_none(row.game_id),
            "season": int(row.season),
            "season_type": int(row.season_type),
            "game_date": row.date.to_pydatetime(),
            "home_id": _str_or_none(row.home_id),
            "away_id": _str_or_none(row.away_id),
            "home_score": int(row.home_score),
            "away_score": int(row.away_score),
            "neutral_site": bool(row.neutral_site),
            "venue_id": _str_or_none(row.venue_id),
        }
        for row in frame.itertuples(index=False)
    ]


def _identifiable_players(frame: pd.DataFrame, season: int) -> pd.DataFrame:
    """Drop player rows with no `athlete_id`, refusing unless the count is exactly what is pinned.

    See `UNIDENTIFIED_PLAYER_ROWS`. The assertion is the point: without it this function is a silent
    filter that would absorb an upstream regression of any size, and the corpus would quietly hold
    fewer players than the source published with nothing to notice it.
    """
    if season not in UNIDENTIFIED_PLAYER_ROWS:
        raise IngestError(
            f"player_box season {season} has no pinned unrepresentable-row count in "
            "UNIDENTIFIED_PLAYER_ROWS -- refusing to drop rows from an unexamined season. Count "
            "its null athlete_id rows by hand and pin the number deliberately first."
        )

    identifiable = frame[frame["athlete_id"].notna()]
    dropped = len(frame) - len(identifiable)
    expected = UNIDENTIFIED_PLAYER_ROWS[season]
    if dropped != expected:
        raise IngestError(
            f"player_box {season}: {dropped} rows have a null athlete_id, but "
            f"UNIDENTIFIED_PLAYER_ROWS pins {expected}. A row with no player cannot be keyed into "
            "corpus_player_box, and the number of them is not allowed to drift unnoticed -- "
            "re-examine the source and update the pin deliberately, in a reviewed commit."
        )
    return identifiable


def _player_box_rows(frame: pd.DataFrame) -> list[dict]:
    """`minutes` and `did_not_play` are carried separately and never collapsed.

    User story 12: a player who logged zero minutes in a blowout is not a player who was absent.
    Verified on the real 2024 file -- 6,638 rows are `did_not_play` with null minutes, 268 are
    active with `minutes == 0`. Storage records what the source said; T-027 decides what counts.
    """
    return [
        {
            "game_id": _str_or_none(row.game_id),
            "team_id": _str_or_none(row.team_id),
            "player_id": _str_or_none(row.athlete_id),
            "minutes": _none_if_nan(row.minutes),
            "started": bool(row.starter),
            "did_not_play": bool(row.did_not_play),
            "points": _int_or_none(row.points),
        }
        for row in frame.itertuples(index=False)
    ]


def _team_box_rows(frame: pd.DataFrame) -> list[dict]:
    return [
        {
            "game_id": _str_or_none(row.game_id),
            "team_id": _str_or_none(row.team_id),
            "is_home": str(row.team_home_away) == "home",
            "points": _int_or_none(row.team_score),
        }
        for row in frame.itertuples(index=False)
    ]


def _assert_franchise_invariant(frame: pd.DataFrame) -> int:
    """D-046's third check: every season carries exactly 30 franchises. Returns the exhibition count.

    Delegates identification to `corpus.partition_exhibitions` rather than re-deriving it, because
    that module already encodes the two things that make the rule correct: it is keyed by
    `(season, team)` so a team below the threshold in one season is not stripped from all of them
    (F-065), and it classifies a game as an exhibition if *either* side is not a franchise, rather
    than only when both are.

    This identifies exhibitions; it does not remove them. They are written, and excluded on read --
    see the module docstring on why the pinned counts require that.
    """
    games = dataset.games_from_frame(frame)
    nba_games, exhibitions = corpus.partition_exhibitions(games)

    by_season: dict[int, set[str]] = {}
    for game in nba_games:
        by_season.setdefault(game.season, set()).update((game.home_id, game.away_id))

    wrong = {
        season: len(teams)
        for season, teams in by_season.items()
        if len(teams) != corpus.NBA_TEAMS_PER_SEASON
    }
    if wrong:
        raise IngestError(
            f"franchise invariant violated: season(s) {wrong} do not carry exactly "
            f"{corpus.NBA_TEAMS_PER_SEASON} franchises once exhibitions are excluded. This is "
            "D-046's third integrity check; refusing to write a corpus whose team population is "
            "not what the model assumes."
        )
    return len(exhibitions)


def ingest_seasons(
    conn: sa.Connection,
    seasons: Iterable[int],
    *,
    box_seasons: Iterable[int] | None = None,
    data_dir: Path = loader.DEFAULT_DATA_DIR,
    force: bool = False,
) -> IngestReport:
    """Load, verify and upsert the requested seasons into the corpus tables.

    Runs inside the caller's transaction. `ingest()` supplies one covering the whole run so a
    failure on the last table does not leave the first three populated.
    """
    seasons = tuple(seasons)
    if not seasons:
        raise IngestError("no seasons requested -- refusing a no-op that would report success")

    unpinned = [s for s in seasons if s not in loader.SCHEDULE_SEASONS]
    if unpinned:
        raise IngestError(
            f"season(s) {unpinned} are not pinned in loader.SCHEDULE_SEASONS -- refusing to ingest "
            "an unverified season. Pin its count and content hash deliberately first."
        )

    if box_seasons is None:
        box_seasons = tuple(s for s in seasons if s in loader.BOX_SEASONS)
    else:
        box_seasons = tuple(box_seasons)
        not_box = [s for s in box_seasons if s not in loader.BOX_SEASONS]
        if not_box:
            raise IngestError(
                f"season(s) {not_box} have no pinned box-score assets (BOX_SEASONS is "
                f"{loader.BOX_SEASONS}) -- refusing to ingest box scores for them"
            )

    tables = _Tables()
    tables.load(conn)

    # Every loader call below verifies bytes and counts and raises before returning. Loading all
    # schedules first means a failure on season five happens before season one has been written.
    frames = [loader.load_season(season, data_dir, force=force) for season in seasons]
    combined = pd.concat(frames, ignore_index=True)
    exhibitions = _assert_franchise_invariant(combined)

    venues = _upsert(conn, tables["corpus_venues"], _venue_rows(combined), key=["venue_id"])
    games = _upsert(conn, tables["corpus_games"], _game_rows(combined), key=["game_id"])

    player_rows = 0
    team_rows = 0
    for season in box_seasons:
        player_frame = loader.load_box("player_box", season, force=force)
        team_frame = loader.load_box("team_box", season, force=force)
        player_rows += _upsert(
            conn,
            tables["corpus_player_box"],
            _player_box_rows(_identifiable_players(player_frame, season)),
            key=["game_id", "player_id"],
        )
        team_rows += _upsert(
            conn,
            tables["corpus_team_box"],
            _team_box_rows(team_frame),
            key=["game_id", "team_id"],
        )

    return IngestReport(
        venues=venues,
        games=games,
        player_box=player_rows,
        team_box=team_rows,
        seasons=seasons,
        box_seasons=box_seasons,
        exhibitions_identified=exhibitions,
    )


def ingest(
    database_url: str,
    seasons: Iterable[int] = loader.SCHEDULE_SEASONS,
    *,
    box_seasons: Iterable[int] | None = None,
    data_dir: Path = loader.DEFAULT_DATA_DIR,
    force: bool = False,
) -> IngestReport:
    """Ingest into the database at `database_url`, in **one transaction**.

    The transaction is what covers the failure mode verification cannot: the byte and count checks
    have all raised before the first INSERT, but a constraint violation on the fourth table would
    otherwise leave the first three populated -- a partial corpus that every count check would
    happily pass, because each table would be internally consistent.
    """
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as conn:
            return ingest_seasons(
                conn, seasons, box_seasons=box_seasons, data_dir=data_dir, force=force
            )
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Ingest the verified corpus into Postgres (T-023).")
    parser.add_argument(
        "--seasons",
        type=int,
        nargs="*",
        default=list(loader.SCHEDULE_SEASONS),
        help="seasons to ingest (default: every pinned schedule season, warm-up included)",
    )
    parser.add_argument(
        "--force", action="store_true", help="re-download sources even if a valid cache exists"
    )
    args = parser.parse_args(argv)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set -- refusing to guess a database to write to.")
        return 2

    report = ingest(database_url, args.seasons, force=args.force)
    print(f"Ingested seasons {report.seasons} (box: {report.box_seasons or 'none'})")
    print(f"  corpus_venues     {report.venues:>7}")
    print(f"  corpus_games      {report.games:>7}")
    print(f"  corpus_player_box {report.player_box:>7}")
    print(f"  corpus_team_box   {report.team_box:>7}")
    print(f"  exhibitions identified (written, excluded on read): {report.exhibitions_identified}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
