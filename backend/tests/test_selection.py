"""T-030 -- feature selection on the dev seasons, and the guard that keeps 2026 out of it.

These tests exist to pin the *protocol*, not the numbers. The numbers are measured once, reported in
the plan, and are not something a test should assert -- a test that pinned them would have to be
edited every time the corpus grew, which is exactly how a pin stops meaning anything.

What is worth pinning is that selection cannot read the sealed season, and that the decision rule is
the one that was written down before the ablation ran. `model/selection.py` was committed with its
rule and these tests **before** the first result existed; git history is the proof, and this file is
what makes a later edit to the rule visible as an edit to the rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from model import selection
from model.features import FEATURE_NAMES
from model.splits import SEALED_FOLD


def test_the_dev_folds_never_include_the_sealed_season():
    """The whole point of the module being separate from `run_evaluation`."""
    assert SEALED_FOLD not in selection.DEV_FOLDS
    for fold in selection.DEV_FOLDS:
        assert SEALED_FOLD.test_season not in fold.seasons, fold
        assert fold.test_season in selection.DEV_SEASONS, fold


def test_the_dev_folds_are_the_two_folds_that_test_on_dev_seasons():
    assert [f.test_season for f in selection.DEV_FOLDS] == [2024, 2025]


def test_the_guard_fires_if_the_sealed_fold_ever_reaches_the_dev_set(monkeypatch):
    """The non-vacuity control. `WALK_FORWARD_FOLDS` is a literal a future edit could extend, and the
    failure mode is silent — a fold testing on 2026 produces a perfectly ordinary-looking number that
    has spent the sealed season."""
    monkeypatch.setattr(selection, "DEV_SEASONS", (2024, 2025, SEALED_FOLD.test_season))
    with pytest.raises(selection.SelectionError, match="sealed fold"):
        selection._dev_folds()


def test_the_guard_fires_when_no_fold_tests_on_a_dev_season(monkeypatch):
    monkeypatch.setattr(selection, "DEV_SEASONS", (1999,))
    with pytest.raises(selection.SelectionError, match="no fold tests"):
        selection._dev_folds()


def test_the_sealed_season_is_not_a_literal_anywhere_in_the_module():
    """Mechanical, in the spirit of T-024's D-039 check: a comment saying "this never reads 2026" is
    not a control. If the sealed season's number appears in this file at all, something reads it."""
    source = Path(selection.__file__).read_text()
    tree = ast.parse(source)
    sealed = SEALED_FOLD.test_season
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == sealed
    ]
    assert not offenders, f"the sealed season {sealed} appears as a literal on line(s) {offenders}"


# --- the pre-registered rule ----------------------------------------------------------------------


def test_the_decision_rule_is_the_one_that_was_written_down():
    """Pinned so that changing the rule is an edit to the rule, visible in a diff, rather than a
    number quietly moving after the results came in. A threshold chosen after seeing the answer is
    not a threshold."""
    assert selection.DROP_CANDIDATES == ("travel_diff", "altitude")
    assert selection.MATERIALITY_THRESHOLD == 0.0020


def test_the_drop_candidates_are_real_features():
    assert set(selection.DROP_CANDIDATES) <= set(FEATURE_NAMES)


def test_removal_is_the_prior_when_the_cost_is_below_the_threshold():
    assert selection.decide(0.7300, 0.7285) is True   # costs .0015, under .0020
    assert selection.decide(0.7300, 0.7300) is True   # costs nothing
    assert selection.decide(0.7300, 0.7310) is True   # removal *helps*


def test_removal_is_reconsidered_when_the_cost_reaches_the_threshold():
    """The escape hatch. Without this the rule would be 'drop regardless', and the ablation would be
    ceremony rather than a measurement that can change the outcome."""
    assert selection.decide(0.7300, 0.7280) is False  # costs exactly .0020
    assert selection.decide(0.7300, 0.7200) is False  # costs .0100


def test_projection_columns_line_up_with_the_names_that_go_to_the_fit():
    """The invariant that actually matters.

    A fitted artifact stores coefficients as `zip(feature_names, coefficients)`, so a projection
    ordered differently from the names passed alongside it would mislabel every coefficient in the
    reduced model — and the waterfall would attribute each factor's contribution to its neighbour.
    One `keep` goes to both, which is what makes them agree; this pins that, deliberately using an
    ordering no real caller produces.
    """
    row = tuple(float(i) for i in range(len(FEATURE_NAMES)))
    keep = (FEATURE_NAMES[3], FEATURE_NAMES[0])  # deliberately not FEATURE_NAMES order
    projected = selection._project([row], keep)[0]
    assert projected == (3.0, 0.0)
    # ...which is to say: column j of the projection is the feature named `keep[j]`.
    for j, name in enumerate(keep):
        assert projected[j] == float(FEATURE_NAMES.index(name))


def test_the_candidate_sets_real_callers_build_are_in_feature_names_order():
    """`_without` is what every candidate in `run` is built from, and it preserves the canonical
    order — so the out-of-order case above stays hypothetical rather than becoming a live path."""
    for name in FEATURE_NAMES:
        kept = selection._without(name)
        assert list(kept) == [f for f in FEATURE_NAMES if f != name]
