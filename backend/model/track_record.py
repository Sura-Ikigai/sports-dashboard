"""The track record (T-032) -- what the model predicted, what happened, and how often it was right.

    record = compute_record(conn)                  # by confidence band, for one model version
    rows   = upcoming(conn, as_of=now, horizon_days=7)
    detail = game_detail(conn, "401810123")

`model.job` writes predictions and this reads them. Nothing here writes anything.

## The read layer for predictions is not `store.py`, on purpose

`store.py` is the corpus reader and is governed by D-039: SQL narrows by season and team and
**never** by an as-of predicate, enforced by an AST check that refuses the string `as_of` anywhere
in an executable literal. That rule is absolute there and stays absolute -- `load_upcoming` complied
with it at the cost of fetching a season of rows to use a week of them, rather than arguing its case
was different.

The `predictions` table has an `as_of` **column**, which cannot be selected without naming it, so the
corpus rule is not merely inconvenient here -- it is unsatisfiable. A separate module with its own,
weaker, still-mechanical rule is the honest resolution: `test_track_record.py` refuses a date
*comparison* in any SQL this file builds. Naming the column is allowed; comparing it in the database
is not.

The rule still has teeth, because the hazard is real and specific: **"the last prediction before
tip-off is the one scored"** is a filter over time, and getting it wrong changes the reported
accuracy with no symptom. So it lives in `last_before_tip_off`, a pure function over records, with a
test that a prediction dated after tip-off is dropped and a control proving the same prediction is
kept when it is dated before.

## Bands are on the favoured side's probability, not on the home team's

A 0.75 home probability and a 0.25 home probability are the same call at the same confidence,
pointing in opposite directions. Banding on the raw home probability would file them apart and make
"how often is the model right at this confidence" unanswerable (user story 21). So the band is taken
on `max(p, 1-p)` -- the probability the model assigned to the side it favoured -- which runs from
0.50 (a coin flip) to 1.0.

## An empty band reports no hit rate, not a hit rate of zero

`0.0` and "nothing to report" render identically as `0%`, and one of them is a lie. `hit_rate` is
`None` when a band has scored no games. For the bands that do have games, the Wilson interval is
reported beside the point estimate, because three-from-three is 100% and means nothing -- and a
surface that shows only the point estimate has no way to say so.

## What is excluded from the record, and how

- **Games that are not final.** A prediction on an unplayed game is not wrong yet. These are counted
  in `pending` rather than dropped silently, so a record cannot look complete while the season is.
- **Postponed and canceled games.** Excluded by joining to the game's status -- never by deleting a
  prediction row, which `sports_job`'s grant could not do anyway. The prediction was a true record of
  what the model said; it simply has no result to be scored against.
- **Predictions dated after tip-off.** There should be none: `load_upcoming` returns games strictly
  after `as_of` and `compute_features` refuses an as-of past tip-off (D-010). Filtering for them here
  anyway is not defensive dead code (F-060) -- it is this function's own definition of the thing it
  computes, and it is exercised by its own test.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa

from .prediction import favoured
from .records import PREDICTABLE_STATUSES, SCOREABLE_STATUSES

#: Probabilities are clamped this far from 0 and 1 before taking a log. A logistic model cannot
#: produce exactly 0 or 1, but a float that underflows to one of them would make log loss infinite,
#: and an infinite summary statistic is less informative than a very bad finite one.
LOG_LOSS_EPSILON = 1e-15

#: 95%, as the z-score for the Wilson interval.
WILSON_Z = 1.959963984540054

#: How far ahead `upcoming` will look. Bounded because D-047 leaves these endpoints
#: unauthenticated: an unbounded horizon is an unbounded read for anyone who asks.
MAX_HORIZON_DAYS = 30


class TrackRecordError(RuntimeError):
    """Raised when the record cannot be computed as stated -- an impossible result, most often."""


# ── confidence bands ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Band:
    """One confidence band, on the favoured side's probability.

    Half-open `[lower, upper)`, except the top band which closes at 1.0 so that a probability of
    exactly 1.0 has somewhere to go.
    """

    slug: str
    label: str
    lower: float
    upper: float

    def contains(self, confidence: float) -> bool:
        if self.upper >= 1.0:
            return self.lower <= confidence <= 1.0
        return self.lower <= confidence < self.upper


#: Four bands rather than ten. A season is ~1,230 games, so ten bands would put ~120 games in each
#: and the sampling noise on a hit rate would swamp the differences between them. The boundaries are
#: chosen to separate the questions a visitor actually asks -- "is this a coin flip?" (toss-up) from
#: "is this a real call?" (lean and up) -- rather than to make the arithmetic tidy.
BANDS: tuple[Band, ...] = (
    Band("toss-up", "Toss-up", 0.50, 0.55),
    Band("lean", "Lean", 0.55, 0.65),
    Band("clear", "Clear", 0.65, 0.75),
    Band("strong", "Strong", 0.75, 1.00),
)


def confidence(probability: float) -> float:
    """The probability the model assigned to the side it favoured. Runs 0.5 -> 1.0."""
    if not 0.0 <= probability <= 1.0:
        raise TrackRecordError(f"probability {probability!r} is outside [0, 1]")
    return max(probability, 1.0 - probability)


def band_for(probability: float) -> Band:
    """The band a home-win probability falls in.

    Raises rather than returning a default: `BANDS` tiles [0.5, 1.0] exactly, so a miss means the
    tiling has been edited into a state with a hole, and the visible symptom of a silent default
    would be games quietly accumulating in the wrong bucket.
    """
    value = confidence(probability)
    for band in BANDS:
        if band.contains(value):
            return band
    raise TrackRecordError(
        f"confidence {value!r} falls in no band -- BANDS must tile [0.5, 1.0] with no gap"
    )


# ── records ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PredictionRecord:
    """One row of `predictions`, as read.

    `features` and `contributions` are `None` when the row was read without its decomposition. That
    is not laziness: the accuracy record needs a probability and a moment for every prediction of the
    season, and carrying two JSONB blobs per row through that query costs far more than it explains.
    The game-detail read asks for them; the record does not.
    """

    game_id: str
    model_version: str
    as_of: datetime
    home_win_probability: float
    schedule_snapshot: str | None = None
    features: dict[str, float] | None = None
    contributions: dict[str, float] | None = None

    @property
    def confidence(self) -> float:
        return confidence(self.home_win_probability)

    @property
    def band(self) -> Band:
        return band_for(self.home_win_probability)

    @property
    def favoured(self) -> str:
        """Delegates to `prediction.favoured` -- one definition, so the surface and the record can
        never disagree about which side the model picked."""
        return favoured(self.home_win_probability)


@dataclass(frozen=True, slots=True)
class ScheduledGame:
    """One row of `scheduled_games`. Carries a result only once the game is final."""

    game_id: str
    season: int
    date: datetime
    home_id: str
    away_id: str
    neutral_site: bool
    status: str
    home_score: int | None = None
    away_score: int | None = None
    venue_id: str | None = None

    @property
    def is_scoreable(self) -> bool:
        return (
            self.status in SCOREABLE_STATUSES
            and self.home_score is not None
            and self.away_score is not None
        )

    @property
    def winner(self) -> str:
        """`"home"` or `"away"`. Raises on a tie, which the NBA does not have (F-049).

        A tie here means the feed is wrong, and the cost of guessing is an accuracy number computed
        partly from fiction. Refusing is loud and fixable; picking a side is neither.
        """
        if self.home_score is None or self.away_score is None:
            raise TrackRecordError(f"game {self.game_id!r} has no result to score against")
        if self.home_score == self.away_score:
            raise TrackRecordError(
                f"game {self.game_id!r} is final at {self.home_score}-{self.away_score}, a tie. "
                "NBA games do not end tied, so the feed is wrong -- refusing to score against it "
                "rather than picking a side."
            )
        return "home" if self.home_score > self.away_score else "away"


@dataclass(frozen=True, slots=True)
class ScoredPrediction:
    """One prediction paired with what actually happened."""

    prediction: PredictionRecord
    game: ScheduledGame

    @property
    def correct(self) -> bool:
        return self.prediction.favoured == self.game.winner


@dataclass(frozen=True, slots=True)
class BandRecord:
    """One band's slice of the record.

    `hit_rate` is `None` -- not `0.0` -- when nothing has been scored in this band. `mean_probability`
    is the mean confidence over the same games, and sits next to the hit rate because the pair is the
    calibration check D-008 makes co-equal with accuracy: a well-calibrated model wins about as often
    as it said it would.
    """

    band: Band
    games: int
    correct: int
    hit_rate: float | None
    hit_rate_low: float | None
    hit_rate_high: float | None
    mean_probability: float | None


@dataclass(frozen=True, slots=True)
class Record:
    """The whole track record for one model version."""

    model_version: str
    games: int
    correct: int
    accuracy: float | None
    log_loss: float | None
    brier: float | None
    bands: tuple[BandRecord, ...]
    pending: int

    def summary(self) -> str:
        if self.games == 0:
            return f"model {self.model_version} · nothing scored yet · {self.pending} pending"
        return (
            f"model {self.model_version} · {self.correct}/{self.games} "
            f"({self.accuracy:.4f}) · log loss {self.log_loss:.4f} · {self.pending} pending"
        )


def wilson_interval(correct: int, games: int, *, z: float = WILSON_Z) -> tuple[float, float]:
    """A 95% interval for a hit rate, by the Wilson score method.

    The normal approximation is wrong exactly where it matters here -- small samples and rates near
    0 or 1 -- and produces intervals that run off the end of [0, 1]. Wilson does not, and it costs
    four lines. Three-from-three is a point estimate of 1.0 and an interval of about [0.44, 1.0],
    which is the honest way to render a band that has barely been tested.
    """
    if games <= 0:
        raise TrackRecordError("a Wilson interval over zero games is not defined")
    p = correct / games
    denominator = 1.0 + z * z / games
    centre = (p + z * z / (2 * games)) / denominator
    spread = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denominator
    return max(0.0, centre - spread), min(1.0, centre + spread)


# ── the scoring rule ──────────────────────────────────────────────────────────


def last_before_tip_off(
    predictions: Iterable[PredictionRecord], tip_off: datetime
) -> PredictionRecord | None:
    """The prediction that gets scored: the latest one made at or before tip-off.

    D-012 keeps every daily prediction because each is a true statement about a different moment --
    a rest value computed six days out is *wrong*, not merely stale. Exactly one of them can be the
    model's final word, and this is it.

    `as_of == tip_off` counts as before: `compute_features` allows it (it refuses only
    `as_of > target.date`), so a prediction made at the instant of tip-off is a legitimate one and
    excluding it here would disagree with the code that produced it.

    Returns `None` when there is nothing to score -- an empty list, or one holding only predictions
    dated after tip-off.
    """
    eligible = [p for p in predictions if p.as_of <= tip_off]
    if not eligible:
        return None
    return max(eligible, key=lambda p: p.as_of)


def score(
    predictions: Iterable[PredictionRecord],
    games: Iterable[ScheduledGame],
    *,
    model_version: str,
) -> Record:
    """Pair predictions with results and aggregate by band. Pure -- no database, no clock.

    Only predictions carrying `model_version` are considered. Mixing versions in one hit rate would
    average a frozen model's trial with whatever superseded it and call the result a track record;
    D-017 makes 2026-27 the genuine trial of *one* version, so the record is per version and says
    which one.

    A prediction whose game is absent from `games` is neither scored nor counted pending. That is not
    a silent drop: the caller passes the games, so an absent one means the caller narrowed to a set
    this prediction is not part of.
    """
    by_game: dict[str, list[PredictionRecord]] = {}
    for prediction in predictions:
        if prediction.model_version != model_version:
            continue
        by_game.setdefault(prediction.game_id, []).append(prediction)

    scored: list[ScoredPrediction] = []
    pending = 0
    for game in games:
        held = by_game.get(game.game_id)
        if not held:
            continue
        final = last_before_tip_off(held, game.date)
        if final is None:
            continue
        if not game.is_scoreable:
            # Not yet played, postponed, or canceled. Only the first of those will ever be scored,
            # but all three are "no result", and `pending` is the count that stops a record from
            # looking complete while games are still outstanding.
            pending += 1
            continue
        scored.append(ScoredPrediction(prediction=final, game=game))

    bands = tuple(_band_record(band, scored) for band in BANDS)
    correct = sum(1 for s in scored if s.correct)
    return Record(
        model_version=model_version,
        games=len(scored),
        correct=correct,
        accuracy=(correct / len(scored)) if scored else None,
        log_loss=_log_loss(scored) if scored else None,
        brier=_brier(scored) if scored else None,
        bands=bands,
        pending=pending,
    )


def _band_record(band: Band, scored: Sequence[ScoredPrediction]) -> BandRecord:
    members = [s for s in scored if s.prediction.band is band]
    if not members:
        return BandRecord(
            band=band, games=0, correct=0, hit_rate=None, hit_rate_low=None,
            hit_rate_high=None, mean_probability=None,
        )
    correct = sum(1 for s in members if s.correct)
    low, high = wilson_interval(correct, len(members))
    return BandRecord(
        band=band,
        games=len(members),
        correct=correct,
        hit_rate=correct / len(members),
        hit_rate_low=low,
        hit_rate_high=high,
        mean_probability=sum(s.prediction.confidence for s in members) / len(members),
    )


def _log_loss(scored: Sequence[ScoredPrediction]) -> float:
    """Mean negative log likelihood of the *home* outcome. Lower is better.

    Taken on the home probability rather than the favoured one because log loss scores the
    distribution, not the pick: a confident wrong call has to be punished more than a hesitant one,
    and folding both sides onto the favourite would hide exactly that.
    """
    total = 0.0
    for s in scored:
        p = min(max(s.prediction.home_win_probability, LOG_LOSS_EPSILON), 1.0 - LOG_LOSS_EPSILON)
        total += -math.log(p) if s.game.winner == "home" else -math.log(1.0 - p)
    return total / len(scored)


def _brier(scored: Sequence[ScoredPrediction]) -> float:
    """Mean squared error of the home probability against the home outcome. Lower is better."""
    total = 0.0
    for s in scored:
        outcome = 1.0 if s.game.winner == "home" else 0.0
        total += (s.prediction.home_win_probability - outcome) ** 2
    return total / len(scored)


# ── reads ─────────────────────────────────────────────────────────────────────
#
# Every narrowing below is by identity -- a game id, a model version, a status. None is by time. See
# the module docstring on why that rule exists here in a weaker form than D-039's.


def _as_tuple(values: Iterable[str] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    out = tuple(values)
    if not out:
        # The same refusal `store._as_tuple` makes, for the same reason: an empty collection reads
        # as "no narrowing" under a naive `if values:` and silently widens to everything.
        raise TrackRecordError(
            "an empty narrowing was requested -- pass None to mean 'everything', or a non-empty "
            "collection to mean 'these'."
        )
    return out


def _decode(value: object) -> dict[str, float] | None:
    """JSONB comes back as a dict from psycopg and as a string from some drivers. Accept both."""
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)  # type: ignore[arg-type]


def load_predictions(
    conn: sa.Connection,
    *,
    game_ids: Iterable[str] | None = None,
    model_version: str | None = None,
    with_decomposition: bool = False,
) -> list[PredictionRecord]:
    """Prediction rows, narrowed by game and/or model version.

    `with_decomposition` controls whether the two JSONB columns are read at all -- see
    `PredictionRecord`. Ordered oldest-first so a caller rendering a game's history gets it in the
    order it happened.
    """
    game_ids = _as_tuple(game_ids)

    clauses: list[str] = []
    params: dict[str, object] = {}
    if game_ids is not None:
        clauses.append("game_id = ANY(:game_ids)")
        params["game_ids"] = list(game_ids)
    if model_version is not None:
        clauses.append("model_version = :model_version")
        params["model_version"] = model_version
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    columns = "game_id, model_version, as_of, home_win_prob, schedule_snapshot"
    if with_decomposition:
        columns += ", features, contributions"

    rows = conn.execute(
        sa.text(f"SELECT {columns} FROM predictions{where} ORDER BY game_id, as_of"), params
    ).all()
    return [
        PredictionRecord(
            game_id=r.game_id,
            model_version=r.model_version,
            as_of=r.as_of,
            home_win_probability=r.home_win_prob,
            schedule_snapshot=r.schedule_snapshot,
            features=_decode(r.features) if with_decomposition else None,
            contributions=_decode(r.contributions) if with_decomposition else None,
        )
        for r in rows
    ]


def load_scheduled_games(
    conn: sa.Connection,
    *,
    game_ids: Iterable[str] | None = None,
    statuses: Iterable[str] | None = None,
) -> list[ScheduledGame]:
    """Rows of `scheduled_games`, narrowed by game id and/or status."""
    game_ids = _as_tuple(game_ids)
    statuses = _as_tuple(statuses)

    clauses: list[str] = []
    params: dict[str, object] = {}
    if game_ids is not None:
        clauses.append("game_id = ANY(:game_ids)")
        params["game_ids"] = list(game_ids)
    if statuses is not None:
        clauses.append("status = ANY(:statuses)")
        params["statuses"] = list(statuses)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    rows = conn.execute(
        sa.text(
            "SELECT game_id, season, game_date, home_id, away_id, neutral_site, status,"
            "       home_score, away_score, venue_id"
            f"  FROM scheduled_games{where}"
            "  ORDER BY game_date, game_id"
        ),
        params,
    ).all()
    return [
        ScheduledGame(
            game_id=r.game_id,
            season=r.season,
            date=r.game_date,
            home_id=r.home_id,
            away_id=r.away_id,
            neutral_site=r.neutral_site,
            status=r.status,
            home_score=r.home_score,
            away_score=r.away_score,
            venue_id=r.venue_id,
        )
        for r in rows
    ]


def model_versions(conn: sa.Connection) -> list[str]:
    """Every model version present in `predictions`, most recently used first.

    Ordering by the newest prediction each version made, which is an aggregate over a column rather
    than a filter on one -- the distinction the module docstring draws.
    """
    rows = conn.execute(
        sa.text(
            "SELECT model_version, max(as_of) AS newest FROM predictions"
            " GROUP BY model_version ORDER BY newest DESC"
        )
    ).all()
    return [r.model_version for r in rows]


def _resolve_version(conn: sa.Connection, model_version: str | None) -> str:
    if model_version is not None:
        return model_version
    versions = model_versions(conn)
    if not versions:
        raise TrackRecordError(
            "no predictions have been made yet, so there is no track record and no model version to "
            "default to. Run `python -m model.job` first."
        )
    return versions[0]


def compute_record(conn: sa.Connection, *, model_version: str | None = None) -> Record:
    """The accuracy record by confidence band, for one model version.

    Defaults to the version that made the most recent prediction. Reads only the games that have a
    prediction, so a schedule full of unpredicted games does not inflate `pending`.
    """
    version = _resolve_version(conn, model_version)
    predictions = load_predictions(conn, model_version=version)
    if not predictions:
        return score([], [], model_version=version)
    games = load_scheduled_games(conn, game_ids={p.game_id for p in predictions})
    return score(predictions, games, model_version=version)


@dataclass(frozen=True, slots=True)
class UpcomingPrediction:
    """A scheduled game and the model's latest word on it, if it has one."""

    game: ScheduledGame
    prediction: PredictionRecord | None


def upcoming(
    conn: sa.Connection,
    *,
    as_of: datetime,
    horizon_days: int = 7,
    model_version: str | None = None,
) -> list[UpcomingPrediction]:
    """Scheduled games tipping off in `(as_of, as_of + horizon_days]`, each with its latest prediction.

    Games with no prediction are **included, carrying `None`**. Omitting them would make "the job has
    not run" and "there are no games" render identically, and the first of those is an outage.

    The window is applied in Python for the same reason `store.load_upcoming` applies its in Python:
    the narrowing that reaches the database is by status, and the comparison stays in code that a
    test can read.
    """
    if not 1 <= horizon_days <= MAX_HORIZON_DAYS:
        raise TrackRecordError(
            # Worded without the word the SQL-shape check refuses -- see the module docstring.
            # The rule has no exceptions, including for prose that could never reach a database.
            f"horizon_days must be 1..{MAX_HORIZON_DAYS} inclusive; got {horizon_days}"
        )
    until = as_of + timedelta(days=horizon_days)
    games = [
        game
        for game in load_scheduled_games(conn, statuses=PREDICTABLE_STATUSES)
        if as_of < game.date <= until
    ]
    if not games:
        return []

    version = model_version if model_version is not None else _latest_version_or_none(conn)
    held: dict[str, list[PredictionRecord]] = {}
    if version is not None:
        for p in load_predictions(
            conn, game_ids={g.game_id for g in games}, model_version=version
        ):
            held.setdefault(p.game_id, []).append(p)

    return [
        UpcomingPrediction(
            game=game, prediction=last_before_tip_off(held.get(game.game_id, []), game.date)
        )
        for game in games
    ]


def _latest_version_or_none(conn: sa.Connection) -> str | None:
    versions = model_versions(conn)
    return versions[0] if versions else None


@dataclass(frozen=True, slots=True)
class GameDetail:
    """One game, the model's latest word on it with its full decomposition, and how it got there."""

    game: ScheduledGame
    latest: PredictionRecord
    history: tuple[PredictionRecord, ...]
    band_record: BandRecord


def game_detail(
    conn: sa.Connection, game_id: str, *, model_version: str | None = None
) -> GameDetail | None:
    """Everything the game-detail surface needs for one game, or `None` if nothing is known about it.

    `history` is every prediction made for this game under this version, oldest first -- which is
    what makes D-012's append-only design visible rather than merely stored. `band_record` is the
    model's track record *in this prediction's band*, so the surface can ground the number in a hit
    rate instead of asserting it (user story 21).
    """
    games = load_scheduled_games(conn, game_ids=[game_id])
    if not games:
        return None
    game = games[0]

    version = model_version if model_version is not None else _latest_version_or_none(conn)
    if version is None:
        return None
    history = load_predictions(
        conn, game_ids=[game_id], model_version=version, with_decomposition=True
    )
    latest = last_before_tip_off(history, game.date)
    if latest is None:
        return None

    return GameDetail(
        game=game,
        latest=latest,
        history=tuple(history),
        band_record=_band_record_for(conn, latest.band, version),
    )


def _band_record_for(conn: sa.Connection, band: Band, model_version: str) -> BandRecord:
    """This band's slice of the full record. One extra pass over the season, which is cheap and
    keeps the definition of a band's hit rate in exactly one place."""
    record = compute_record(conn, model_version=model_version)
    for entry in record.bands:
        if entry.band is band:
            return entry
    raise TrackRecordError(f"band {band.slug!r} is missing from the record")
