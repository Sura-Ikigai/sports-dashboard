"""The scoring contract (T-033) -- the grid two implementations must agree on, and its generator.

    PYTHONPATH=backend python -m model.contract          # rewrite the committed vectors

## Why a second scorer exists at all, given D-011 forbids one

D-011's whole mechanism is that training and inference call **one** feature function, so there is no
second implementation to drift from. T-034's what-if panel breaks that on purpose: story 25 wants a
visitor to change a value and watch the probability respond, and story 27 requires that exploration
never reach anything that records it. A round trip to the server for every slider drag would be both
slow and a write-shaped path into a service whose entire security property is that it has none.

So the browser gets a scorer, and **this file is the price**. The duplication is deliberately made as
small as it can be: what TypeScript reimplements is the ~5 lines of logistic arithmetic in
`prediction.decompose`, and *nothing else*. It does not compute features. `compute_features` stays a
single implementation behind T-006's property test, and the browser scores a feature vector the API
already gave it -- with one or two values changed by the visitor.

That is the line, and it is worth stating precisely: **a what-if changes a feature's value, never the
history it was computed from.** A panel that recomputed `elo_diff` from game results in the browser
would be the D-011 violation this design avoids.

## What the contract is

A committed file of cases -- a model, a feature vector, and the exact output Python produces. Two
checks, one in each CI job, and neither can be satisfied locally:

  - `backend/tests/test_contract.py` regenerates the cases from `prediction.decompose` and asserts
    the committed file matches. If the Python arithmetic changes, the file is stale and the backend
    job goes red.
  - `frontend/lib/scoring/contract.test.ts` scores every case in TypeScript and asserts agreement.
    If the TypeScript drifts, the frontend job goes red.

Both jobs are required status checks on `main`, which is what "the check must run in the gate, not
locally" requires. A third job would have been more faithful -- one runtime running both languages --
and would **not** have been a required check, so a failing contract would not have blocked anything.
The file is the intermediary that lets the check live inside the two jobs that already block.

The file can only ever encode Python's answer, because it is generated from Python. Regenerating it
to make a failing TypeScript test pass therefore does not work: the regenerated file still says what
Python says.

## Exact equality on contributions, tolerance only on the probability

Every contribution is `coef * ((value - mean) / std)` -- three IEEE-754 operations in a fixed order,
which both languages perform identically on doubles. So contributions are compared with `===`, and
`decompose`'s docstring records that the parenthesization is load-bearing for that reason.

The probability is the one number that passes through `exp`, which is libm's and may differ in the
last unit in the last place between platforms. It gets a tolerance, pinned tight
(`PROBABILITY_TOLERANCE`) and measured rather than guessed at.

## The models here are synthetic, and that is not laziness

The obvious grid would score the frozen artifact. It cannot: `/models/` is gitignored precisely so a
model cannot be committed as if it were source (F-016, F-110), and a fixture carrying the artifact's
intercept, coefficients, means and stds would be that model in git under a different name --
reintroducing exactly the provenance ambiguity the gitignore exists to prevent.

So the grid uses models chosen to **bracket** the real one rather than to be it: the frozen
artifact's coefficients run |0.13| to |0.71| with means near zero and stds from .089 to 90, and
`MODELS` below spans coefficients to ±8, means to ±1000 and stds from 1e-3 to 1e3. Agreement across
that range implies agreement at the one point that ships, and the grid also exercises regions the
real model never visits -- saturated logits, a single feature, a negative intercept.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .estimator import LogisticModel
from .prediction import decompose

#: Bumped when the file's *shape* changes, so a TypeScript reader can refuse a document it does not
#: understand rather than silently reading missing fields as undefined.
CONTRACT_VERSION = 1

#: Where the committed file lives. Inside `frontend/` because the frontend test reads it by import;
#: the backend test reaches it by relative path, which is the direction that costs less.
VECTORS_PATH = (
    Path(__file__).resolve().parents[2] / "frontend" / "lib" / "scoring" / "contract-vectors.json"
)

#: The probability is the only output that passes through `exp`, which is the platform's libm and is
#: permitted to differ in the last ulp between implementations.
#:
#: **Measured, not guessed: the observed divergence across all 148 cases is exactly 0** -- CPython on
#: macOS/arm64 and V8 agree bit-for-bit on every probability, every logit and every contribution. The
#: tolerance is retained anyway, because "it agreed on the two platforms I ran it on" is not the same
#: claim as "exp is specified to agree", and CI runs Node on Ubuntu x86-64 against a file generated
#: on macOS. Contributions and the logit are still compared with exact equality; only this one number
#: gets slack, and it has never needed any.
PROBABILITY_TOLERANCE = 1e-12


@dataclass(frozen=True, slots=True)
class ContractModel:
    """One synthetic model in the grid, with the reason it is here."""

    key: str
    why: str
    model: LogisticModel


def _model(
    key: str, why: str, spec: dict[str, tuple[float, float, float]], intercept: float
) -> ContractModel:
    """Build a model from `{name: (coefficient, mean, std)}`, which reads far better than five
    parallel tuples that must be kept in the same order by hand."""
    names = tuple(spec)
    return ContractModel(
        key=key,
        why=why,
        model=LogisticModel(
            feature_names=names,
            coefficients=tuple(spec[n][0] for n in names),
            intercept=intercept,
            means=tuple(spec[n][1] for n in names),
            stds=tuple(spec[n][2] for n in names),
        ),
    )


MODELS: tuple[ContractModel, ...] = (
    _model(
        "frozen-shape",
        "four features at the magnitudes the shipped model actually uses -- the ordinary case",
        {
            "elo_diff": (0.70, 1.0, 90.0),
            "home_b2b": (-0.16, 0.14, 0.35),
            "away_b2b": (0.14, 0.18, 0.38),
            "avail_diff": (0.12, -0.001, 0.089),
        },
        0.25,
    ),
    _model(
        "saturating",
        "coefficients large enough to drive |z| past 40, so both branches of the stable sigmoid run "
        "and the probability saturates at each end",
        {"a": (8.0, 0.0, 1.0), "b": (-8.0, 0.0, 1.0)},
        0.0,
    ),
    _model(
        "tiny-std",
        "standardization that multiplies the raw value by a thousand -- catches an implementation "
        "that divides where it should multiply, or reorders the three operations",
        {"a": (0.5, 0.0, 1e-3), "b": (-0.25, 0.5, 1e-3)},
        -0.5,
    ),
    _model(
        "wide-mean",
        "means far from zero and a large std, so `value - mean` dominates and cancellation matters",
        {"a": (1.5, 1000.0, 1e3), "b": (-1.5, -1000.0, 250.0)},
        1.0,
    ),
    _model(
        "mixed-magnitude",
        "two enormous terms that cancel, plus an ordinary one. Summed in order the cancellation "
        "happens first and the small term survives; summed in reverse the small term is lost under "
        "the first enormous partial sum and the total is different. Floating-point addition is not "
        "associative, and this is the model that makes exact equality on the logit earn its keep -- "
        "a comparison with any tolerance at all would find nothing here",
        {"huge": (1e16, 0.0, 1.0), "opposite": (-1e16, 0.0, 1.0), "ordinary": (1.0, 0.0, 1.0)},
        0.5,
    ),
    _model(
        "single-feature",
        "one term, so a summation bug that only shows with several terms cannot hide behind them",
        {"only": (2.0, 0.5, 2.0)},
        -3.0,
    ),
)

#: How far each feature is swept from its mean, in standard deviations. Includes 0 -- the case where
#: every contribution is exactly zero and the probability is `sigmoid(intercept)`, which is the one
#: case where a scorer that ignored the features entirely would still be right, and therefore the one
#: that must never be the *only* case.
SWEEP_SIGMAS: tuple[float, ...] = (-6.0, -3.0, -1.0, -0.25, 0.0, 0.25, 1.0, 3.0, 6.0)


def _vectors(entry: ContractModel) -> list[tuple[str, list[float]]]:
    """The feature vectors scored against one model.

    Three families, and the third is the one that matters. Varying a single feature at a time is how
    a per-feature bug is localized; moving them all together is how a bug in the *sum* shows up.
    """
    model = entry.model
    base = list(model.means)
    out: list[tuple[str, list[float]]] = [("at-the-mean", list(base))]

    for index, name in enumerate(model.feature_names):
        for sigma in SWEEP_SIGMAS:
            if sigma == 0.0:
                continue  # already covered by `at-the-mean`
            values = list(base)
            values[index] = model.means[index] + sigma * model.stds[index]
            out.append((f"{name}@{sigma:+g}s", values))

    for sigma in (-3.0, -1.0, 1.0, 3.0):
        out.append((
            f"all@{sigma:+g}s",
            [m + sigma * s for m, s in zip(model.means, model.stds, strict=True)],
        ))
    # Alternating signs: every term present, no two the same, so a sum that dropped one shows.
    out.append((
        "alternating",
        [
            m + (2.0 if i % 2 == 0 else -2.0) * s
            for i, (m, s) in enumerate(zip(model.means, model.stds, strict=True))
        ],
    ))
    return out


def build() -> dict:
    """The whole contract document, computed through `prediction.decompose` and nothing else."""
    cases = []
    for entry in MODELS:
        model = entry.model
        for label, values in _vectors(entry):
            result = decompose(model, values)
            cases.append({
                "id": f"{entry.key}/{label}",
                "model": entry.key,
                "features": dict(zip(model.feature_names, values, strict=True)),
                "expected": {
                    "home_win_probability": result.home_win_probability,
                    "baseline_logit": result.baseline_logit,
                    "logit": result.logit,
                    "contributions": result.contributions,
                },
            })

    return {
        "contract_version": CONTRACT_VERSION,
        "generated_by": "backend/model/contract.py -- regenerate with `python -m model.contract`",
        "probability_tolerance": PROBABILITY_TOLERANCE,
        "models": {
            entry.key: {
                "why": entry.why,
                "feature_names": list(entry.model.feature_names),
                "intercept": entry.model.intercept,
                "coefficients": dict(
                    zip(entry.model.feature_names, entry.model.coefficients, strict=True)
                ),
                "means": dict(zip(entry.model.feature_names, entry.model.means, strict=True)),
                "stds": dict(zip(entry.model.feature_names, entry.model.stds, strict=True)),
            }
            for entry in MODELS
        },
        "cases": cases,
    }


def render(document: dict) -> str:
    """The exact bytes the file holds.

    `json.dumps` writes floats with `repr`, which round-trips through IEEE-754 exactly -- so the
    committed file loses nothing, and `test_contract.py` can compare regenerated values to loaded
    ones with `==` rather than a tolerance. Keys are not sorted: the order below is the order a
    reader wants, and `sort_keys` would put `cases` first.
    """
    return json.dumps(document, indent=2) + "\n"


def write(path: Path = VECTORS_PATH) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = build()
    path.write_text(render(document))
    return len(document["cases"])


def main() -> int:
    count = write()
    print(f"wrote {count} cases to {VECTORS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
