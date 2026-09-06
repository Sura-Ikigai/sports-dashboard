"""T-033 -- the scoring contract, from the Python side.

`frontend/lib/scoring/contract.test.ts` holds the TypeScript to the committed grid. This file holds
the **grid** to Python: it regenerates every case from `prediction.decompose` and fails if the
committed file no longer matches.

Together those are the whole justification for a second scorer existing at all, and the pair matters
more than either half:

  - TypeScript drifts -> the frontend job goes red.
  - Python drifts -> this goes red, because the committed file is now stale.

The second is the one that is easy to get wrong. If this file merely *read* the vectors and checked
they were self-consistent, a change to `decompose` would land green and the TypeScript would silently
become the odd one out -- with the committed file agreeing with neither. Regenerating is what makes
the file a statement about today's Python rather than about some Python that once existed.

And regenerating it to make a failing TypeScript test pass does not work, which is worth stating
because it is the obvious way to try to cheat this: the file is generated *from* Python, so it can
only ever encode Python's answer.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from model import contract
from model.contract import CONTRACT_VERSION, VECTORS_PATH, build, render
from model.estimator import LogisticModel
from model.prediction import decompose


@pytest.fixture(scope="module")
def committed() -> dict:
    assert VECTORS_PATH.exists(), (
        f"{VECTORS_PATH} is missing. It is committed on purpose -- the frontend CI job has no "
        "Python and cannot generate it. Run `PYTHONPATH=backend python -m model.contract`."
    )
    return json.loads(VECTORS_PATH.read_text())


# ── the committed file is current ─────────────────────────────────────────────


def test_the_committed_vectors_match_what_python_produces_today(committed):
    """**The check.** If `decompose` changes, this fails and the backend job goes red.

    Compared as rendered text rather than as parsed objects, so key order and float formatting are
    pinned too -- a diff a reviewer can read is worth more than one that says "the dicts differ".
    """
    regenerated = render(build())
    assert VECTORS_PATH.read_text() == regenerated, (
        "the committed contract vectors are stale -- `model.prediction.decompose` produces "
        "something different now. Regenerate with `PYTHONPATH=backend python -m model.contract`, "
        "and expect the TypeScript contract test to fail next unless it was changed to match."
    )


def test_the_check_above_would_notice_a_changed_arithmetic(monkeypatch):
    """Non-vacuity, and the only honest way to demonstrate it: change the arithmetic and confirm the
    regenerated document stops matching the committed one."""
    real = contract.decompose

    def perturbed(model, values):
        result = real(model, values)
        return type(result)(
            home_win_probability=result.home_win_probability,
            baseline_logit=result.baseline_logit + 1e-9,
            contributions=result.contributions,
        )

    monkeypatch.setattr(contract, "decompose", perturbed)
    assert render(build()) != VECTORS_PATH.read_text()


def test_floats_survive_the_round_trip_exactly(committed):
    """Why the comparison above can be exact rather than approximate.

    `json.dumps` writes a float with `repr`, which is the shortest string that reads back as the same
    double. So the committed file loses nothing, and a tolerance here would only hide a real change.
    """
    for case in committed["cases"][:20]:
        for name, value in case["expected"]["contributions"].items():
            assert json.loads(json.dumps(value)) == value, name


# ── the grid is worth agreeing on ─────────────────────────────────────────────


def test_the_grid_is_not_quietly_narrowed(committed):
    """A contract satisfied by three easy cases is not a contract. These floors are duplicated in
    the TypeScript test on purpose -- either side narrowing the grid should fail both."""
    assert len(committed["cases"]) >= 140
    assert len(committed["models"]) >= 6


def test_the_grid_reaches_both_ends_of_the_probability_range(committed):
    """Both branches of the stable sigmoid have to run, or the branch that is never exercised is the
    one that is wrong."""
    probabilities = [c["expected"]["home_win_probability"] for c in committed["cases"]]
    assert min(probabilities) < 1e-6
    assert max(probabilities) > 1 - 1e-6


def test_the_grid_contains_the_case_a_broken_scorer_would_still_pass(committed):
    """Every feature at its mean: every contribution is exactly zero and the probability is
    `sigmoid(intercept)`. A scorer that ignored the features entirely gets this one right, which is
    exactly why it must never be the only case -- and why it must be present, because it is also the
    only case that isolates the intercept."""
    at_the_mean = [c for c in committed["cases"] if c["id"].endswith("/at-the-mean")]
    assert len(at_the_mean) == len(committed["models"])
    for case in at_the_mean:
        assert all(v == 0.0 for v in case["expected"]["contributions"].values())
        assert case["expected"]["logit"] == case["expected"]["baseline_logit"]


def test_the_grid_carries_contributions_of_both_signs(committed):
    values = [v for c in committed["cases"] for v in c["expected"]["contributions"].values()]
    assert any(v > 0 for v in values)
    assert any(v < 0 for v in values)


def test_the_grid_contains_a_case_where_summation_order_changes_the_answer(committed):
    """What lets the TypeScript comparison be exact rather than approximate.

    Floating-point addition is not associative. Without a case where that bites, an implementation
    that summed the terms in a different order would agree everywhere and the exact-equality
    comparison would be decoration. `mixed-magnitude` exists for this: two enormous terms that
    cancel plus an ordinary one, where summing forward keeps the small term and summing backward
    loses it under the first partial sum.
    """
    order_sensitive = 0
    for case in committed["cases"]:
        terms = list(case["expected"]["contributions"].values())
        forward = 0.0
        for term in terms:
            forward += term
        backward = 0.0
        for term in reversed(terms):
            backward += term
        if forward != backward:
            order_sensitive += 1
    assert order_sensitive > 0, (
        "no case in the grid is sensitive to summation order, so the exact-equality comparison in "
        "contract.test.ts proves nothing about ordering"
    )


def test_every_model_in_the_grid_says_why_it_is_there(committed):
    """A fixture nobody can explain is a fixture nobody dares change."""
    for key, spec in committed["models"].items():
        assert spec["why"].strip(), key
        assert len(spec["feature_names"]) >= 1


def test_the_grid_brackets_the_shipped_model(committed):
    """The grid is synthetic on purpose -- `/models/` is gitignored so a model cannot be committed
    as if it were source (F-016, F-110), and a fixture carrying the artifact's parameters would be
    that model in git under another name.

    What makes synthetic acceptable is that it **brackets** the real one. The frozen artifact runs
    coefficients |0.13|-|0.71|, means within +-1.2, stds .089-90. Agreement across a wider range
    implies agreement at the point that ships.
    """
    coefficients, means, stds = [], [], []
    for spec in committed["models"].values():
        coefficients += [abs(v) for v in spec["coefficients"].values()]
        means += [abs(v) for v in spec["means"].values()]
        stds += list(spec["stds"].values())
    assert max(coefficients) >= 8.0
    assert max(means) >= 1000.0
    assert min(stds) <= 1e-3
    assert max(stds) >= 1e3


# ── the arithmetic itself ─────────────────────────────────────────────────────


def test_decompose_sums_to_the_logit_with_no_residual():
    """D-040's waterfall is a decomposition, not an attribution. Asserted as an identity, not to a
    tolerance: the same arithmetic produces both sides, so a residual means a term went missing."""
    model = LogisticModel(
        feature_names=("a", "b", "c"),
        coefficients=(0.7, -0.3, 0.15),
        intercept=0.25,
        means=(1.0, 0.0, -2.0),
        stds=(90.0, 0.35, 4.0),
    )
    result = decompose(model, [145.0, 1.0, 0.5])
    assert result.baseline_logit + sum(result.contributions.values()) == result.logit
    assert result.home_win_probability == 1.0 / (1.0 + math.exp(-result.logit))


def test_decompose_scores_a_model_whose_features_this_code_never_computes():
    """The grid's models carry names like `huge` and `only`. `decompose` takes the vector in
    `model.feature_names` order and never consults `FEATURE_NAMES`, which is what lets the contract
    stress the arithmetic outside the shipped feature set."""
    model = LogisticModel(
        feature_names=("only",), coefficients=(2.0,), intercept=-3.0, means=(0.5,), stds=(2.0,)
    )
    result = decompose(model, [4.5])
    assert result.contributions == {"only": 2.0 * ((4.5 - 0.5) / 2.0)}


def test_decompose_refuses_a_vector_of_the_wrong_length():
    """`zip(strict=True)`. A short vector silently scored against the first few coefficients would
    produce a plausible number from an incomplete model."""
    model = LogisticModel(
        feature_names=("a", "b"), coefficients=(1.0, 1.0), intercept=0.0,
        means=(0.0, 0.0), stds=(1.0, 1.0),
    )
    with pytest.raises(ValueError, match="argument"):
        decompose(model, [1.0])


def test_the_contract_version_is_pinned_on_both_sides(committed):
    """A shape change must break the TypeScript reader loudly rather than have it read missing
    fields as `undefined`. The TS side asserts the same number."""
    assert committed["contract_version"] == CONTRACT_VERSION
    ts_test = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "scoring" / "contract.test.ts"
    assert f"SUPPORTED_CONTRACT_VERSION = {CONTRACT_VERSION};" in ts_test.read_text()
