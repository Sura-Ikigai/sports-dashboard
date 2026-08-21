"""Tests for the logistic regression and the model artifact (T-009).

Standard library only, so they run in CI — and note what that means here: the fit is *ours*, so these
are the only mechanical guard on it. They are written to be independent of the implementation rather
than a restatement of it:

  - **coefficient recovery** — data generated FROM known coefficients must fit back to them;
  - **the vanishing gradient** — the property that *defines* the optimum, checkable without redoing
    the solve, so it cannot agree with a wrong implementation by sharing its arithmetic;
  - **tamper detection** on the artifact, whose whole point is that loading it cannot execute code.

A one-time cross-check against scikit-learn (max |Δcoef| 2.1e-08 over 6 trials, n=200..3000) is
recorded in the tracker as evidence. It is deliberately NOT a test: sklearn is not a dependency of
this module, and a test that skips in CI is how F-091 happened.
"""

import json
import math
import random

import pytest

from model.estimator import (
    ARTIFACT_FORMAT,
    DEFAULT_L2,
    EstimatorError,
    LogisticModel,
    artifact_payload,
    fit,
    load_artifact,
    model_version,
    save_artifact,
)

NAMES = ("home_advantage", "form_diff", "rest_diff", "point_diff_diff")


def _synthetic(n: int, coefs, intercept, *, seed: int = 3):
    """Rows drawn from a known logistic model — so the truth is known, not inferred."""
    rng = random.Random(seed)
    rows, labels = [], []
    for _ in range(n):
        row = [rng.gauss(0.0, 1.0) for _ in coefs]
        z = intercept + sum(c * v for c, v in zip(row, coefs, strict=True))
        labels.append(rng.random() < 1.0 / (1.0 + math.exp(-z)))
        rows.append(row)
    return rows, labels


def test_the_fit_recovers_coefficients_it_was_generated_from():
    """The strongest available check that does not need a second library: generate from known truth
    and fit back. Loose tolerance because 4,000 samples estimate a coefficient, they do not determine
    it — but a sign error, a swapped feature, or a missing standardization would blow past this."""
    truth, intercept = [1.2, -0.8, 0.5, 0.0], 0.3
    rows, labels = _synthetic(4000, truth, intercept)
    model = fit(rows, labels, NAMES, l2=1e-6)  # essentially unpenalized, so truth is the target
    for name, got, want in zip(NAMES, model.coefficients, truth, strict=True):
        assert got == pytest.approx(want, abs=0.15), f"{name}: {got} vs {want}"
    assert model.intercept == pytest.approx(intercept, abs=0.15)


def test_the_gradient_vanishes_at_the_returned_solution():
    """What *defines* an optimum. Independent of how the solve got there, so unlike a fixture it
    cannot be satisfied by an implementation that is confidently wrong."""
    rows, labels = _synthetic(800, [0.9, -0.4, 0.2, 0.6], -0.2)
    for l2 in (0.0, DEFAULT_L2, 10.0):
        model = fit(rows, labels, NAMES, l2=l2)
        assert model.gradient_norm(rows, labels, l2) < 1e-6, l2


def test_stronger_l2_shrinks_coefficients_toward_zero():
    rows, labels = _synthetic(600, [1.5, -1.2, 0.9, 0.4], 0.1)
    magnitudes = [
        sum(abs(c) for c in fit(rows, labels, NAMES, l2=l2).coefficients)
        for l2 in (0.01, 1.0, 100.0, 10000.0)
    ]
    assert magnitudes == sorted(magnitudes, reverse=True), magnitudes


def test_the_fit_is_deterministic():
    """No randomness, no shuffling, no seed — the same data must give bit-identical coefficients, or
    T-010's 'reproducible from committed code' is not true."""
    rows, labels = _synthetic(500, [0.7, 0.3, -0.5, 0.2], 0.0)
    a = fit(rows, labels, NAMES)
    b = fit(rows, labels, NAMES)
    assert a == b


def test_standardization_comes_from_the_data_it_was_fitted_on():
    """The subtle leak this project exists to avoid: means/stds computed over train+test would carry
    test distribution into the fit. They must be the training set's."""
    rows, labels = _synthetic(400, [1.0, 0.0, 0.0, 0.0], 0.0)
    model = fit(rows, labels, NAMES)
    for j in range(len(NAMES)):
        column = [r[j] for r in rows]
        expected_mean = sum(column) / len(column)
        assert model.means[j] == pytest.approx(expected_mean)


def test_a_constant_feature_does_not_blow_up_the_fit():
    """D-024 is not hypothetical: `home_advantage` is 1.0 in 2,642 of fold 1's 2,643 rows, and a
    genuinely constant column would divide by a zero standard deviation."""
    rows, labels = _synthetic(300, [0.0, 0.8, 0.3, -0.4], 0.2)
    for row in rows:
        row[0] = 1.0  # perfectly constant
    model = fit(rows, labels, NAMES)
    assert all(math.isfinite(c) for c in model.coefficients)
    assert model.stds[0] == 1.0  # kept at unit scale rather than dividing by zero
    assert abs(model.coefficients[0]) < 1e-6  # carries no information, driven to ~0 by L2


def test_predict_proba_returns_probabilities_and_checks_its_input():
    rows, labels = _synthetic(200, [1.0, -0.5, 0.0, 0.3], 0.0)
    model = fit(rows, labels, NAMES)
    probs = model.predict_proba(rows)
    assert len(probs) == len(rows)
    assert all(0.0 < p < 1.0 for p in probs)
    with pytest.raises(EstimatorError, match="expected 4 features"):
        model.predict_proba([[1.0, 2.0]])


@pytest.mark.parametrize(
    ("rows", "labels", "match"),
    [([[1.0] * 4], [True, False], "1 rows but 2 labels"), ([], [], "zero rows"),
     ([[1.0, 2.0]], [True], "must have 4 features")],
    ids=["length-mismatch", "empty", "wrong-width"],
)
def test_malformed_training_input_is_refused(rows, labels, match):
    with pytest.raises(EstimatorError, match=match):
        fit(rows, labels, NAMES)


# --- the artifact: the thing T-009's security note is about ---------------------------------------


def _model() -> LogisticModel:
    rows, labels = _synthetic(300, [1.0, -0.5, 0.2, 0.4], 0.1)
    return fit(rows, labels, NAMES)


def test_the_artifact_is_plain_json_with_no_executable_content(tmp_path):
    """T-009's security note: 'the serialized artifact is an arbitrary-code-execution vector ... and
    Phase 2 loads this artifact inside the API service.' It is JSON precisely so that is false —
    `json.loads` returns dicts and floats or raises, and there is no object graph to reconstruct."""
    path = tmp_path / "model.json"
    save_artifact(_model(), path, training={"seasons": [2022, 2023], "n_games": 300})

    raw = path.read_text()
    document = json.loads(raw)  # parses as pure JSON, which a pickle would not
    assert document["format"] == ARTIFACT_FORMAT
    assert set(document["coefficients"]) == set(NAMES)
    # no pickle opcodes, no import machinery, no code objects
    assert "__reduce__" not in raw and "pickle" not in raw and "!!python" not in raw
    assert all(isinstance(v, (int, float)) for v in document["coefficients"].values())


def test_an_artifact_round_trips_to_identical_predictions(tmp_path):
    path = tmp_path / "model.json"
    original = _model()
    version = save_artifact(original, path, training={"seasons": [2022], "n_games": 300})
    restored, document = load_artifact(path)

    assert restored == original
    assert document["model_version"] == version
    rows, _ = _synthetic(50, [1.0, -0.5, 0.2, 0.4], 0.1, seed=99)
    assert restored.predict_proba(rows) == original.predict_proba(rows)


def test_the_model_version_is_a_content_hash_not_a_timestamp(tmp_path):
    """D-012 keys persisted predictions by `model_version`, so 'which model said this' must be
    answerable months later — and re-running the pipeline on the same data must prove it."""
    model = _model()
    training = {"seasons": [2022, 2023], "n_games": 300}
    v1 = save_artifact(model, tmp_path / "a.json", training=training)
    v2 = save_artifact(model, tmp_path / "b.json", training=training)
    assert v1 == v2  # same content, same version, despite different `created_utc`
    assert json.loads((tmp_path / "a.json").read_text())["created_utc"] != ""

    # ...and a different model, or different training metadata, gets a different version
    other = LogisticModel(NAMES, (0.1, 0.2, 0.3, 0.4), 0.0, model.means, model.stds)
    assert model_version(artifact_payload(other, training=training)) != v1
    assert model_version(artifact_payload(model, training={**training, "n_games": 301})) != v1


def test_an_edited_artifact_is_refused(tmp_path):
    """The version is a hash of the content, so hand-editing a coefficient is detectable — which
    matters because Phase 2 loads this from disk and a silently altered coefficient has no symptom."""
    path = tmp_path / "model.json"
    save_artifact(_model(), path, training={"seasons": [2022], "n_games": 300})
    document = json.loads(path.read_text())
    document["coefficients"]["form_diff"] += 0.5
    path.write_text(json.dumps(document))

    with pytest.raises(EstimatorError, match="has been edited since it was written"):
        load_artifact(path)


def test_an_artifact_in_an_unknown_format_is_refused(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"format": "pickle/2", "model_version": "x"}))
    with pytest.raises(EstimatorError, match="refusing to guess"):
        load_artifact(path)


# --- F-110: the containment check the docstring used to only claim ------------------------------


def test_an_artifact_inside_the_repo_but_outside_models_is_refused():
    """F-110 — the docstring claimed this refused; the body was an unconditional write.

    The claim's real intent is narrower than the claim was: an artifact can only be committed by
    accident if it lands inside the repository, and `.gitignore` covers exactly `/models/` (anchored,
    F-016). So this is the case that must fail — a plausible-looking path that git would happily
    track.
    """
    from model.estimator import REPO_ROOT

    with pytest.raises(EstimatorError, match="inside the repository but outside"):
        save_artifact(
            _model(),
            REPO_ROOT / "backend" / "model" / "accidental.json",
            training={"seasons": [2022], "n_games": 300},
        )
    assert not (REPO_ROOT / "backend" / "model" / "accidental.json").exists()


def test_an_artifact_outside_the_repo_is_allowed(tmp_path):
    """The control (F-051's lesson): a check that refused everything would pass the test above and
    break every existing caller. Outside the repo, nothing can reach git — which is why the whole
    suite already writes to tmp_path and must keep working."""
    version = save_artifact(
        _model(), tmp_path / "model.json", training={"seasons": [2022], "n_games": 300}
    )
    assert version
    assert (tmp_path / "model.json").exists()


def test_an_artifact_in_the_models_dir_is_allowed():
    """The path production actually uses (`run_evaluation.ARTIFACT_DIR`). Gitignored, so writing and
    removing a probe here cannot dirty the tree — asserted rather than assumed."""
    import subprocess

    from model.estimator import REPO_ROOT

    path = REPO_ROOT / "models" / "f110-probe.json"
    try:
        assert save_artifact(_model(), path, training={"seasons": [2022], "n_games": 300})
        assert path.exists()
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout
        assert "f110-probe.json" not in porcelain
    finally:
        path.unlink(missing_ok=True)
