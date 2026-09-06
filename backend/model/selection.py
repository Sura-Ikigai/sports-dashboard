"""Feature selection on the dev seasons (T-030) -- run before the sealed fold is ever touched.

    PYTHONPATH=backend python -m model.selection postgresql://.../corpus

The plan's rule is that **feature selection happens on 2024 and 2025 only**, and that 2026 is spent
exactly once after the set is frozen. This module is the first half of that. It is deliberately a
separate module from `run_evaluation`, which owns the second half, because the two must not share an
entry point: a module you can run freely while exploring must be structurally incapable of touching
the sealed season, and that is easier to guarantee than to remember.

## What "structurally incapable" means here

`DEV_FOLDS` is filtered from `splits.WALK_FORWARD_FOLDS` by test season, and `_dev_folds` raises if
the filter ever admits `splits.SEALED_FOLD`. There is no parameter that selects a fold, no flag that
widens the set, and no code path in this module that reads a 2026 row. Running it a hundred times
costs nothing, which is the point: exploration has to be free, or the pressure to peek lands
somewhere worse.

## The decision rule, fixed before the numbers exist

This is the part that matters, and the reason this docstring is committed **before** the first run.
A threshold chosen after seeing the results is not a threshold, it is a rationalisation, and the git
history of this file is what makes the claim checkable rather than asserted.

The owner's call, taken 2026-09-05 on T-028's and T-029's measurements:

  1. **`travel_diff` and `altitude` are dropped**, jointly, **unless** removing both costs at least
     `MATERIALITY_THRESHOLD` of mean dev AUC. Removal is the standing prior; the ablation is its
     escape hatch, not its justification.

  2. **No other feature is dropped by this run.** Leave-one-out numbers for the rest are *reported,
     not acted on*. If one of them looks weaker than the two candidates -- `rest_edge` is the obvious
     possibility, its T-028 coefficient being the smallest at +.027 -- that goes back to the owner as
     a question. Acting on it here would be selection on a rule invented after seeing the data, which
     is the thing this whole ceremony exists to prevent.

     **This is exactly what happened, and it is recorded rather than smoothed over.** `rest_edge`
     came back at -.0001 to remove; the question went to the owner and the owner chose to drop it.
     That is post-hoc selection, it is a deviation from this rule, and `DROP_CANDIDATES` below is
     deliberately *not* amended to include it -- amending it would rewrite the pre-registration to
     match the outcome, which would leave no trace that the protocol had been departed from. The two
     candidate sets differed by .0001 dev AUC, so the overfitting risk it carries is small; small is
     not none, and T-035 reports it as what it was.

  3. **The threshold is 0.0020 mean dev AUC.** T-028 measured all six non-Elo features together worth
     +.0090 AUC over Elo alone; this is a fifth of that. Below it, on ~2,641 dev games, a difference
     is not distinguishable from resampling noise -- and D-040's waterfall is *better* with fewer
     bars, so a feature that cannot be shown to carry anything is a bar the reader has to learn to
     ignore.

## Honest about what an ablation of this size can and cannot resolve

It will rank the features. It will **not** cleanly separate the two candidates from zero: their
individual contribution sits around .001-.003 AUC, which is the resolution floor at this sample size.
That is why the rule above is written as a prior plus an escape hatch rather than as a test -- the
measurement is being used to catch a surprise, not to prove an absence.

Correlated features are ablated as a **block** as well as individually, because leave-one-out
systematically understates a correlated group: `home_b2b`, `away_b2b` and `rest_edge` correlate at
±.47 (T-028), so dropping any one of them lets the other two absorb most of its work.
"""

from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass, field

import sqlalchemy as sa

from .estimator import DEFAULT_L2, fit
from .evaluate import roc_auc
from .features import FEATURE_NAMES, compute_training_features, to_vector
from .splits import SEALED_FOLD, WALK_FORWARD_FOLDS, Fold, split_games
from .store import load_context, load_games

#: Seasons feature selection may be measured on. 2026 is absent, and `_dev_folds` proves it.
DEV_SEASONS: tuple[int, ...] = (2024, 2025)

#: The owner's drop candidates (2026-09-05), evaluated jointly. See the module docstring, rule 1.
DROP_CANDIDATES: tuple[str, ...] = ("travel_diff", "altitude")

#: Mean dev AUC a candidate set must give up before removal is reconsidered. Rule 3.
MATERIALITY_THRESHOLD: float = 0.0020


def decide(full_auc: float, reduced_auc: float, *, threshold: float = MATERIALITY_THRESHOLD) -> bool:
    """Whether to drop `DROP_CANDIDATES`, per rule 1. Removal is the prior; cost is its escape hatch.

    A three-line function with its own test, rather than an expression inside `run`, so the rule can
    be pinned without a database and so a later edit to it is visible as an edit to the rule rather
    than as a change buried in a reporting function.
    """
    return (full_auc - reduced_auc) < threshold


class SelectionError(RuntimeError):
    """Raised when selection would read a season it is not allowed to see."""


def _dev_folds() -> tuple[Fold, ...]:
    """The folds selection may use: those testing on a dev season, and nothing else.

    The assertion is not decoration. `WALK_FORWARD_FOLDS` is a literal that a future edit could
    reorder or extend, and the failure mode of getting this wrong is silent -- a fold testing on 2026
    would produce a perfectly ordinary-looking number that had spent the sealed season.
    """
    folds = tuple(f for f in WALK_FORWARD_FOLDS if f.test_season in DEV_SEASONS)
    if SEALED_FOLD in folds:
        raise SelectionError(
            f"the sealed fold [{SEALED_FOLD}] is in the dev set — selection must never read the "
            "season the model is evaluated on"
        )
    if not folds:
        raise SelectionError(f"no fold tests on a dev season {DEV_SEASONS}")
    for fold in folds:
        if fold.test_season not in DEV_SEASONS:
            raise SelectionError(f"fold [{fold}] does not test on a dev season")
    return folds


DEV_FOLDS: tuple[Fold, ...] = _dev_folds()


@dataclass(frozen=True, slots=True)
class Candidate:
    """One feature set, scored across the dev folds."""

    name: str
    features: tuple[str, ...]
    auc: float
    log_loss: float
    accuracy: float
    per_fold: tuple[tuple[int, float], ...] = field(default=())

    @property
    def size(self) -> int:
        return len(self.features)


def _project(vectors: list[tuple[float, ...]], keep: tuple[str, ...]) -> list[tuple[float, ...]]:
    """Slice full-width vectors down to `keep`, **in `keep`'s own order**.

    The invariant that matters is not which order that is, but that it is the *same* order as the
    names handed to `fit` alongside it -- a fitted artifact stores coefficients as
    `zip(feature_names, coefficients)`, so a projection ordered differently from its names would
    mislabel every coefficient in the reduced model and produce a waterfall attributing each factor's
    contribution to its neighbour. Passing one `keep` to both is what makes them agree by
    construction; `_without` happens to emit `FEATURE_NAMES` order, but nothing here depends on that.

    Feature vectors are computed **once** over the whole corpus and projected per candidate, rather
    than recomputed. Recomputing would be minutes per candidate and would also make every candidate a
    fresh opportunity for the as-of filter to be applied differently -- one computation, many
    projections, is both faster and the safer shape.
    """
    index = [FEATURE_NAMES.index(name) for name in keep]
    return [tuple(row[i] for i in index) for row in vectors]


def _log_loss(labels: list[bool], probs: list[float]) -> float:
    import math

    eps = 1e-15
    return -statistics.fmean(
        math.log(max(p, eps)) if y else math.log(max(1.0 - p, eps))
        for y, p in zip(labels, probs, strict=True)
    )


def score(
    rows: dict[int, tuple[list, list, list, list]], keep: tuple[str, ...], name: str
) -> Candidate:
    """Fit and evaluate `keep` across every dev fold, returning the mean."""
    aucs, losses, accuracies, per_fold = [], [], [], []
    for season, (train_x, train_y, test_x, test_y) in sorted(rows.items()):
        model = fit(_project(train_x, keep), train_y, keep, l2=DEFAULT_L2)
        probs = model.predict_proba(_project(test_x, keep))
        auc = roc_auc(test_y, probs)
        aucs.append(auc)
        losses.append(_log_loss(test_y, probs))
        accuracies.append(
            statistics.fmean(1.0 if (p >= 0.5) == y else 0.0 for p, y in zip(probs, test_y, strict=True))
        )
        per_fold.append((season, auc))
    return Candidate(
        name=name,
        features=keep,
        auc=statistics.fmean(aucs),
        log_loss=statistics.fmean(losses),
        accuracy=statistics.fmean(accuracies),
        per_fold=tuple(per_fold),
    )


def _without(*names: str) -> tuple[str, ...]:
    return tuple(f for f in FEATURE_NAMES if f not in names)


def build_rows(url: str) -> dict[int, tuple[list, list, list, list]]:
    """Feature vectors and labels for each dev fold, computed once.

    The Context carries the **whole** corpus, D-037's warm-up included, because Elo is running state
    over every prior season. `split_games` is what keeps the warm-up out of the rows (T-029), and it
    is given the same collection — one source, separated at the boundary that enforces it.
    """
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            context = load_context(conn)
            games = load_games(conn)
    finally:
        engine.dispose()

    rows: dict[int, tuple[list, list, list, list]] = {}
    for fold in DEV_FOLDS:
        train, test = split_games(games, fold)
        rows[fold.test_season] = (
            [to_vector(compute_training_features(context, g)) for g in train],
            [g.home_win for g in train],
            [to_vector(compute_training_features(context, g)) for g in test],
            [g.home_win for g in test],
        )
    return rows


def run(url: str) -> dict:
    """The ablation and the decision, as a JSON-serializable report."""
    rows = build_rows(url)

    full = score(rows, FEATURE_NAMES, "full")
    candidates = [full]
    for name in FEATURE_NAMES:
        candidates.append(score(rows, _without(name), f"without {name}"))
    # Blocks: correlated groups leave-one-out understates, plus the pair under decision. The rest
    # block was three features when this ran first; `rest_edge` left it after the owner's call, and
    # `_without` simply skips a name the set no longer carries.
    candidates.append(
        score(rows, _without("home_b2b", "away_b2b", "rest_edge"), "without the rest block")
    )
    candidates.append(score(rows, _without(*DROP_CANDIDATES), "without the drop candidates"))
    candidates.append(score(rows, ("elo_diff",), "elo_diff alone"))

    reduced = next(c for c in candidates if c.name == "without the drop candidates")
    cost = full.auc - reduced.auc
    drop = decide(full.auc, reduced.auc)

    return {
        "dev_seasons": list(DEV_SEASONS),
        "folds": [str(f) for f in DEV_FOLDS],
        "n_train": {s: len(v[0]) for s, v in rows.items()},
        "n_test": {s: len(v[2]) for s, v in rows.items()},
        "threshold": MATERIALITY_THRESHOLD,
        "drop_candidates": list(DROP_CANDIDATES),
        "candidates": [
            {
                "name": c.name,
                "features": list(c.features),
                "mean_auc": c.auc,
                "mean_log_loss": c.log_loss,
                "mean_accuracy": c.accuracy,
                "delta_auc_vs_full": c.auc - full.auc,
                "per_fold_auc": {str(s): a for s, a in c.per_fold},
            }
            for c in candidates
        ],
        "decision": {
            "cost_of_dropping_candidates": cost,
            "threshold": MATERIALITY_THRESHOLD,
            "drop": drop,
            "frozen_features": list(reduced.features if drop else FEATURE_NAMES),
        },
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    report = run(sys.argv[1])

    print(f"dev folds: {', '.join(report['folds'])}")
    print(f"train rows {report['n_train']}   test rows {report['n_test']}\n")
    print(f"{'candidate':<32}{'n':>3}{'mean AUC':>11}{'Δ vs full':>11}{'log loss':>10}{'acc':>8}")
    for c in report["candidates"]:
        print(
            f"  {c['name']:<30}{len(c['features']):>3}{c['mean_auc']:>11.4f}"
            f"{c['delta_auc_vs_full']:>+11.4f}{c['mean_log_loss']:>10.4f}{c['mean_accuracy']:>8.4f}"
        )

    decision = report["decision"]
    print("\n── the pre-registered decision ──")
    print(f"   dropping {report['drop_candidates']} costs {decision['cost_of_dropping_candidates']:+.4f} mean dev AUC")
    print(f"   threshold {decision['threshold']:.4f}  ->  DROP: {decision['drop']}")
    print(f"   frozen feature set ({len(decision['frozen_features'])}): {decision['frozen_features']}")
    print(json.dumps(report, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
