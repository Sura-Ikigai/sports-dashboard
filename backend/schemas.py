from datetime import datetime

from pydantic import BaseModel


class TeamOut(BaseModel):
    id: int
    external_id: str
    name: str
    abbreviation: str | None
    logo_url: str | None
    source: str | None

    model_config = {"from_attributes": True}


class GameOut(BaseModel):
    id: int
    external_id: str
    home_team: TeamOut
    away_team: TeamOut
    home_score: int | None
    away_score: int | None
    status: str
    game_time: datetime

    model_config = {"from_attributes": True}


class FavoriteOut(BaseModel):
    team_id: int

    model_config = {"from_attributes": True}


# ── model outputs (T-032) ─────────────────────────────────────────────────────
#
# These describe what the model said, never what the corpus holds. T-032's security note is that
# these endpoints are unauthenticated (D-047) and read a database that also holds the training
# corpus, so the rule is: model outputs out, corpus rows never. `test_predictions_api.py` asserts
# that mechanically rather than trusting this comment.


class BandOut(BaseModel):
    """A confidence band, on the probability the model gave the side it favoured."""

    slug: str
    label: str
    lower: float
    upper: float


class ScheduledGameOut(BaseModel):
    """A game from the live schedule feed (D-048). Scores are present only once it is final."""

    game_id: str
    season: int
    tip_off: datetime
    home_id: str
    away_id: str
    neutral_site: bool
    status: str
    home_score: int | None = None
    away_score: int | None = None


class PredictionOut(BaseModel):
    """One prediction, without its decomposition. What a list of games needs."""

    game_id: str
    model_version: str
    as_of: datetime
    home_win_probability: float
    favoured: str
    confidence: float
    band: BandOut


class FactorOut(BaseModel):
    """One feature's contribution to the log-odds, with the value that produced it (story 23).

    `contribution` is in **log-odds**, not probability: +0.5 here is not "+50%". Anything rendering
    these has to say so, which is why the unit is named in this docstring and in the API description
    rather than left for a reader to infer from the magnitudes.
    """

    name: str
    value: float
    contribution: float


class BandRecordOut(BaseModel):
    """How the model has done in one band. `hit_rate` is null -- not 0.0 -- when nothing is scored."""

    band: BandOut
    games: int
    correct: int
    hit_rate: float | None
    hit_rate_low: float | None
    hit_rate_high: float | None
    mean_probability: float | None


class PredictionDetailOut(BaseModel):
    """A prediction with everything needed to explain it, plus how the model got here.

    `baseline_logit` is **recovered**, not stored: `predictions` persists the probability and the
    per-feature contributions, and the intercept is `logit - sum(contributions)` by construction.
    The recovery is arithmetic on stored numbers, not an estimate.
    """

    prediction: PredictionOut
    baseline_logit: float
    logit: float
    factors: list[FactorOut]
    schedule_snapshot: str | None


class GameDetailOut(BaseModel):
    """Everything the game-detail surface (T-034) needs for one game."""

    game: ScheduledGameOut
    latest: PredictionDetailOut
    history: list[PredictionOut]
    band_record: BandRecordOut


class UpcomingItemOut(BaseModel):
    """A scheduled game and the model's latest word on it.

    `prediction` is null when the job has not scored this game yet. Such games are listed rather
    than omitted, so "the job has not run" cannot render identically to "there are no games".
    """

    game: ScheduledGameOut
    prediction: PredictionOut | None


class UpcomingOut(BaseModel):
    as_of: datetime
    horizon_days: int
    model_version: str | None
    games: int
    predicted: int
    items: list[UpcomingItemOut]


class RecordOut(BaseModel):
    """The accuracy record for one model version, by confidence band."""

    model_version: str
    games: int
    correct: int
    accuracy: float | None
    log_loss: float | None
    brier: float | None
    pending: int
    bands: list[BandRecordOut]

    # `model_version` collides with pydantic v2's protected `model_` namespace, which warns at class
    # definition time. The field name is the one the database, the artifact and D-012 all use, so the
    # namespace is the thing that gives way.
    model_config = {"protected_namespaces": ()}


class ModelOut(BaseModel):
    """The frozen model's parameters, so a second implementation can score with them.

    Public by D-047: every model output is visible to every visitor, and coefficients are already
    quoted in the analysis documents. This exists because T-033's TypeScript scorer and T-034's
    what-if panel run in the browser, and the artifact is gitignored -- `/models/` never enters git,
    so the frontend cannot import it at build time.
    """

    model_version: str
    feature_names: list[str]
    intercept: float
    coefficients: dict[str, float]
    means: dict[str, float]
    stds: dict[str, float]

    model_config = {"protected_namespaces": ()}
