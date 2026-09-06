"""The daily prediction job (T-031, D-012) -- the only thing in this codebase that writes predictions.

    PYTHONPATH=backend python -m model.job postgresql://.../corpus \
        --artifact models/logistic-2026.json --season 2027

Refreshes the live schedule (D-048), scores every game tipping off in the next seven days, and
**appends** a prediction row for each. It never updates one.

## Append-only, and why the horizon is seven days rather than one

D-012 keys predictions by `(game_id, model_version, as_of)` and re-predicts daily rather than
updating a row in place. The reason is a property of the features, not a filing preference: a rest
value computed six days out is *wrong* rather than merely stale -- the team will play again before
then -- so a prediction is a statement about a moment, and overwriting it would destroy the record
of what the model actually said when it said it. Seven daily predictions for one game is seven
different statements, and the accuracy record scores the last one before tip-off.

The append-only guarantee is a **grant**, not a convention: `sports_job` holds INSERT on
`predictions` without UPDATE or DELETE (T-021), so this module could not overwrite a row if it tried.
`ON CONFLICT DO NOTHING` is compatible with that grant; `DO UPDATE` would not be, which is a pleasant
way of finding out you have written something you should not have.

## The as-of moment is truncated to the hour

Two runs in the same hour produce the same `as_of`, collide on the unique constraint, and the second
writes nothing. That is what makes the job **idempotent against a double-fire** -- the hazard the
plan's *Future hardening* section already names for the in-process scheduler under a second replica.

Truncating further, to the day, was the obvious alternative and is wrong: features would be computed
at midnight UTC, which is *before* the previous evening's US games have finished, so every prediction
would be built on ratings that ignore last night's results. The hour is short enough to be honest
about when the prediction was made and long enough to absorb a retry.

## What it will not do

- **It does not predict a game that has tipped off.** `load_upcoming` returns games strictly after
  `as_of`, and `compute_features` refuses an as-of past tip-off anyway (D-010). Two checks, because
  the failure is a model re-predicting a finished game, which is the thing D-010 exists to prevent.
- **It does not predict a postponed game.** Only `STATUS_SCHEDULED` rows are returned. A prediction
  already made for a game that is later postponed is *retained* -- it is a true record of what the
  model said -- and excluded from the accuracy record by joining to the game's status, never by
  deleting a row.
- **It does not write anything but predictions.** The schedule refresh is `model.schedule`'s, which
  runs as `sports_ingest`; this runs as `sports_job`, whose only write is the INSERT below.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from .prediction import Prediction, Scorer
from .schedule import SnapshotReport, refresh
from .store import load_serving_context, load_upcoming

DEFAULT_HORIZON_DAYS = 7


class JobError(RuntimeError):
    """Raised when the job cannot run as stated."""


@dataclass(frozen=True, slots=True)
class JobReport:
    as_of: datetime
    model_version: str
    snapshot: SnapshotReport
    upcoming: int
    written: int

    def summary(self) -> str:
        skipped = self.upcoming - self.written
        return (
            f"as of {self.as_of.isoformat()} · model {self.model_version} · "
            f"{self.upcoming} upcoming · {self.written} written"
            + (f" · {skipped} already present" if skipped else "")
        )


def truncate_to_hour(moment: datetime) -> datetime:
    """The as-of moment the job predicts at. See the module docstring on why the hour and not the day."""
    if moment.tzinfo is None:
        raise JobError("as_of must be timezone-aware")
    return moment.replace(minute=0, second=0, microsecond=0)


def _insert(conn: sa.Connection, predictions: list[Prediction], snapshot: str) -> int:
    """Append predictions. Returns how many rows were actually written.

    `ON CONFLICT DO NOTHING` rather than an existence check: two workers racing would both pass a
    check and then one would fail on the constraint, and the constraint is the thing that is actually
    true. It is also the strongest conflict clause `sports_job`'s grant permits -- `DO UPDATE`
    requires UPDATE, which it deliberately does not hold.
    """
    if not predictions:
        return 0
    result = conn.execute(
        sa.text(
            "INSERT INTO predictions (game_id, model_version, as_of, home_win_prob, features,"
            " contributions, schedule_snapshot)"
            " VALUES (:game_id, :model_version, :as_of, :home_win_prob, :features,"
            " :contributions, :schedule_snapshot)"
            " ON CONFLICT (game_id, model_version, as_of) DO NOTHING"
        ),
        [
            {
                "game_id": p.game_id,
                "model_version": p.model_version,
                "as_of": p.as_of,
                "home_win_prob": p.home_win_probability,
                "features": json.dumps(p.features, sort_keys=True),
                "contributions": json.dumps(p.contributions, sort_keys=True),
                "schedule_snapshot": snapshot,
            }
            for p in predictions
        ],
    )
    return result.rowcount


def run(
    url: str,
    artifact: Path,
    season: int,
    *,
    as_of: datetime | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> JobReport:
    """Refresh the schedule, score the horizon, append the predictions.

    `as_of` is a parameter with a default rather than a call to `datetime.now()` inside the scoring
    path -- the same rule `features` follows, for the same reason: a function that reads the clock is
    a function whose output cannot be reproduced. The job is the one place allowed to ask what time
    it is, and it does so once.
    """
    moment = truncate_to_hour(as_of if as_of is not None else datetime.now(UTC))
    scorer = Scorer.load(artifact)

    snapshot = refresh(url, season)

    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            context = load_serving_context(conn)
            upcoming = load_upcoming(conn, as_of=moment, horizon_days=horizon_days)
            predictions = [scorer.predict(context, target, moment) for target in upcoming]
            written = _insert(conn, predictions, snapshot.sha256)
    finally:
        engine.dispose()

    return JobReport(
        as_of=moment,
        model_version=scorer.model_version,
        snapshot=snapshot,
        upcoming=len(upcoming),
        written=written,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append daily predictions over a 7-day horizon")
    parser.add_argument("url", help="SQLAlchemy URL of an ingested corpus")
    parser.add_argument("--artifact", type=Path, required=True, help="the frozen model artifact")
    parser.add_argument("--season", type=int, required=True, help="the live season")
    parser.add_argument(
        "--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS,
        help=f"how far ahead to predict (default {DEFAULT_HORIZON_DAYS})",
    )
    parser.add_argument(
        "--as-of", default=None,
        help="ISO-8601 prediction moment; defaults to now. Truncated to the hour either way.",
    )
    args = parser.parse_args(argv)

    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    if as_of is not None and as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    report = run(
        args.url, args.artifact, args.season, as_of=as_of, horizon_days=args.horizon_days
    )
    print(report.snapshot.summary())
    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
