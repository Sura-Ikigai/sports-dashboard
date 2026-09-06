"""The live schedule feed -- fetched, checked and stored under D-048 (T-031).

    PYTHONPATH=backend python -m model.schedule postgresql://.../corpus --season 2027

## Why this is not part of `ingest`

`ingest` implements D-046: content hashes over source bytes, pinned per-season counts, the franchise
invariant, and a database trusted afterwards. That works because a finished season's file never
changes again. This module implements **D-048**, which exists because the current season's file
changes nightly -- scores fill in, statuses flip, playoff rows are appended, and the NBA Cup
placeholders resolve into real matchups *under the same game ids*.

Two decisions, two guarantees, two modules, two tables. The separation is the point: a pinned hash
and a continuity check answer different questions -- *"is this exactly the data the model was fit
on"* versus *"is this a plausible, self-consistent NBA schedule"* -- and putting them in one place
would leave the weaker one wearing the stronger one's name.

## What replaces the content pin

**Structure.** Required columns, parseable dates, timezone-aware instants, team ids that are actual
franchises, and a season label matching the season requested. This catches an HTML error page, a
truncated body, and an upstream shape change -- most of what a hash caught, and it catches them by
saying what is wrong rather than "the bytes differ".

**Continuity, against the previous accepted snapshot.** The game count may grow or hold but never
shrink, and a game id we have already predicted must not vanish. This is *stronger* than the pinned
count it replaces: a total count masks a partial regression in which rows are both lost and gained,
and an id-level check does not.

**Provenance.** Every accepted snapshot is recorded with its SHA-256 and its counts, every scheduled
row records the snapshot that last confirmed it, and every prediction records the snapshot it was
made against. Reproducibility is not lost, it moves from *before* to *after*: "what did we know when
we predicted this?" is answerable exactly, which is the question a prediction actually raises.

## What is refused, and what is only counted

Refusals are for things that cannot be true of a real schedule: a shrinking game count, a predicted
game vanishing, an unknown franchise, a season label that disagrees with the request.

**Churn is counted and logged, not thresholded.** D-048 is explicit that thresholds start permissive:
there is exactly one calibration anchor -- the completed 2026 season ended with 4 postponed games out
of 1,330, about 0.3% churn across a whole season -- and nobody has watched what a normal Tuesday
looks like. A threshold built on zero observations would be a number someone made up, so the counts
go into `schedule_snapshots` where a real one can be derived from a week of them.

## Rows that are not games

Five rows of the 2027 schedule carry `home_id = -1` and `away_id = -2` with no venue: the NBA Cup
semi-finals and final, whose participants are undetermined until December. They are excluded here,
for the same reason F-042's All-Star games are excluded from the corpus -- they are not matchups, and
a prediction for "team -1 versus team -2" is not a prediction. They will arrive as ordinary rows once
the bracket resolves, under those same game ids, which is exactly the mutation D-048 was written for.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd
import sqlalchemy as sa

from . import corpus, loader

#: Placeholder team ids the source uses for undetermined NBA Cup participants.
PLACEHOLDER_TEAM_IDS: frozenset[str] = frozenset({"-1", "-2"})

# The share of rows that may be excluded as "not a real matchup" before the snapshot is refused
# outright. Excluded rows are ordinary: NBA Cup placeholders (6 of the 1,206-row 2027 file, 0.5%),
# and the All-Star game, which this source files as `season_type = 2` with phantom team ids (F-042).
# What is not ordinary is *most* of the file being unrecognisable, which is what an upstream
# id-scheme change looks like.
#
# **This check is only for the wholesale case, and that is deliberate.** A partial degradation --
# say a third of ids going unrecognisable -- would shrink the accepted row count, and
# `_check_continuity` refuses a schedule that shrinks. So there are two nets at different scales,
# and this one does not have to be the fine one. Set at half the file for that reason: the measured
# gap runs from 0.5% (a real season) to ~100% (a renamed scheme), and a threshold near the low end
# would fire on a small file with a couple of placeholders while buying nothing the continuity check
# does not already cover.
MAX_EXCLUDED_SHARE: float = 0.50

#: Statuses this module understands. Anything else is refused rather than guessed at.
KNOWN_STATUSES: frozenset[str] = frozenset(
    {"STATUS_SCHEDULED", "STATUS_POSTPONED", "STATUS_FINAL", "STATUS_IN_PROGRESS", "STATUS_CANCELED"}
)

#: Statuses a game can carry and still be worth predicting.
PREDICTABLE_STATUSES: frozenset[str] = frozenset({"STATUS_SCHEDULED"})


class ScheduleError(RuntimeError):
    """Raised when a snapshot cannot be accepted as a plausible NBA schedule."""


@dataclass(frozen=True, slots=True)
class SnapshotReport:
    """What one accepted snapshot contained and what moved since the last one."""

    sha256: str
    season: int
    row_count: int
    scheduled_count: int
    completed_count: int
    postponed_count: int
    added_game_ids: int
    changed_game_ids: int
    excluded_placeholders: int
    unchanged: bool = False
    notes: tuple[str, ...] = field(default=())

    def summary(self) -> str:
        if self.unchanged:
            return f"snapshot {self.sha256[:12]} unchanged ({self.row_count} rows)"
        return (
            f"snapshot {self.sha256[:12]}: {self.row_count} rows "
            f"({self.scheduled_count} scheduled, {self.completed_count} final, "
            f"{self.postponed_count} postponed), +{self.added_game_ids} new, "
            f"{self.changed_game_ids} changed, {self.excluded_placeholders} placeholders excluded"
        )


def _known_franchises(conn: sa.Connection) -> frozenset[str]:
    """Real franchises, by the same rule `corpus` uses: enough games in a season to be a team.

    Read from `corpus_games` rather than hardcoded -- the corpus defines what a franchise is in this
    pipeline, and a fixed list would silently stop being right the day a team relocates or the league
    expands. Identified by games played rather than by an id list for exactly the reason F-042 gives:
    next season's All-Star game will carry *fresh* phantom ids, and a hardcoded list would let them
    through with no signal.

    The bare `SELECT home_id AS t` this started as is a trap worth naming: `t` collides with a
    SQLAlchemy `Row` attribute, so `row.t` returned the **Row itself** rather than the team id, the
    resulting set held tuples, and every membership test failed. It failed loudly here only because
    the check it fed refuses on mismatch; a check that merely *warned* would have passed vacuously
    forever. Alias to a name the library does not own.
    """
    rows = conn.execute(
        sa.text(
            "SELECT team_id FROM ("
            "  SELECT home_id AS team_id, season FROM corpus_games"
            "  UNION ALL"
            "  SELECT away_id AS team_id, season FROM corpus_games"
            ") sides"
            " GROUP BY team_id, season"
            " HAVING count(*) >= :min_games"
        ),
        {"min_games": corpus.MIN_SEASON_GAMES_FOR_A_REAL_TEAM},
    ).all()
    return frozenset(row.team_id for row in rows)


def _check_structure(frame: pd.DataFrame, season: int, franchises: frozenset[str]) -> int:
    """Structural validation. Returns the number of placeholder rows excluded."""
    required = {
        "game_id", "date", "season", "season_type", "home_id", "away_id", "status",
        "home_score", "away_score",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ScheduleError(f"live schedule frame is missing column(s) {missing}")

    if frame.empty:
        raise ScheduleError(
            f"live schedule for season {season} is empty -- a real schedule is never empty, so this "
            "is a truncated download or an upstream error page that parsed"
        )

    wrong_season = sorted(set(frame["season"].unique()) - {season})
    if wrong_season:
        raise ScheduleError(
            f"live schedule for season {season} carries season label(s) {wrong_season}"
        )

    if frame["date"].isna().any():
        raise ScheduleError("live schedule contains unparseable dates")
    if getattr(frame["date"].dt, "tz", None) is None:
        raise ScheduleError(
            "live schedule dates are timezone-naive -- every instant in this pipeline is aware, and "
            "a naive one silently takes the machine's zone"
        )

    duplicates = frame["game_id"][frame["game_id"].duplicated()].unique()
    if len(duplicates):
        raise ScheduleError(
            f"live schedule repeats game_id(s) {sorted(duplicates)[:5]} -- a game id must identify "
            "one game"
        )

    unknown = sorted(
        set(frame["status"].unique()) - KNOWN_STATUSES
    )
    if unknown:
        raise ScheduleError(
            f"live schedule carries unrecognised status(es) {unknown} -- refusing to guess whether "
            f"they are predictable. Known: {sorted(KNOWN_STATUSES)}"
        )

    # Excluded rows are ordinary and are counted, not refused: NBA Cup placeholders, and the
    # All-Star game, which this source files as an ordinary regular-season row with phantom team ids.
    # Refusing them would mean the whole daily job stopping in February for a game nobody wanted a
    # prediction for. What *is* refused is most of the file being unrecognisable.
    excluded_mask = ~(frame["home_id"].isin(franchises) & frame["away_id"].isin(franchises))
    excluded = int(excluded_mask.sum())
    share = excluded / len(frame)
    if share > MAX_EXCLUDED_SHARE:
        unknown = sorted(
            (set(frame.loc[excluded_mask, "home_id"]) | set(frame.loc[excluded_mask, "away_id"]))
            - franchises
        )
        raise ScheduleError(
            f"{excluded} of {len(frame)} rows ({share:.1%}) carry team ids that are not franchises "
            f"in the corpus, over the {MAX_EXCLUDED_SHARE:.0%} limit. Unrecognised ids: "
            f"{unknown[:8]}. A handful of these is normal -- NBA Cup placeholders and the All-Star "
            "game -- but this many means the source changed its id scheme."
        )
    return excluded


def _previous_state(conn: sa.Connection, season: int) -> dict[str, tuple]:
    """`game_id` -> the fields continuity is measured on, from what the database already accepted."""
    rows = conn.execute(
        sa.text(
            "SELECT game_id, game_date, home_id, away_id, status"
            "  FROM scheduled_games WHERE season = :season"
        ),
        {"season": season},
    ).all()
    return {r.game_id: (r.game_date, r.home_id, r.away_id, r.status) for r in rows}


def _predicted_game_ids(conn: sa.Connection) -> frozenset[str]:
    """Games we have already published a prediction for.

    These are the ones whose disappearance is a hard refusal rather than churn: a prediction that
    can no longer be scored, for a game the schedule no longer admits ever existed, is a hole in the
    accuracy record with no way to explain it. A game that is *postponed* keeps its row and its
    status changes, so this only fires on an actual vanishing.
    """
    rows = conn.execute(sa.text("SELECT DISTINCT game_id FROM predictions")).all()
    return frozenset(r.game_id for r in rows)


def _check_continuity(
    frame: pd.DataFrame, previous: dict[str, tuple], predicted: frozenset[str]
) -> tuple[int, int]:
    """Compare against the previous accepted snapshot. Returns `(added, changed)`."""
    current_ids = set(frame["game_id"])
    if previous and len(current_ids) < len(previous):
        raise ScheduleError(
            f"live schedule has {len(current_ids)} games, down from {len(previous)} in the previous "
            "accepted snapshot -- a schedule does not shrink. Postponed games keep their row; this "
            "is a truncated file or an upstream regression."
        )

    vanished = sorted((set(previous) & predicted) - current_ids)
    if vanished:
        raise ScheduleError(
            f"{len(vanished)} game(s) we have already predicted are absent from this snapshot "
            f"(first few: {vanished[:5]}) -- refusing to accept a schedule that would leave those "
            "predictions unscoreable with no record of why."
        )

    added = len(current_ids - set(previous))
    changed = 0
    for row in frame.itertuples(index=False):
        before = previous.get(row.game_id)
        if before is None:
            continue
        if (before[0], before[1], before[2], before[3]) != (
            row.date.to_pydatetime(), row.home_id, row.away_id, row.status
        ):
            changed += 1
    return added, changed


def _score(value) -> int | None:
    """A score, or None for a game that has not been played. `pd.NA`/NaN both become None."""
    if value is None or pd.isna(value):
        return None
    return int(value)


def _rows_for_upsert(frame: pd.DataFrame, digest: str, seen: datetime) -> list[dict]:
    return [
        {
            "game_id": row.game_id,
            "season": int(row.season),
            "season_type": int(row.season_type),
            "game_date": row.date.to_pydatetime(),
            "home_id": row.home_id,
            "away_id": row.away_id,
            "neutral_site": bool(row.neutral_site),
            "venue_id": row.venue_id,
            "status": row.status,
            "home_score": _score(row.home_score),
            "away_score": _score(row.away_score),
            "first_seen": seen,
            "last_seen": seen,
            "snapshot_sha256": digest,
        }
        for row in frame.itertuples(index=False)
    ]


def ingest_snapshot(
    conn: sa.Connection, frame: pd.DataFrame, digest: str, season: int
) -> SnapshotReport:
    """Check a live schedule snapshot and store it. One transaction, caller-managed.

    An unchanged file -- same SHA-256 as a snapshot already recorded -- is a no-op that returns a
    report saying so. Re-fetching the same bytes must not add a row to the provenance trail, or the
    trail stops meaning "the schedule changed" and starts meaning "somebody ran the job".
    """
    existing = conn.execute(
        sa.text("SELECT row_count FROM schedule_snapshots WHERE sha256 = :d"), {"d": digest}
    ).first()
    if existing is not None:
        return SnapshotReport(
            sha256=digest, season=season, row_count=existing.row_count,
            scheduled_count=0, completed_count=0, postponed_count=0,
            added_game_ids=0, changed_game_ids=0, excluded_placeholders=0, unchanged=True,
        )

    franchises = _known_franchises(conn)
    if not franchises:
        raise ScheduleError(
            "the corpus has no games, so there is nothing to validate team ids against -- run "
            "`model.ingest` before accepting a live schedule"
        )
    excluded = _check_structure(frame, season, franchises)
    real = frame[frame["home_id"].isin(franchises) & frame["away_id"].isin(franchises)]

    added, changed = _check_continuity(
        real, _previous_state(conn, season), _predicted_game_ids(conn)
    )

    counts = real["status"].value_counts().to_dict()
    seen = datetime.now(UTC)
    report = SnapshotReport(
        sha256=digest,
        season=season,
        row_count=len(real),
        scheduled_count=int(counts.get("STATUS_SCHEDULED", 0)),
        completed_count=int(counts.get("STATUS_FINAL", 0)),
        postponed_count=int(counts.get("STATUS_POSTPONED", 0)),
        added_game_ids=added,
        changed_game_ids=changed,
        excluded_placeholders=excluded,
    )

    conn.execute(
        sa.text(
            "INSERT INTO schedule_snapshots (sha256, season, row_count, scheduled_count,"
            " completed_count, postponed_count, added_game_ids, changed_game_ids)"
            " VALUES (:sha256, :season, :row_count, :scheduled_count, :completed_count,"
            " :postponed_count, :added_game_ids, :changed_game_ids)"
        ),
        {
            "sha256": digest, "season": season, "row_count": report.row_count,
            "scheduled_count": report.scheduled_count, "completed_count": report.completed_count,
            "postponed_count": report.postponed_count, "added_game_ids": added,
            "changed_game_ids": changed,
        },
    )
    # `first_seen` is preserved on conflict; `last_seen` moves. When a row first appeared is a fact
    # about the schedule, not about this run, and overwriting it would erase how long we have known
    # about a game -- which is exactly what a churn threshold will eventually need.
    conn.execute(
        sa.text(
            "INSERT INTO scheduled_games (game_id, season, season_type, game_date, home_id,"
            " away_id, neutral_site, venue_id, status, home_score, away_score, first_seen,"
            " last_seen, snapshot_sha256)"
            " VALUES (:game_id, :season, :season_type, :game_date, :home_id, :away_id,"
            " :neutral_site, :venue_id, :status, :home_score, :away_score, :first_seen,"
            " :last_seen, :snapshot_sha256)"
            " ON CONFLICT (game_id) DO UPDATE SET"
            "   season = EXCLUDED.season, season_type = EXCLUDED.season_type,"
            "   game_date = EXCLUDED.game_date, home_id = EXCLUDED.home_id,"
            "   away_id = EXCLUDED.away_id, neutral_site = EXCLUDED.neutral_site,"
            "   venue_id = EXCLUDED.venue_id, status = EXCLUDED.status,"
            "   home_score = EXCLUDED.home_score, away_score = EXCLUDED.away_score,"
            "   last_seen = EXCLUDED.last_seen, snapshot_sha256 = EXCLUDED.snapshot_sha256"
        ),
        _rows_for_upsert(real, digest, seen),
    )
    return report


def refresh(url: str, season: int) -> SnapshotReport:
    """Fetch the live schedule and store it. The entry point the daily job calls."""
    frame, digest = loader.load_live_schedule(season)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            return ingest_snapshot(conn, frame, digest, season)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the live schedule (D-048)")
    parser.add_argument("url", help="SQLAlchemy URL of an ingested corpus")
    parser.add_argument("--season", type=int, required=True, help="the live season to fetch")
    args = parser.parse_args(argv)
    print(refresh(args.url, args.season).summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
