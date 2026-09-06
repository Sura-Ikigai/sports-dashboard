"""The walk-forward evaluation, and the **single** sealed-fold run (T-030, D-013/D-042).

    PYTHONPATH=backend python -m model.run_evaluation postgresql://.../corpus --spend-the-sealed-fold

The plan's rule: feature selection happens on 2024 and 2025 (that is `model.selection`), the feature
set freezes, and **2026 is evaluated exactly once**. This module owns that second half. It is a
separate module from `selection` because the two must not share an entry point -- exploration has to
be free, and the sealed run has to be hard to do by accident.

## The flag is the control

Running this without `--spend-the-sealed-fold` evaluates folds 1 and 2 and stops. Reaching 2026
requires typing a phrase that says what it costs, which is the whole design: D-013's headline is only
worth anything if it was produced once, and the cheapest way to keep a number honest is to make
spending it deliberate. The flag is not a safety catch to be removed later -- it is the record that
someone chose.

**A second run is not prevented, because it cannot be.** Anyone can pass the flag again, or check out
an older commit, or fit a model by hand. What is enforced is that it cannot happen *silently*: every
evaluation of the sealed fold appends to `docs/results/t030-sealed-runs.jsonl`, so a second look
leaves a second line, in git, next to the first. The plan says a second look "must be recorded if it
happens" -- this is that sentence made mechanical rather than remembered.

The append lives in `evaluate_fold`, not in `main`, and that placement was earned: with it in `main`,
re-deriving the results table by calling `evaluate_fold` directly evaluated 2026 again and logged
nothing. `model_version` is a content hash, so **two lines with the same version are a re-derivation
and two with different versions are a second attempt** -- a distinction the file makes visible
without anyone having to remember which run was which.

## What it evaluates

The frozen `features.FEATURE_NAMES`, and nothing else. There is no feature argument, no ablation and
no model selection here; those live in `selection` and ran before the freeze. This module fits and
reports.

## The rules it is required to respect, and where each is enforced

  - **Features come from `compute_training_features`**, whose as-of moment is the game's own tip-off.
    Nothing here chooses an as-of, so nothing here can choose a leaky one (D-022).
  - **The Context carries the whole corpus**, D-037's warm-up included, because Elo is running state
    over every prior season. `split_games` keeps the warm-up out of the rows (T-029) -- one
    collection, separated at the boundary that enforces the separation.
  - **The model is fitted on training games only**, and standardization means/stds come from that
    fit (`estimator.fit`), so no test-season distribution reaches the coefficients.
  - **`assert_curated` runs on the input** (F-067) before anything else, inside `split_games`.
  - **Artifacts are JSON** and land in the gitignored `models/` directory (D-030). Never pickle,
    never committed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from .estimator import DEFAULT_L2, fit, save_artifact
from .evaluate import ACCURACY_TARGET, BASE_RATE, evaluate
from .features import FEATURE_NAMES, Context, compute_training_features, to_vector
from .splits import SEALED_FOLD, Fold, split_games, walk_forward_folds
from .store import load_context, load_games

REPO_ROOT = Path(__file__).resolve().parents[2]
#: `.gitignore` anchors `/models/` at the repo root -- an artifact cannot be committed by accident.
ARTIFACT_DIR = REPO_ROOT / "models"

#: Every sealed run appends one line here. Committed, unlike the artifacts: the point is the record.
SEALED_LOG = REPO_ROOT / "docs" / "results" / "t030-sealed-runs.jsonl"

SPEND_FLAG = "--spend-the-sealed-fold"


def _rows(context: Context, games) -> tuple[list, list]:
    """Feature vectors and labels for `games`, each as of its own tip-off."""
    vectors, labels = [], []
    for game in games:
        vectors.append(to_vector(compute_training_features(context, game)))
        labels.append(game.home_win)
    return vectors, labels


def evaluate_fold(context: Context, games, fold: Fold) -> tuple[dict, str]:
    """Fit on the fold's training seasons, evaluate on its test season, emit a versioned artifact.

    **Recording a sealed evaluation happens here, not in `main`.** It was in `main` first, and the
    hole showed up within minutes of the control existing: re-deriving the three-fold table for
    `docs/results/` called this function directly, evaluated 2026 a second time, and logged nothing.
    The numbers were bit-identical -- `model_version` is a content hash, so an identical hash *is* the
    evidence that no new information was obtained -- but "it happened to be harmless" is not the
    property being claimed. The claim is that a sealed evaluation cannot happen silently, and a claim
    that only holds through one entry point does not hold.

    So the log follows the *evaluation*, and the `--spend-the-sealed-fold` flag follows *reaching*
    it. Two mechanisms, two jobs: the flag makes spending the fold deliberate, this makes it visible.
    Two lines carrying the same `model_version` are a re-derivation; two carrying different ones are
    a second attempt, and the difference is legible to anyone reading the file.
    """
    train_games, test_games = split_games(games, fold)
    train_x, train_y = _rows(context, train_games)
    test_x, test_y = _rows(context, test_games)

    model = fit(train_x, train_y, FEATURE_NAMES, l2=DEFAULT_L2)
    result = evaluate(test_y, model.predict_proba(test_x), constant=BASE_RATE)
    version = save_artifact(
        model,
        ARTIFACT_DIR / f"logistic-{fold.test_season}.json",
        training={
            "train_seasons": list(fold.train_seasons),
            "test_season": fold.test_season,
            "n_train": len(train_games),
            "l2": DEFAULT_L2,
            "feature_names": list(FEATURE_NAMES),
        },
    )
    row = {
        "fold": str(fold),
        "test_season": fold.test_season,
        "model_version": version,
        "n_train": len(train_games),
        "n_test": len(test_games),
        "accuracy": result.accuracy,
        "log_loss": result.log_loss,
        "roc_auc": result.roc_auc,
        "constant_log_loss": result.comparison.constant_log_loss,
        "beats_constant": result.comparison.beats_constant,
        "meets_ship_criterion": result.meets_ship_criterion,
        "coefficients": dict(zip(FEATURE_NAMES, model.coefficients, strict=True)),
        "intercept": model.intercept,
        "calibration": [
            {
                "lower": b.lower,
                "upper": b.upper,
                "count": b.count,
                "mean_predicted": b.mean_predicted,
                "observed_rate": b.observed_rate,
            }
            for b in result.calibration
            if b.count
        ],
    }
    if fold == SEALED_FOLD:
        _record_sealed_run({
            "ran_at": datetime.now(UTC).isoformat(),
            "fold": row["fold"],
            "model_version": version,
            "feature_names": list(FEATURE_NAMES),
            "accuracy": row["accuracy"],
            "log_loss": row["log_loss"],
            "roc_auc": row["roc_auc"],
            "constant_log_loss": row["constant_log_loss"],
            "meets_ship_criterion": row["meets_ship_criterion"],
        })
    return row, version


def folds_to_evaluate(*, spend_the_sealed_fold: bool) -> tuple[Fold, ...]:
    """Which folds a run may touch. The sealed fold is absent unless it was asked for by name.

    Extracted from `main` so the claim can be tested without a database -- the alternative was a
    guarantee that only a live Postgres could check, which is how a guarantee stops being checked.
    """
    folds = walk_forward_folds()
    if spend_the_sealed_fold:
        return folds
    return tuple(f for f in folds if f != SEALED_FOLD)


def _record_sealed_run(payload: dict) -> None:
    """Append one line to the committed log. A second look leaves a second line."""
    SEALED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SEALED_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _print_fold(row: dict) -> None:
    print(f"── fold: {row['fold']}   (model {row['model_version']})")
    print(f"   train {row['n_train']:5d}   test {row['n_test']:5d}")
    print(f"   accuracy {row['accuracy']:.4f}   log loss {row['log_loss']:.4f}   "
          f"AUC {row['roc_auc']:.4f}")
    print(f"   constant({BASE_RATE}) log loss {row['constant_log_loss']:.4f}   "
          f"beats constant: {row['beats_constant']}")
    print("   coefficients: " + "  ".join(
        f"{n} {c:+.4f}" for n, c in row["coefficients"].items()
    ) + f"   intercept {row['intercept']:+.4f}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="SQLAlchemy URL of an ingested corpus")
    parser.add_argument(
        SPEND_FLAG,
        action="store_true",
        help="evaluate the sealed fold. D-013's headline is worth something only if it happens "
             "once; every use of this appends a line to docs/results/t030-sealed-runs.jsonl",
    )
    args = parser.parse_args(argv)

    engine = sa.create_engine(args.url)
    try:
        with engine.connect() as conn:
            context = load_context(conn)
            games = load_games(conn)
    finally:
        engine.dispose()

    folds = folds_to_evaluate(spend_the_sealed_fold=args.spend_the_sealed_fold)

    print(f"corpus: {len(games)} games, {len({g.season for g in games})} seasons")
    print(f"frozen feature set ({len(FEATURE_NAMES)}): {list(FEATURE_NAMES)}\n")

    rows = [evaluate_fold(context, games, fold)[0] for fold in folds]
    for row in rows:
        _print_fold(row)

    accuracies = [r["accuracy"] for r in rows]
    print("── fold-to-fold spread (D-013: the point of three folds rather than one) ──")
    print(f"   accuracy   min {min(accuracies):.4f}   max {max(accuracies):.4f}   "
          f"spread {max(accuracies) - min(accuracies):.4f}"
          + (f"   stdev {statistics.stdev(accuracies):.4f}" if len(accuracies) > 1 else ""))
    print(f"   {sum(r['n_test'] for r in rows)} evaluation games across {len(rows)} folds\n")

    if not args.spend_the_sealed_fold:
        print(f"sealed fold [{SEALED_FOLD}] NOT evaluated. Pass {SPEND_FLAG} to spend it.")
        return 0

    sealed = next(r for r in rows if r["test_season"] == SEALED_FOLD.test_season)
    print(f"══ HEADLINE — the sealed fold ({sealed['fold']}), model {sealed['model_version']} ══")
    print(f"   accuracy   {sealed['accuracy']:.4f}   (target >= {ACCURACY_TARGET})")
    print(f"   log loss   {sealed['log_loss']:.4f}   vs constant {sealed['constant_log_loss']:.4f}")
    print(f"   AUC        {sealed['roc_auc']:.4f}\n")
    print(f"   D-008 half 1 — accuracy >= {ACCURACY_TARGET}:            "
          f"{'MET' if sealed['accuracy'] >= ACCURACY_TARGET else 'NOT MET'}")
    print(f"   D-008 half 2 — log loss beats constant {BASE_RATE}:  "
          f"{'MET' if sealed['beats_constant'] else 'NOT MET'}")
    print(f"\n   SHIP CRITERION (both required): "
          f"{'MET' if sealed['meets_ship_criterion'] else 'NOT MET'}\n")

    print("── calibration on the sealed fold (is a 70% call right ~70% of the time?) ──")
    for b in sealed["calibration"]:
        print(f"   [{b['lower']:.1f},{b['upper']:.1f})  n={b['count']:5d}  "
              f"predicted {b['mean_predicted']:.3f}  observed {b['observed_rate']:.3f}")

    print(f"\nrecorded in {SEALED_LOG.relative_to(REPO_ROOT)}")
    return 0 if sealed["meets_ship_criterion"] else 1


if __name__ == "__main__":
    sys.exit(main())
