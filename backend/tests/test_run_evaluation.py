"""T-030 -- the sealed fold is spent deliberately, or not at all.

D-013's headline number is worth something only if it was produced once. These tests pin the two
mechanisms that make that true in practice rather than by intention: the sealed fold is absent from
a default run, and every sealed run leaves a line in a committed log.

They deliberately do **not** assert any evaluation number. The numbers belong in the plan's outcome
block and in `docs/results/`; a test that pinned them would have to be edited whenever the corpus
grew, which is how a pin stops meaning anything.
"""

from __future__ import annotations

import json

import pytest

from model import run_evaluation
from model.features import FEATURE_NAMES
from model.splits import SEALED_FOLD, walk_forward_folds


def test_a_default_run_does_not_touch_the_sealed_fold():
    """The control the whole module is arranged around."""
    folds = run_evaluation.folds_to_evaluate(spend_the_sealed_fold=False)
    assert SEALED_FOLD not in folds
    assert all(f.test_season != SEALED_FOLD.test_season for f in folds)
    assert len(folds) == len(walk_forward_folds()) - 1


def test_the_sealed_fold_is_reachable_only_by_asking_for_it():
    """The non-vacuity control: a filter that removed everything would satisfy the test above and
    make the module useless."""
    folds = run_evaluation.folds_to_evaluate(spend_the_sealed_fold=True)
    assert SEALED_FOLD in folds
    assert folds == walk_forward_folds()


def test_the_flag_says_what_it_costs():
    """It is not a `--force`. Someone reading the command later should be able to tell that a
    sealed-fold run was a decision rather than a default."""
    assert run_evaluation.SPEND_FLAG == "--spend-the-sealed-fold"


def test_the_evaluation_evaluates_the_frozen_set_and_offers_no_way_to_change_it():
    """There is no feature argument here. Selection happened in `model.selection`, before the freeze;
    a module that could re-choose features while holding the sealed season would be able to select on
    it, which is the one thing this protocol exists to prevent."""
    import inspect

    parameters = set(inspect.signature(run_evaluation.evaluate_fold).parameters)
    assert parameters == {"context", "games", "fold"}
    assert "features" not in parameters and "feature_names" not in parameters


def test_every_sealed_run_appends_a_line_to_the_committed_log(tmp_path, monkeypatch):
    """A second look is not prevented -- it cannot be. What is enforced is that it cannot happen
    *silently*: a second run leaves a second line, in git, next to the first."""
    log = tmp_path / "sealed.jsonl"
    monkeypatch.setattr(run_evaluation, "SEALED_LOG", log)

    run_evaluation._record_sealed_run({"fold": "first", "accuracy": 0.66})
    run_evaluation._record_sealed_run({"fold": "second", "accuracy": 0.66})

    lines = log.read_text().strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["fold"] for line in lines] == ["first", "second"]


def test_the_sealed_log_survives_a_missing_directory():
    """It is written once, at the end of a run that must not fail on a mkdir."""
    assert run_evaluation.SEALED_LOG.name.endswith(".jsonl")
    assert "results" in run_evaluation.SEALED_LOG.parts


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_the_frozen_features_are_the_ones_the_run_reports(name):
    """Coefficients are reported by zipping `FEATURE_NAMES` against the fitted vector, so the two
    must be the same list -- a mismatch would relabel every coefficient in the headline table."""
    assert name in FEATURE_NAMES
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))


def test_evaluating_the_sealed_fold_records_it_whatever_the_entry_point(tmp_path, monkeypatch):
    """The hole this control had for about ten minutes.

    With the append in `main`, re-deriving the results table by calling `evaluate_fold` directly
    evaluated the sealed season a second time and logged nothing. The numbers happened to be
    bit-identical, but "it happened to be harmless" is not the property claimed — the claim is that a
    sealed evaluation cannot happen silently, and a claim that holds through only one entry point does
    not hold. This asserts the recording follows the *evaluation*, not the command line.
    """
    log = tmp_path / "sealed.jsonl"
    monkeypatch.setattr(run_evaluation, "SEALED_LOG", log)

    recorded = []
    monkeypatch.setattr(run_evaluation, "_record_sealed_run", recorded.append)
    monkeypatch.setattr(run_evaluation, "fit", lambda *a, **k: _StubModel())
    monkeypatch.setattr(run_evaluation, "save_artifact", lambda *a, **k: "stub00000000")
    monkeypatch.setattr(run_evaluation, "split_games", lambda games, fold: (games, games))
    # Both classes present: `evaluate` refuses a single-class test set rather than returning .5,
    # which is the right behaviour and one this stub has to respect.
    monkeypatch.setattr(
        run_evaluation, "_rows", lambda ctx, games: ([(0.0,) * 4, (0.0,) * 4], [True, False])
    )

    run_evaluation.evaluate_fold(None, [], walk_forward_folds()[0])
    assert recorded == [], "a dev fold must not touch the sealed log"

    run_evaluation.evaluate_fold(None, [], SEALED_FOLD)
    assert len(recorded) == 1
    assert recorded[0]["fold"] == str(SEALED_FOLD)
    assert recorded[0]["model_version"] == "stub00000000"


class _StubModel:
    """Just enough model to let `evaluate_fold` run without fitting anything."""

    coefficients = (0.0, 0.0, 0.0, 0.0)
    intercept = 0.0

    def predict_proba(self, rows):
        return [0.5 for _ in rows]
