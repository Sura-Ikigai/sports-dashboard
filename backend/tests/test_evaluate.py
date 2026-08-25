"""Tests for the evaluation metrics (T-008, `backend/model/evaluate.py`).

Standard library only, so they run in CI.

Every metric is asserted against a value computed **by hand** on one small labelled set, written as a
literal with its derivation in the comment. That is T-008's acceptance and it is also the point: a
disappointing headline number in T-009 has to be trustworthy rather than possibly a metric bug, and a
test that re-derives a metric with the same arithmetic as the implementation cannot tell the two
apart (the F-055 lesson).

The worked set — 5 games, 3 won by the home side:

    y_true    True    False   True    True    False
    y_prob    0.9     0.2     0.6     0.4     0.7
"""

import math

import pytest

from model.evaluate import (
    ACCURACY_TARGET,
    BASE_RATE,
    CalibrationBin,
    ConstantComparison,
    EvaluationError,
    accuracy,
    calibration_curve,
    compare_to_constant,
    evaluate,
    log_loss,
    roc_auc,
)

Y_TRUE = [True, False, True, True, False]
Y_PROB = [0.9, 0.2, 0.6, 0.4, 0.7]

# accuracy @0.5:  predictions 1,0,1,0,1  vs  1,0,1,1,0  -> right on games 1,2,3 -> 3/5
EXPECTED_ACCURACY = 0.6

# log loss: -(1/5)[ln.9 + ln.8 + ln.6 + ln.4 + ln.3]
#         = (0.10536052 + 0.22314355 + 0.51082562 + 0.91629073 + 1.20397280) / 5
EXPECTED_LOG_LOSS = 0.5919186453876236

# AUC: positives {0.9, 0.6, 0.4}, negatives {0.2, 0.7}; of the 6 pairs the positive wins 4
#      (0.9>0.2, 0.9>0.7, 0.6>0.2, 0.4>0.2) -> 4/6
EXPECTED_AUC = 2 / 3


# --- the hand-computed values --------------------------------------------------------------------


def test_accuracy_matches_the_hand_computed_value():
    assert accuracy(Y_TRUE, Y_PROB) == pytest.approx(EXPECTED_ACCURACY)


def test_log_loss_matches_the_hand_computed_value():
    assert log_loss(Y_TRUE, Y_PROB) == pytest.approx(EXPECTED_LOG_LOSS)


def test_roc_auc_matches_the_hand_computed_value():
    assert roc_auc(Y_TRUE, Y_PROB) == pytest.approx(EXPECTED_AUC)


def test_calibration_curve_matches_the_hand_computed_bins():
    """0.2→bin 2, 0.4→bin 4, 0.6→bin 6, 0.7→bin 7, 0.9→bin 9; every other bin empty."""
    curve = calibration_curve(Y_TRUE, Y_PROB, bins=10)
    assert len(curve) == 10
    occupied = {i: b for i, b in enumerate(curve) if b.count}
    assert set(occupied) == {2, 4, 6, 7, 9}
    assert occupied[9] == CalibrationBin(0.9, 1.0, 1, 0.9, 1.0)   # the 0.9 call, won
    assert occupied[2] == CalibrationBin(0.2, 0.3, 1, 0.2, 0.0)   # the 0.2 call, lost
    assert occupied[4] == CalibrationBin(0.4, 0.5, 1, 0.4, 1.0)   # the 0.4 call, won anyway
    assert occupied[7] == CalibrationBin(0.7, 0.8, 1, 0.7, 0.0)   # the 0.7 call, lost


def test_empty_bins_are_returned_as_empty_rather_than_omitted_or_zeroed():
    """An empty bin means the model never made a call in that range — information, not a zero. With
    shrinkage toward a 0.5-ish prior the extreme bins are often empty, and that is a finding."""
    curve = calibration_curve(Y_TRUE, Y_PROB, bins=10)
    assert curve[0] == CalibrationBin(0.0, 0.1, 0, None, None)
    assert all(b.mean_predicted is None for b in curve if b.count == 0)


def test_a_probability_of_exactly_one_lands_in_the_last_bin():
    """`int(1.0 * bins)` is `bins`, one past the end — an off-by-one that would drop the most
    confident call in the set."""
    curve = calibration_curve([True], [1.0], bins=10)
    assert curve[-1].count == 1
    assert sum(b.count for b in curve) == 1


# --- the comparator, and its boundary behaviour ---------------------------------------------------


def test_compare_to_constant_against_a_hand_computable_constant():
    """At 0.5 the constant predictor's log loss is ln 2 for any labels, which makes this checkable
    without trusting the implementation twice."""
    comparison = compare_to_constant(Y_TRUE, Y_PROB, constant=0.5)
    assert comparison.constant_log_loss == pytest.approx(math.log(2))
    assert comparison.model_log_loss == pytest.approx(EXPECTED_LOG_LOSS)
    assert comparison.improvement == pytest.approx(math.log(2) - EXPECTED_LOG_LOSS)
    assert comparison.beats_constant is True


def test_a_model_that_merely_reproduces_the_constant_does_not_beat_it():
    """THE boundary. A tie is not a win: a model that has learned only the base rate has demonstrated
    nothing, and that is precisely the null D-008 tests against."""
    tie = compare_to_constant(Y_TRUE, [BASE_RATE] * 5, constant=BASE_RATE)
    assert tie.model_log_loss == pytest.approx(tie.constant_log_loss)
    assert tie.improvement == pytest.approx(0.0)
    assert tie.beats_constant is False


def test_the_boundary_is_strict_on_both_sides():
    barely_better = ConstantComparison(0.6931, 0.6931472, 0.5)
    barely_worse = ConstantComparison(0.6932, 0.6931472, 0.5)
    assert barely_better.beats_constant is True
    assert barely_worse.beats_constant is False


def test_the_best_possible_constant_is_the_observed_base_rate():
    """A property, not a fixture: if some other constant scored better, the comparator would be
    testing against a straw man rather than the strongest constant predictor available."""
    observed = sum(Y_TRUE) / len(Y_TRUE)  # 0.6
    best = log_loss(Y_TRUE, [observed] * 5)
    for other in (0.3, 0.45, 0.5, 0.55534, 0.7, 0.9):
        assert best <= log_loss(Y_TRUE, [other] * 5) + 1e-12, other


def test_a_constant_outside_the_open_unit_interval_is_refused():
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(EvaluationError, match="strictly inside"):
            compare_to_constant(Y_TRUE, Y_PROB, constant=bad)


# --- metrics that must refuse rather than return a sentinel ---------------------------------------


def test_auc_refuses_a_single_class_set_rather_than_returning_one_half():
    """0.5 is the conventional answer and it is indistinguishable from 'no signal' — the very
    conclusion T-009 exists to draw honestly."""
    for labels in ([True, True, True], [False, False]):
        with pytest.raises(EvaluationError, match="undefined with one class"):
            roc_auc(labels, [0.5] * len(labels))


def test_auc_gives_ties_half_credit():
    """A shrunk feature set emits repeated probabilities, so tie handling is not academic: without
    it, AUC would depend on how the sort broke them."""
    assert roc_auc([True, False], [0.5, 0.5]) == pytest.approx(0.5)
    assert roc_auc([True, False], [0.9, 0.1]) == pytest.approx(1.0)
    assert roc_auc([True, False], [0.1, 0.9]) == pytest.approx(0.0)
    # one clean pair plus one tied pair -> (1 + 0.5) / 2
    assert roc_auc([True, True, False, False], [0.9, 0.5, 0.5, 0.1]) == pytest.approx(0.875)


def test_log_loss_clips_rather_than_returning_infinity():
    """One confidently wrong call would otherwise be infinite loss and swamp an entire fold."""
    loss = log_loss([False], [1.0])
    assert math.isfinite(loss)
    assert loss > 30  # ~ -ln(1e-15)
    assert log_loss([True], [1.0]) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize(
    ("labels", "probs", "match"),
    [
        ([True], [0.5, 0.5], "y_true has 1 items"),
        ([], [], "empty input"),
        ([True], [1.5], r"outside \[0, 1\]"),
        ([True], [float("nan")], "not a finite number"),
        ([True], [float("inf")], "not a finite number"),
    ],
    ids=["length-mismatch", "empty", "out-of-range", "nan", "inf"],
)
def test_malformed_input_is_refused(labels, probs, match):
    with pytest.raises(EvaluationError, match=match):
        accuracy(labels, probs)


def test_accuracy_counts_a_probability_exactly_at_the_threshold_as_a_home_pick():
    """A real choice, not an accident: two teams with no prior games produce every difference feature
    at 0, so exact 0.5s occur early in a season."""
    assert accuracy([True], [0.5]) == 1.0
    assert accuracy([False], [0.5]) == 0.0


# --- the paired verdict ---------------------------------------------------------------------------


def test_evaluate_bundles_every_metric_consistently():
    result = evaluate(Y_TRUE, Y_PROB, constant=0.5)
    assert result.n_games == 5
    assert result.accuracy == pytest.approx(EXPECTED_ACCURACY)
    assert result.log_loss == pytest.approx(EXPECTED_LOG_LOSS)
    assert result.roc_auc == pytest.approx(EXPECTED_AUC)
    assert result.comparison.constant_log_loss == pytest.approx(math.log(2))
    assert sum(b.count for b in result.calibration) == 5


@pytest.mark.parametrize(
    ("acc", "beats", "expected"),
    [(0.62, True, True), (0.62, False, False), (0.61999, True, False), (0.61999, False, False)],
    ids=["both", "accuracy-only", "calibration-only", "neither"],
)
def test_the_ship_criterion_requires_BOTH_halves(acc, beats, expected):
    """D-008 is paired, and an `or` here would be the single most consequential mutation in the
    project: it would declare a badly calibrated model shippable on accuracy alone, which is the
    exact failure D-008 was written to prevent."""
    from model.evaluate import Evaluation

    comparison = ConstantComparison(0.6 if beats else 0.8, 0.7, 0.5)
    assert comparison.beats_constant is beats
    result = Evaluation(
        n_games=1, accuracy=acc, log_loss=0.6, roc_auc=0.7, comparison=comparison, calibration=[]
    )
    assert result.meets_ship_criterion is expected


def test_the_accuracy_target_and_base_rate_are_the_pinned_ones():
    """Guards the constants themselves — D-008's 62% and D-026's 55.534%, the latter measured on the
    curated 6,605-game corpus rather than D-007's pre-curation 55.556%."""
    assert ACCURACY_TARGET == 0.62
    assert BASE_RATE == 0.55534


def test_auc_agrees_with_brute_force_pair_counting_on_tie_heavy_random_sets():
    """An independent check on the one metric whose implementation is not obvious by inspection.

    `roc_auc` uses the Mann-Whitney rank formulation; this counts all positive/negative pairs
    directly, giving ties half credit. The two share no arithmetic, so agreement is evidence rather
    than a tautology — which the hand-computed fixtures above, being single cases, cannot be.
    Probabilities are drawn from a deliberately coarse set so ties are common: that is the realistic
    shape for a shrunk feature set, and the case a naive threshold sweep gets wrong.
    """
    import random

    def brute_force(labels, probs):
        pos = [q for a, q in zip(labels, probs, strict=True) if a]
        neg = [q for a, q in zip(labels, probs, strict=True) if not a]
        wins = sum(
            1.0 if a > b else 0.5 if a == b else 0.0
            for a in pos
            for b in neg
        )
        return wins / (len(pos) * len(neg))

    rng = random.Random(7)
    compared = 0
    for _ in range(400):
        n = rng.randint(2, 40)
        probs = [rng.choice([0.1, 0.3, 0.5, 0.5, 0.5, 0.7, 0.9]) for _ in range(n)]
        labels = [rng.random() < 0.55 for _ in range(n)]
        if all(labels) or not any(labels):
            continue  # AUC undefined; covered by its own test
        assert roc_auc(labels, probs) == pytest.approx(brute_force(labels, probs)), (labels, probs)
        compared += 1
    assert compared > 300, f"only {compared} usable sets — the fixture stopped exercising ties"
