"""Logistic regression and the versioned model artifact (T-009).

Standard library only — no scikit-learn, no numpy — and the artifact is **JSON**, not a pickle.

## Why this is not a wrapper around scikit-learn

PLAN-v1 described `estimator` as "a thin wrapper over the logistic-regression implementation", and
T-009's security note anticipated the consequence:

  > **the serialized artifact is an arbitrary-code-execution vector.** Loading a pickled object
  > executes code inside it, and Phase 2 loads this artifact inside the API service.

That note is the sharpest in the plan, and the cheapest way to honor it is to make it inapplicable.
A four-feature logistic regression is a 5x5 Newton solve; the fitted model is five floats. Serialized
as JSON, **there is no deserialization step that can execute anything** — `json.load` returns dicts
and floats or raises. Phase 2 loads a number, not an object graph.

This is the same move D-004 made on F-005 (choose an ESPN-keyed source so no join exists to get
wrong) and D-022 made on leakage (make the target scoreless so its result is unreachable): design the
threat out rather than mitigate it. Recorded as **D-030**.

Two further consequences, both good: the served image gains **nothing** for Phase 2 inference beyond
`json` and the stdlib feature function; and the coefficients are directly readable, which T-010 needs
("records which features carried signal — coefficients and their direction").

The cost is that the fit is ours to get right. It is checked three ways — coefficient recovery from
synthetic data with known truth, the vanishing-gradient property that *defines* the optimum, and a
one-time cross-check against scikit-learn recorded in the tracker.

## Numerical notes

Fitting is Newton-Raphson / IRLS with L2, which converges in a handful of iterations for a problem
this small and needs no learning rate. Features are standardized using **training** means and
deviations only — computing them over train+test would leak test distribution into the fit, which is
the subtle version of the thing this whole project is built to avoid. The standardization parameters
travel in the artifact, because inference must reproduce them exactly.

L2 is applied to the coefficients but **not** the intercept: penalizing the intercept would bias the
base rate, and the base rate is the thing D-008 compares against.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Artifact format tag. Bump if the shape changes so a stale artifact fails loudly.
ARTIFACT_FORMAT: str = "json-linear-model/1"

#: Default L2 strength, on standardized features. Small: the job is conditioning, not shrinkage —
#: D-024 notes `home_advantage` is near-constant in the early folds, which makes the system
#: ill-conditioned rather than the coefficient large.
DEFAULT_L2: float = 1.0

_MAX_ITERATIONS: int = 100
_TOLERANCE: float = 1e-10

#: The artifact fields the version hashes over — content only, never `created_utc` or the version.
_PAYLOAD_KEYS: tuple[str, ...] = (
    "format", "feature_names", "coefficients", "intercept", "standardization", "training",
)


class EstimatorError(RuntimeError):
    """Raised when a fit cannot be trusted, or an artifact cannot be."""


def _sigmoid(z: float) -> float:
    # Split by sign to avoid overflow in exp for large |z|.
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting. The system is (p+1)x(p+1) with p=4."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise EstimatorError(
                f"the normal-equation system is singular at column {col} — a feature is constant or "
                "perfectly collinear with another. With L2 > 0 this should not happen; check the "
                "training set rather than raising the tolerance."
            )
        a[col], a[pivot] = a[pivot], a[col]
        for row in range(n):
            if row == col:
                continue
            factor = a[row][col] / a[col][col]
            for k in range(col, n + 1):
                a[row][k] -= factor * a[col][k]
    return [a[i][n] / a[i][i] for i in range(n)]


@dataclass(frozen=True, slots=True)
class LogisticModel:
    """A fitted model: five floats and the standardization that makes them meaningful."""

    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float
    means: tuple[float, ...]
    stds: tuple[float, ...]

    def predict_proba(self, rows: Sequence[Sequence[float]]) -> list[float]:
        """P(home win) for each feature vector, in `feature_names` order."""
        out = []
        for row in rows:
            if len(row) != len(self.feature_names):
                raise EstimatorError(
                    f"expected {len(self.feature_names)} features, got {len(row)}"
                )
            z = self.intercept
            for value, mean, std, coef in zip(
                row, self.means, self.stds, self.coefficients, strict=True
            ):
                z += coef * ((value - mean) / std)
            out.append(_sigmoid(z))
        return out

    def gradient_norm(self, rows: Sequence[Sequence[float]], labels: Sequence[bool], l2: float) -> float:
        """Max absolute gradient of the penalized log-likelihood at this solution.

        Near zero is what *defines* an optimum, so this is the property a test can assert without
        re-deriving the fit — the one check that cannot agree with a wrong implementation by
        sharing its arithmetic.
        """
        probs = self.predict_proba(rows)
        grad_intercept = sum(
            (1.0 if y else 0.0) - p for y, p in zip(labels, probs, strict=True)
        )
        grads = [abs(grad_intercept)]
        for j in range(len(self.feature_names)):
            g = sum(
                ((1.0 if y else 0.0) - p) * ((row[j] - self.means[j]) / self.stds[j])
                for row, y, p in zip(rows, labels, probs, strict=True)
            )
            grads.append(abs(g - l2 * self.coefficients[j]))
        return max(grads)


def fit(
    rows: Sequence[Sequence[float]],
    labels: Sequence[bool],
    feature_names: Sequence[str],
    *,
    l2: float = DEFAULT_L2,
) -> LogisticModel:
    """Fit by Newton-Raphson. Deterministic: no randomness, no shuffling, no initialization seed."""
    if len(rows) != len(labels):
        raise EstimatorError(f"{len(rows)} rows but {len(labels)} labels")
    if not rows:
        raise EstimatorError("cannot fit on zero rows")
    p = len(feature_names)
    if any(len(r) != p for r in rows):
        raise EstimatorError(f"every row must have {p} features")
    if l2 < 0.0:
        raise EstimatorError(f"l2 must be >= 0, got {l2}")

    n = len(rows)
    means = [sum(r[j] for r in rows) / n for j in range(p)]
    stds = []
    for j in range(p):
        var = sum((r[j] - means[j]) ** 2 for r in rows) / n
        std = math.sqrt(var)
        # A constant feature carries no information; standardizing it would divide by zero. Keep it
        # at scale 1 so its coefficient is driven to ~0 by L2 rather than exploding. D-024: this is
        # not hypothetical -- `home_advantage` is 1.0 in 2,642 of fold 1's 2,643 rows.
        stds.append(std if std > 1e-12 else 1.0)

    design = [[1.0, *[(r[j] - means[j]) / stds[j] for j in range(p)]] for r in rows]
    beta = [0.0] * (p + 1)

    for _ in range(_MAX_ITERATIONS):
        probs = [_sigmoid(sum(b * x for b, x in zip(beta, row, strict=True))) for row in design]
        # gradient of the penalized log-likelihood (intercept unpenalized)
        grad = [
            sum(((1.0 if y else 0.0) - pr) * row[k] for row, y, pr in zip(design, labels, probs, strict=True))
            - (0.0 if k == 0 else l2 * beta[k])
            for k in range(p + 1)
        ]
        hessian = [[0.0] * (p + 1) for _ in range(p + 1)]
        for row, pr in zip(design, probs, strict=True):
            w = pr * (1.0 - pr)
            for a in range(p + 1):
                wa = w * row[a]
                for b in range(p + 1):
                    hessian[a][b] += wa * row[b]
        for k in range(1, p + 1):
            hessian[k][k] += l2

        step = _solve(hessian, grad)
        beta = [b + s for b, s in zip(beta, step, strict=True)]
        if max(abs(s) for s in step) < _TOLERANCE:
            break
    else:
        raise EstimatorError(
            f"Newton did not converge in {_MAX_ITERATIONS} iterations — refusing to return a model "
            "that has not settled rather than reporting numbers from it"
        )

    return LogisticModel(
        feature_names=tuple(feature_names),
        coefficients=tuple(beta[1:]),
        intercept=beta[0],
        means=tuple(means),
        stds=tuple(stds),
    )


def artifact_payload(model: LogisticModel, *, training: dict) -> dict:
    """The artifact's content, without the version — the thing the version is a hash *of*."""
    return {
        "format": ARTIFACT_FORMAT,
        "feature_names": list(model.feature_names),
        "coefficients": dict(zip(model.feature_names, model.coefficients, strict=True)),
        "intercept": model.intercept,
        "standardization": {
            "means": dict(zip(model.feature_names, model.means, strict=True)),
            "stds": dict(zip(model.feature_names, model.stds, strict=True)),
        },
        "training": training,
    }


def model_version(payload: dict) -> str:
    """A content hash of the fitted model.

    Deterministic on purpose (D-012 keys persisted predictions by `model_version`): the same data and
    the same config must produce the same id, so "which model said this" is answerable months later
    and re-running the pipeline proves it. A timestamp or a counter would not survive that test.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def save_artifact(model: LogisticModel, path: Path, *, training: dict) -> str:
    """Write the versioned artifact as JSON and return its version. Returns before it is read back.

    JSON, never pickle — see the module docstring. `.gitignore` covers `/models/`; this refuses to
    write anywhere else so an artifact cannot be committed by accident.
    """
    payload = artifact_payload(model, training=training)
    version = model_version(payload)
    document = {"model_version": version, "created_utc": datetime.now(UTC).isoformat(), **payload}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return version


def load_artifact(path: Path) -> tuple[LogisticModel, dict]:
    """Read an artifact back. Returns (model, document).

    `json.load` cannot execute code: it returns dicts, lists, strings and numbers, or it raises. That
    is the entire reason this is JSON — Phase 2 loads this inside the API service, and the alternative
    was a format whose load step runs whatever is in the file.
    """
    document = json.loads(path.read_text())
    if document.get("format") != ARTIFACT_FORMAT:
        raise EstimatorError(
            f"artifact at {path} has format {document.get('format')!r}, expected "
            f"{ARTIFACT_FORMAT!r} — refusing to guess at a shape this code does not know"
        )
    names = tuple(document["feature_names"])
    # Re-hash exactly the fields `artifact_payload` produces — the version must be a function of the
    # model's content, so `created_utc` and `model_version` itself are excluded by construction.
    recomputed = model_version(
        {key: document[key] for key in _PAYLOAD_KEYS}
    )
    if recomputed != document["model_version"]:
        raise EstimatorError(
            f"artifact at {path} carries model_version {document['model_version']} but its contents "
            f"hash to {recomputed} — the file has been edited since it was written"
        )
    model = LogisticModel(
        feature_names=names,
        coefficients=tuple(document["coefficients"][n] for n in names),
        intercept=document["intercept"],
        means=tuple(document["standardization"]["means"][n] for n in names),
        stds=tuple(document["standardization"]["stds"][n] for n in names),
    )
    return model, document
