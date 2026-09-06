"""Read-only endpoints over the model's output (T-032).

    GET /predictions/upcoming          what the model expects to happen next
    GET /predictions/games/{game_id}   one prediction, decomposed, with its band's track record
    GET /predictions/accuracy          the record by confidence band
    GET /predictions/model             the frozen model's parameters

## Every one of these is a read, and that is a boundary rather than a coincidence

D-047 makes this surface unauthenticated: model results are public, every visitor is equivalent, and
no per-caller check exists to get wrong. What keeps that safe is the grant split -- `sports_api`
holds SELECT and nothing else on `predictions` and the corpus tables -- so the strongest statement
this module can make is that it never tries to write. `test_predictions_api.py` enforces it by
parsing this file: no INSERT, UPDATE, DELETE, and in fact no SQL at all. Every read goes through
`model.track_record`, which is where the queries live and where they are tested.

## Model outputs out; corpus rows never

The other half of T-032's security note. These endpoints serve predictions, bands, and the schedule
rows a prediction is about. They do not serve `corpus_games`, player box scores, or anything else
the training corpus holds -- not because a visitor could do harm with them (D-047 says they could
not) but because an API that returns whatever is convenient stops having a shape, and the shape is
what makes the next endpoint's security question answerable. The same AST check refuses the string
`corpus_` here.

## The clock is read once, at the edge

`as_of` defaults to now and is a parameter everywhere below it, exactly as in `model.job`. A
function that reads the clock is a function whose output cannot be reproduced, and the boundary is
the one place allowed to do it.
"""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection

from database import get_connection
from model import track_record as tr
from model.estimator import EstimatorError
from model.features import FEATURE_NAMES
from model.prediction import PredictionError, Scorer
from schemas import (
    BandOut,
    BandRecordOut,
    FactorOut,
    GameDetailOut,
    ModelOut,
    PredictionDetailOut,
    PredictionOut,
    RecordOut,
    ScheduledGameOut,
    UpcomingItemOut,
    UpcomingOut,
)

router = APIRouter(prefix="/predictions", tags=["predictions"])

#: Where the frozen artifact lives. Overridable so a deployment can mount it elsewhere; the default
#: is the repository's own `models/` directory, which is gitignored (`/models/` never enters git).
ARTIFACT_PATH = Path(
    os.getenv("MODEL_ARTIFACT")
    or Path(__file__).resolve().parents[2] / "models" / "logistic-2026.json"
)


@lru_cache(maxsize=4)
def _load_scorer(path: str) -> Scorer:
    """Load and cache the artifact. Verifying its content hash on every request would be waste.

    Cached for the life of the process, so replacing the artifact needs a restart. Under D-042 the
    model is frozen for the season, which makes that the right behaviour rather than a limitation:
    a served model that could change without anyone restarting anything is a served model nobody can
    say the version of.
    """
    return Scorer.load(Path(path))


# ── converters ────────────────────────────────────────────────────────────────


def _band_out(band: tr.Band) -> BandOut:
    return BandOut(slug=band.slug, label=band.label, lower=band.lower, upper=band.upper)


def _game_out(game: tr.ScheduledGame) -> ScheduledGameOut:
    return ScheduledGameOut(
        game_id=game.game_id,
        season=game.season,
        tip_off=game.date,
        home_id=game.home_id,
        away_id=game.away_id,
        neutral_site=game.neutral_site,
        status=game.status,
        home_score=game.home_score,
        away_score=game.away_score,
    )


def _prediction_out(record: tr.PredictionRecord) -> PredictionOut:
    return PredictionOut(
        game_id=record.game_id,
        model_version=record.model_version,
        as_of=record.as_of,
        home_win_probability=record.home_win_probability,
        favoured=record.favoured,
        confidence=record.confidence,
        band=_band_out(record.band),
    )


def _band_record_out(entry: tr.BandRecord) -> BandRecordOut:
    return BandRecordOut(
        band=_band_out(entry.band),
        games=entry.games,
        correct=entry.correct,
        hit_rate=entry.hit_rate,
        hit_rate_low=entry.hit_rate_low,
        hit_rate_high=entry.hit_rate_high,
        mean_probability=entry.mean_probability,
    )


def _factors(
    features: dict[str, float], contributions: dict[str, float]
) -> list[FactorOut]:
    """Pair each feature's value with its contribution, in the model's own feature order.

    Ordered by `FEATURE_NAMES` rather than by magnitude so a client rendering a waterfall gets a
    stable axis across games -- a chart whose bars reorder per game is a chart nobody can compare.
    A name the current `FEATURE_NAMES` does not contain still appears, sorted after the known ones:
    a prediction row written by a superseded model version carries that version's feature set, and
    dropping the unfamiliar half of it would silently render an incomplete decomposition.
    """
    order = {name: i for i, name in enumerate(FEATURE_NAMES)}
    names = sorted(contributions, key=lambda n: (order.get(n, len(order)), n))
    return [
        FactorOut(name=name, value=features.get(name, 0.0), contribution=contributions[name])
        for name in names
    ]


def _detail_out(record: tr.PredictionRecord) -> PredictionDetailOut:
    contributions = record.contributions or {}
    features = record.features or {}
    total = sum(contributions.values())
    logit = _logit(record.home_win_probability)
    return PredictionDetailOut(
        prediction=_prediction_out(record),
        # Recovered, not stored -- see `PredictionDetailOut`. The intercept is not a column on
        # `predictions`, and `logit - sum(contributions)` is exactly what it was.
        baseline_logit=logit - total,
        logit=logit,
        factors=_factors(features, contributions),
        schedule_snapshot=record.schedule_snapshot,
    )


def _logit(probability: float) -> float:
    clamped = min(max(probability, tr.LOG_LOSS_EPSILON), 1.0 - tr.LOG_LOSS_EPSILON)
    return math.log(clamped / (1.0 - clamped))


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.get("/upcoming", response_model=UpcomingOut)
def list_upcoming(
    horizon_days: int = Query(7, ge=1, le=tr.MAX_HORIZON_DAYS),
    as_of: datetime | None = Query(
        None, description="ISO-8601 moment to look forward from; defaults to now (UTC)."
    ),
    model_version: str | None = Query(None),
    conn: Connection = Depends(get_connection),
) -> UpcomingOut:
    """Scheduled games tipping off in the next `horizon_days`, each with the model's latest word.

    `horizon_days` is bounded rather than open: D-047 leaves this endpoint unauthenticated, and an
    unbounded horizon is an unbounded read for anyone who asks for one.

    A game with no prediction is listed with `prediction: null`, and the envelope reports `games`
    against `predicted` so a gap between them is visible without counting the array.
    """
    moment = _aware(as_of) if as_of is not None else datetime.now(UTC)
    try:
        rows = tr.upcoming(
            conn, as_of=moment, horizon_days=horizon_days, model_version=model_version
        )
    except tr.TrackRecordError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    items = [
        UpcomingItemOut(
            game=_game_out(row.game),
            prediction=_prediction_out(row.prediction) if row.prediction else None,
        )
        for row in rows
    ]
    predicted = sum(1 for row in rows if row.prediction is not None)
    version = next((r.prediction.model_version for r in rows if r.prediction), model_version)
    return UpcomingOut(
        as_of=moment,
        horizon_days=horizon_days,
        model_version=version,
        games=len(items),
        predicted=predicted,
        items=items,
    )


@router.get("/accuracy", response_model=RecordOut)
def accuracy_record(
    model_version: str | None = Query(
        None, description="Defaults to the version that made the most recent prediction."
    ),
    conn: Connection = Depends(get_connection),
) -> RecordOut:
    """The record of predicted versus actual, by confidence band (user stories 21 and 31).

    Scoped to one model version. Mixing versions would average a frozen model's trial with whatever
    superseded it and call the result a track record; D-017 makes 2026-27 the trial of *one* version.

    `hit_rate` is null rather than 0.0 for a band with nothing scored, and every band with games
    carries a 95% Wilson interval beside the point estimate -- three-from-three is 100% and means
    nothing, and a surface shown only the point estimate has no way to say so.
    """
    try:
        record = tr.compute_record(conn, model_version=model_version)
    except tr.TrackRecordError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RecordOut(
        model_version=record.model_version,
        games=record.games,
        correct=record.correct,
        accuracy=record.accuracy,
        log_loss=record.log_loss,
        brier=record.brier,
        pending=record.pending,
        bands=[_band_record_out(entry) for entry in record.bands],
    )


@router.get("/model", response_model=ModelOut)
def frozen_model() -> ModelOut:
    """The frozen model's parameters, so the browser can score with them (T-033, T-034).

    Public by D-047. This exists because `/models/` is gitignored, so a client cannot import the
    artifact at build time, and because T-034's what-if panel must run entirely client-side --
    story 27 requires hypothetical exploration never to reach anything that records it.
    """
    try:
        scorer = _load_scorer(str(ARTIFACT_PATH))
    except (FileNotFoundError, PredictionError, EstimatorError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"the model artifact is unavailable or unusable at {ARTIFACT_PATH}: {exc}",
        ) from exc
    model = scorer.model
    names = list(model.feature_names)
    return ModelOut(
        model_version=scorer.model_version,
        feature_names=names,
        intercept=model.intercept,
        coefficients=dict(zip(names, model.coefficients, strict=True)),
        means=dict(zip(names, model.means, strict=True)),
        stds=dict(zip(names, model.stds, strict=True)),
    )


@router.get("/games/{game_id}", response_model=GameDetailOut)
def game_detail(
    game_id: str,
    model_version: str | None = Query(None),
    conn: Connection = Depends(get_connection),
) -> GameDetailOut:
    """One game: the prediction that stands, its decomposition, its history, and its band's record.

    The prediction returned is the latest one made at or before tip-off -- the same rule the accuracy
    record scores by, so the page and the track record can never show different numbers for the same
    game.
    """
    try:
        detail = tr.game_detail(conn, game_id, model_version=model_version)
    except tr.TrackRecordError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no prediction for game {game_id!r}")
    return GameDetailOut(
        game=_game_out(detail.game),
        latest=_detail_out(detail.latest),
        history=[_prediction_out(p) for p in detail.history],
        band_record=_band_record_out(detail.band_record),
    )


def _aware(moment: datetime) -> datetime:
    """A naive query parameter is read as UTC rather than refused -- the only timezone this API
    speaks, and every stored moment is already in it."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
