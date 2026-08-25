"""Evaluation metrics for the Phase 1 analytical core (T-008).

Pure functions over `(y_true, y_prob)`. Standard library only (D-021), like `features`, `corpus` and
`splits` — no numpy, no scikit-learn — so these run in the gate, and so the eventual serving image
inherits nothing from them.

## What this exists to decide

D-008's ship criterion is **paired**: ≥62% accuracy on the sealed fold **and** log loss beating a
constant base-rate predictor. Accuracy alone cannot validate the product's central promise — that a
55% call and an 80% call mean genuinely different things — and a model can reach 65% accuracy while
being badly calibrated. So the comparator against a constant predictor is not a nicety here; it is
half the gate, which is why `compare_to_constant` returns the two log losses alongside the verdict
rather than just a boolean.

The constant to compare against is **0.55534** (D-026), the home-win rate of the curated 6,605-game
modeling corpus — not D-007's 0.55556, which was measured before F-042's ten All-Star exhibitions
were removed. The difference is 0.02pp and changes no conclusion, but T-010 requires every reported
number to be reproducible from committed code, so the constant a report quotes must be the one the
report's own corpus produces.

## Where these differ from a library implementation, deliberately

  - **Nothing returns a silent sentinel.** `roc_auc` on a single-class set is undefined, and the
    conventional 0.5 is indistinguishable from "the model has no signal" — the exact reading T-009
    is trying to make. It raises.
  - **Log loss clips**, because a confident-and-wrong probability of exactly 0 or 1 is infinite loss
    and would swamp a fold with one bad prediction. `_EPSILON` is the clip, applied symmetrically.
  - **`roc_auc` handles ties properly** (Mann-Whitney U with half-credit), rather than sweeping
    thresholds. A model that emits the same probability for many games — which a shrunk feature set
    does, especially early in a season — would otherwise score arbitrarily.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

#: Probabilities are clipped into [_EPSILON, 1 - _EPSILON] before taking a log.
_EPSILON: float = 1e-15

#: The home-win rate of the curated modeling corpus (D-026). The constant predictor D-008 compares
#: against. NOT D-007's 0.55556, which predates F-042's exhibition removal.
BASE_RATE: float = 0.55534

#: D-008's accuracy half.
ACCURACY_TARGET: float = 0.62


class EvaluationError(ValueError):
    """Raised when a metric is undefined for the input given, rather than returning a sentinel."""


def _validate(y_true: Sequence[bool], y_prob: Sequence[float]) -> None:
    if len(y_true) != len(y_prob):
        raise EvaluationError(f"y_true has {len(y_true)} items, y_prob has {len(y_prob)}")
    if not y_true:
        raise EvaluationError("empty input — no metric is defined over zero games")
    for p in y_prob:
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not math.isfinite(p):
            raise EvaluationError(f"probability {p!r} is not a finite number")
        if not 0.0 <= p <= 1.0:
            raise EvaluationError(f"probability {p!r} is outside [0, 1]")


def accuracy(
    y_true: Sequence[bool], y_prob: Sequence[float], *, threshold: float = 0.5
) -> float:
    """Share of games whose predicted winner was right.

    A probability exactly at `threshold` counts as a home prediction — stated because it is a real
    choice, and with a shrunk feature set exact 0.5s occur (two teams with no prior games produce
    every difference feature at 0, so only the intercept separates them).
    """
    _validate(y_true, y_prob)
    hits = sum(1 for actual, p in zip(y_true, y_prob, strict=True) if (p >= threshold) == bool(actual))
    return hits / len(y_true)


def log_loss(y_true: Sequence[bool], y_prob: Sequence[float]) -> float:
    """Mean negative log likelihood — the calibration half of D-008's criterion.

    Lower is better. Unlike accuracy this reads the *confidence*, so a model that is right often but
    overconfident scores worse than one that is right as often and honest about it.
    """
    _validate(y_true, y_prob)
    total = 0.0
    for actual, p in zip(y_true, y_prob, strict=True):
        clipped = min(max(p, _EPSILON), 1.0 - _EPSILON)
        total -= math.log(clipped) if actual else math.log(1.0 - clipped)
    return total / len(y_true)


def roc_auc(y_true: Sequence[bool], y_prob: Sequence[float]) -> float:
    """Probability that a randomly chosen won game is scored above a randomly chosen lost one.

    Computed as the Mann-Whitney U statistic over average ranks, so ties get half credit — a model
    emitting one repeated probability scores 0.5 for those pairs rather than 0 or 1 depending on how
    a sort happened to break them.

    Raises on a single-class input rather than returning 0.5: AUC is genuinely undefined there, and
    0.5 would read as "no signal", which is the conclusion T-009 exists to draw honestly.
    """
    _validate(y_true, y_prob)
    positives = sum(1 for a in y_true if a)
    negatives = len(y_true) - positives
    if positives == 0 or negatives == 0:
        raise EvaluationError(
            f"AUC is undefined with one class only ({positives} won, {negatives} lost) — refusing "
            "to return 0.5, which would read as 'no signal' rather than 'not measurable'"
        )

    order = sorted(range(len(y_prob)), key=lambda i: y_prob[i])
    ranks = [0.0] * len(y_prob)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and y_prob[order[j + 1]] == y_prob[order[i]]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0  # 1-based, averaged across the tie group
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1

    rank_sum = sum(r for r, a in zip(ranks, y_true, strict=True) if a)
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    """One bucket of a calibration curve. `mean_predicted` and `observed_rate` are None when empty —
    an empty bin is information (the model never made a call in that range), not a zero."""

    lower: float
    upper: float
    count: int
    mean_predicted: float | None
    observed_rate: float | None


def calibration_curve(
    y_true: Sequence[bool], y_prob: Sequence[float], *, bins: int = 10
) -> list[CalibrationBin]:
    """Is a 70% call right about 70% of the time?

    Every bin is returned, including empty ones, so a caller can see *where* the model declines to
    predict — with shrinkage toward a 0.5-ish prior, the extreme bins are often empty, and that is a
    finding rather than a gap. The last bin includes 1.0.
    """
    _validate(y_true, y_prob)
    if bins < 1:
        raise EvaluationError(f"bins must be >= 1, got {bins}")

    buckets: list[list[tuple[bool, float]]] = [[] for _ in range(bins)]
    for actual, p in zip(y_true, y_prob, strict=True):
        index = min(int(p * bins), bins - 1)  # p == 1.0 belongs in the last bin, not a new one
        buckets[index].append((bool(actual), p))

    out = []
    for index, bucket in enumerate(buckets):
        lower, upper = index / bins, (index + 1) / bins
        if not bucket:
            out.append(CalibrationBin(lower, upper, 0, None, None))
            continue
        out.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                count=len(bucket),
                mean_predicted=sum(p for _, p in bucket) / len(bucket),
                observed_rate=sum(1 for a, _ in bucket if a) / len(bucket),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class ConstantComparison:
    """The calibration half of D-008, with its inputs kept alongside the verdict.

    T-010 requires every number to be reproducible, and a bare boolean is not: the two losses and the
    constant used are what make `beats_constant` auditable.
    """

    model_log_loss: float
    constant_log_loss: float
    constant: float

    @property
    def beats_constant(self) -> bool:
        """Strictly better. A tie is not a win — a model that merely matches the base rate has
        demonstrated nothing, which is exactly the null D-008 is testing against."""
        return self.model_log_loss < self.constant_log_loss

    @property
    def improvement(self) -> float:
        """How much log loss the model saves per game. Positive means better."""
        return self.constant_log_loss - self.model_log_loss


def compare_to_constant(
    y_true: Sequence[bool], y_prob: Sequence[float], *, constant: float = BASE_RATE
) -> ConstantComparison:
    """Compare the model's log loss against a predictor that always says `constant` (D-008/D-026).

    Note the constant predictor is evaluated on the *same* labels, so this asks the right question:
    does knowing the teams beat knowing only that home teams win about 55.5% of the time?
    """
    _validate(y_true, y_prob)
    if not 0.0 < constant < 1.0:
        raise EvaluationError(f"constant must be strictly inside (0, 1), got {constant}")
    return ConstantComparison(
        model_log_loss=log_loss(y_true, y_prob),
        constant_log_loss=log_loss(y_true, [constant] * len(y_true)),
        constant=constant,
    )


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Everything T-009 reports for one fold, and the paired verdict D-008 asks for."""

    n_games: int
    accuracy: float
    log_loss: float
    roc_auc: float
    comparison: ConstantComparison
    calibration: list[CalibrationBin]

    @property
    def meets_ship_criterion(self) -> bool:
        """**Both** halves of D-008, never either alone."""
        return self.accuracy >= ACCURACY_TARGET and self.comparison.beats_constant


def evaluate(
    y_true: Sequence[bool],
    y_prob: Sequence[float],
    *,
    constant: float = BASE_RATE,
    bins: int = 10,
) -> Evaluation:
    """Every metric for one fold, in one pass, plus D-008's paired verdict."""
    _validate(y_true, y_prob)
    return Evaluation(
        n_games=len(y_true),
        accuracy=accuracy(y_true, y_prob),
        log_loss=log_loss(y_true, y_prob),
        roc_auc=roc_auc(y_true, y_prob),
        comparison=compare_to_constant(y_true, y_prob, constant=constant),
        calibration=calibration_curve(y_true, y_prob, bins=bins),
    )
