"""The one scoring interface (T-031, D-011/D-040) -- used by the job, the API and nothing else.

    scorer = Scorer.load(Path("models/logistic-2026.json"))
    prediction = scorer.predict(context, matchup, as_of)

## Why this exists at all

D-011's anti-skew mechanism was that training and inference call **one** feature function, so there
is no second implementation to drift from. T-031 extends the same argument one layer out: the
scheduled job and the API both need a probability, a feature vector, a contribution breakdown and a
model version, and if each built its own the two would eventually disagree about what the model said.
A user would then see one number on a page and a different one in the accuracy record, and neither
would be wrong in a way anybody could point at.

So there is one `predict`, and both callers hold a `Scorer`.

## The contribution decomposition is exact, not attributed

A logistic model's log-odds are additive by construction:

    z = intercept + SUM_j coef_j * (x_j - mean_j) / std_j
    p = sigmoid(z)

so each feature's contribution to `z` is just its own term, and the terms sum to `z` exactly. That is
the whole reason D-040's waterfall can be a *decomposition* rather than an approximation, and the
reason D-023 kept the model class linear: a gradient-boosted model would need SHAP to estimate what
this gives by arithmetic. `sigmoid(baseline_logit + sum(contributions)) == probability` is asserted
as an identity, not to floating-point tolerance -- it is the same arithmetic, so it is exact.

Contributions are in **log-odds**, not probability. A contribution of +0.5 does not mean "+50%", and
anything rendering these has to say what the unit is; the sum being the logit is what makes them
comparable to each other, which is what a waterfall is for.

## The feature contract is checked at load, not at use

An artifact fitted before T-030's freeze carries seven feature names; the current `FEATURE_NAMES`
has four. Scoring with a mismatched artifact would either raise deep inside the estimator or --
worse, if the counts happened to match -- silently pair each coefficient with the wrong feature and
return a plausible number. `Scorer.load` refuses it, once, at the boundary, naming both lists.

## Standard library only

D-016/D-021, and here it actually bites: this module is what the FastAPI service imports to score a
game. Everything it reaches -- `features`, `estimator`, `elo`, `availability`, `records` -- is
standard-library-only for that reason, and the served image carries no training dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .estimator import LogisticModel, load_artifact
from .features import FEATURE_NAMES, Context, Matchup, compute_features, to_vector


class PredictionError(ValueError):
    """Raised when a prediction cannot be produced as stated -- a mismatched artifact, most often."""


@dataclass(frozen=True, slots=True)
class Prediction:
    """One prediction, and everything needed to explain or reproduce it.

    Carries the feature vector as well as the probability because D-012 persists both: a prediction
    whose inputs were not recorded cannot be explained six months later, and "why did the model say
    that" is the question the whole game-detail surface exists to answer.
    """

    game_id: str
    as_of: datetime
    model_version: str
    home_win_probability: float
    baseline_logit: float
    features: dict[str, float]
    contributions: dict[str, float]

    @property
    def logit(self) -> float:
        """The total log-odds. Equal to `baseline_logit + sum(contributions)` by construction."""
        return self.baseline_logit + sum(self.contributions.values())

    @property
    def favoured(self) -> str:
        """`"home"` or `"away"`. At exactly 0.5 this says `"away"`, which is arbitrary and would be a
        poor thing to display -- a surface should show the probability, not a pick."""
        return "home" if self.home_win_probability > 0.5 else "away"


def _sigmoid(z: float) -> float:
    """Numerically stable logistic. The naive form overflows on large negative `z`."""
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


@dataclass(frozen=True, slots=True)
class Scorer:
    """A fitted model plus the version that identifies it. The unit both callers hold.

    Deliberately pairs the two. `model_version` is a content hash of the artifact (D-012 keys
    persisted predictions by it), and a `LogisticModel` on its own cannot say which one it is -- so
    a caller holding only the model would have to carry the version alongside and keep them in step
    by hand, which is a job that gets done wrongly eventually.
    """

    model: LogisticModel
    model_version: str

    @classmethod
    def load(cls, path: Path) -> Scorer:
        """Load an artifact and check it scores the feature set this code computes.

        `load_artifact` already refuses a file whose contents do not hash to its recorded version, so
        an edited artifact never reaches here. What this adds is the *contract* check: an artifact
        fitted before T-030's freeze carries seven feature names against today's four.
        """
        model, document = load_artifact(path)
        if tuple(model.feature_names) != FEATURE_NAMES:
            raise PredictionError(
                f"artifact at {path} scores {list(model.feature_names)} but this code computes "
                f"{list(FEATURE_NAMES)} -- refusing to score with a mismatched feature set. If the "
                "counts happened to match, every coefficient would be paired with the wrong feature "
                "and the result would look perfectly ordinary."
            )
        return cls(model=model, model_version=document["model_version"])

    def predict(self, context: Context, target: Matchup, as_of: datetime) -> Prediction:
        """**The interface.** Probability, feature vector and contribution breakdown for one game.

        Args:
            context: the sources, as a `features.Context`. Not pre-filtered -- the as-of filter is
                `features`' job and runs inside `compute_features`.
            target: the pre-game `Matchup`.
            as_of: the prediction moment, timezone-aware and at or before tip-off. `compute_features`
                refuses anything later, which is what stops this becoming a way to re-predict a
                finished game (D-010).

        Returns:
            A `Prediction` whose contributions sum to `logit - baseline_logit` exactly.
        """
        features = compute_features(context, target, as_of)
        vector = to_vector(features)

        model = self.model
        contributions = {
            name: coefficient * ((value - mean) / std)
            for name, value, mean, std, coefficient in zip(
                model.feature_names, vector, model.means, model.stds, model.coefficients,
                strict=True,
            )
        }
        z = model.intercept + sum(contributions.values())

        return Prediction(
            game_id=target.game_id,
            as_of=as_of,
            model_version=self.model_version,
            home_win_probability=_sigmoid(z),
            baseline_logit=model.intercept,
            features=features,
            contributions=contributions,
        )
