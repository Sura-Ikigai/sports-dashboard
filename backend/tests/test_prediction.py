"""T-031 -- the one scoring interface, and the exactness the waterfall depends on.

The headline test here is `test_the_contributions_sum_to_the_logit_exactly`. D-040's game-detail
surface shows a *decomposition*, not an attribution, and the only thing that makes that word honest
is that the per-feature terms add up to the log-odds with no residual. Asserted as an identity --
`==`, not `pytest.approx` -- because it is the same arithmetic on both sides, so anything less than
exact means a term went missing.

The second thing worth pinning is that this module does not become a second implementation of
scoring. D-011 removed train/serve skew by having one feature function; T-031 would reintroduce it
one layer up if `predict` computed a probability its own way, so `predict` is cross-checked against
`LogisticModel.predict_proba` -- the path T-009 already tests -- rather than against itself.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from model.availability import Appearance
from model.estimator import LogisticModel, save_artifact
from model.features import FEATURE_NAMES, Context, Game, Matchup, to_vector
from model.prediction import Prediction, PredictionError, Scorer, _sigmoid

EPOCH = datetime(2024, 1, 1, 19, 0, tzinfo=UTC)
ROTATION = 9


def at(day: float) -> datetime:
    return EPOCH + timedelta(days=day)


def _game(gid: str, day: float, home: str, away: str, hs: int, aws: int) -> Game:
    return Game(gid, at(day), 2024, home, away, hs, aws)


HISTORY = [
    _game("g1", 0, "A", "C", 110, 100),
    _game("g2", 2, "D", "A", 105, 100),
    _game("g3", 4, "A", "B", 120, 100),
    _game("g4", 6, "B", "C", 99, 90),
    _game("g5", 8, "D", "B", 100, 88),
]
TARGET = Matchup("target", at(9), 2024, "A", "B")


def _context() -> Context:
    teams = {t for g in HISTORY for t in (g.home_id, g.away_id)}
    people = {
        team: [
            Appearance(g.game_id, g.date, f"{team}-p{i}", 30.0, False)
            for g in HISTORY
            if team in (g.home_id, g.away_id)
            for i in range(ROTATION)
        ]
        for team in teams
    }
    return Context(HISTORY, people)


def _model(**overrides) -> LogisticModel:
    """A hand-built model. Constructed rather than fitted so the tests do not depend on `fit`
    converging -- what is under test here is the decomposition, not the optimiser."""
    n = len(FEATURE_NAMES)
    defaults = {
        "feature_names": FEATURE_NAMES,
        "coefficients": tuple(0.5 - 0.1 * i for i in range(n)),
        "intercept": 0.25,
        "means": tuple(1.0 * i for i in range(n)),
        "stds": tuple(1.0 + i for i in range(n)),
    }
    return LogisticModel(**{**defaults, **overrides})


def _scorer(**overrides) -> Scorer:
    return Scorer(model=_model(**overrides), model_version="test00000000")


# --- the exactness the waterfall rests on ---------------------------------------------------------


def test_the_contributions_sum_to_the_logit_exactly():
    """**The property D-040's waterfall is built on.** Not `approx`: the same arithmetic produces
    both sides, so a residual means a term was dropped, not that floats drifted."""
    prediction = _scorer().predict(_context(), TARGET, at(9))
    assert prediction.logit == prediction.baseline_logit + sum(prediction.contributions.values())
    assert prediction.home_win_probability == _sigmoid(prediction.logit)


def test_the_probability_agrees_with_the_estimators_own_scoring_path():
    """The cross-check that stops this becoming a second implementation (D-011, one layer up).

    `LogisticModel.predict_proba` is the path T-009 already tests. If `predict` ever computes a
    probability its own way, a user would see one number on a page and another in the accuracy
    record, and neither would be wrong in a way anyone could point at.
    """
    context, scorer = _context(), _scorer()
    prediction = scorer.predict(context, TARGET, at(9))
    vector = to_vector(prediction.features)
    assert prediction.home_win_probability == pytest.approx(
        scorer.model.predict_proba([vector])[0], abs=1e-15
    )


def test_a_feature_sitting_at_its_training_mean_contributes_exactly_zero():
    """The interpretable property that makes a waterfall readable: a bar's length is distance from
    average, so a league-average team on that factor gets no bar at all."""
    context = _context()
    features = _scorer().predict(context, TARGET, at(9)).features
    # Re-centre the model on this game's own values, so every feature is exactly at its mean.
    centred = _scorer(means=tuple(features[n] for n in FEATURE_NAMES))
    prediction = centred.predict(context, TARGET, at(9))
    assert all(c == 0.0 for c in prediction.contributions.values())
    assert prediction.logit == prediction.baseline_logit
    assert prediction.home_win_probability == _sigmoid(centred.model.intercept)


def test_a_zero_coefficient_feature_contributes_nothing_however_extreme_its_value():
    context = _context()
    zeroed = _scorer(coefficients=(0.0,) * len(FEATURE_NAMES))
    prediction = zeroed.predict(context, TARGET, at(9))
    assert set(prediction.contributions.values()) == {0.0}
    assert prediction.home_win_probability == pytest.approx(_sigmoid(0.25))


def test_contributions_are_keyed_by_exactly_the_feature_names():
    """The waterfall renders these in order and the TypeScript scorer (T-033) is pinned against
    them, so a missing or extra key is a contract break, not a display bug."""
    prediction = _scorer().predict(_context(), TARGET, at(9))
    assert set(prediction.contributions) == set(FEATURE_NAMES)
    assert set(prediction.features) == set(FEATURE_NAMES)
    assert list(prediction.contributions) == list(FEATURE_NAMES)


def test_the_prediction_carries_its_inputs_not_just_its_output():
    """D-012 persists the feature vector alongside the probability. A prediction whose inputs were
    not recorded cannot be explained six months later."""
    prediction = _scorer().predict(_context(), TARGET, at(9))
    assert prediction.game_id == "target"
    assert prediction.as_of == at(9)
    assert prediction.model_version == "test00000000"
    assert all(isinstance(v, float) for v in prediction.features.values())


# --- the boundaries it inherits and the one it adds -----------------------------------------------


def test_an_as_of_after_tip_off_is_refused():
    """Inherited from `compute_features`, and asserted here because this is the module an API route
    will call with a caller-supplied moment (D-010: a model that re-predicts a finished game is
    worthless)."""
    from model.features import FeatureLeakageError

    with pytest.raises(FeatureLeakageError, match="after tip-off"):
        _scorer().predict(_context(), TARGET, at(9.5))


def test_scoring_is_deterministic():
    context, scorer = _context(), _scorer()
    first = scorer.predict(context, TARGET, at(9))
    second = scorer.predict(context, TARGET, at(9))
    assert first == second


def test_an_artifact_scoring_a_different_feature_set_is_refused(tmp_path):
    """The freeze made this concrete: an artifact fitted before T-030 carries seven feature names
    against today's four. If the counts ever happened to match, every coefficient would be paired
    with the wrong feature and the result would look perfectly ordinary."""
    stale = LogisticModel(
        feature_names=("elo_diff", "form_diff", "point_diff_diff"),
        coefficients=(0.5, 0.2, 0.1),
        intercept=0.0,
        means=(0.0, 0.0, 0.0),
        stds=(1.0, 1.0, 1.0),
    )
    path = tmp_path / "stale.json"
    save_artifact(stale, path, training={"note": "pre-freeze"})
    with pytest.raises(PredictionError, match="refusing to score with a mismatched feature set"):
        Scorer.load(path)


def test_a_matching_artifact_round_trips_through_load(tmp_path):
    """The non-vacuity control for the refusal above: a check that rejected every artifact would
    satisfy it perfectly."""
    path = tmp_path / "good.json"
    version = save_artifact(_model(), path, training={"note": "current"})
    scorer = Scorer.load(path)
    assert scorer.model_version == version
    assert scorer.model.feature_names == FEATURE_NAMES
    assert scorer.predict(_context(), TARGET, at(9)).model_version == version


def test_an_edited_artifact_is_refused(tmp_path):
    """Inherited from `load_artifact`'s content hash, asserted end-to-end because this is the load
    path the served image uses."""
    from model.estimator import EstimatorError

    path = tmp_path / "tampered.json"
    save_artifact(_model(), path, training={"note": "current"})
    document = path.read_text().replace('"intercept": 0.25', '"intercept": 9.99')
    assert document != path.read_text(), "the edit must actually change the file"
    path.write_text(document)
    with pytest.raises(EstimatorError, match="edited since it was written"):
        Scorer.load(path)


# --- numerics -------------------------------------------------------------------------------------


@pytest.mark.parametrize("z", [-800.0, -50.0, -1.0, 0.0, 1.0, 50.0, 800.0])
def test_the_sigmoid_is_stable_at_the_extremes(z):
    """The naive `1/(1+exp(-z))` overflows on large negative z. A model this well-conditioned will
    not produce one, but a scorer that raises OverflowError on a surface is a worse failure than a
    saturated probability."""
    p = _sigmoid(z)
    assert 0.0 <= p <= 1.0
    assert math.isfinite(p)
    if z > 0:
        assert p > 0.5
    elif z < 0:
        assert p < 0.5
    else:
        assert p == 0.5


def test_the_sigmoid_is_symmetric():
    for z in (0.3, 2.0, 17.0):
        assert _sigmoid(z) + _sigmoid(-z) == pytest.approx(1.0, abs=1e-15)


def test_favoured_reads_the_probability_not_the_logit_sign():
    base = _scorer().predict(_context(), TARGET, at(9))
    assert base.favoured == ("home" if base.home_win_probability > 0.5 else "away")
    tilted = Prediction(
        game_id="x", as_of=at(9), model_version="v", home_win_probability=0.5,
        baseline_logit=0.0, features={}, contributions={},
    )
    assert tilted.favoured == "away", "exactly 0.5 is not a home pick"
