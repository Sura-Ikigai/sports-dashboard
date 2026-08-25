"""The walk-forward evaluation run (T-009) — the script that answers Phase 1's question.

    PYTHONPATH=backend python -m model.run_evaluation

Loads the curated corpus, computes features once, fits logistic regression per fold, evaluates each
fold on its sealed test season, and states plainly whether **both** halves of D-008 are met.

This is the only module in `model/` that is not pure: it reads the corpus (via `dataset`, hence
pandas) and writes artifacts. Everything it calls is pure, which is what makes the numbers it prints
reproducible from committed code — T-010's requirement.

## The rules it is required to respect, and where each is enforced

  - **Features come from `compute_training_features`**, whose as-of moment is the game's own tip-off.
    T-009 never chooses an as-of, so it cannot choose a leaky one (D-022).
  - **The history passed for a test game is the whole corpus.** That is not a leak: `features`
    filters to strictly-before-tip-off internally, and the leakage property test is what proves it.
    Pre-filtering here would be a caller doing the module's job, which the security note forbids.
  - **The model is fitted on training games only**, and standardization means/stds come from that fit
    (`estimator.fit`), so no test-season distribution reaches the coefficients.
  - **`assert_curated` runs on the input** (F-067) before anything else.
  - **Artifacts are JSON** and land in the gitignored `models/` directory (D-030). Never pickle,
    never committed.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

from .corpus import assert_curated
from .dataset import load_games
from .estimator import DEFAULT_L2, fit, save_artifact
from .evaluate import ACCURACY_TARGET, BASE_RATE, evaluate
from .features import FEATURE_NAMES, GameHistory, compute_training_features, to_vector
from .splits import SEALED_FOLD, split_games, walk_forward_folds

REPO_ROOT = Path(__file__).resolve().parents[2]
#: `.gitignore` anchors `/models/` at the repo root — an artifact cannot be committed by accident.
ARTIFACT_DIR = REPO_ROOT / "models"


def _rows(history: GameHistory, games):
    """Feature vectors and labels for `games`, each as of its own tip-off."""
    vectors, labels = [], []
    for game in games:
        vectors.append(to_vector(compute_training_features(history, game)))
        labels.append(game.home_win)
    return vectors, labels


def main() -> int:
    games = load_games()
    assert_curated(games)
    history = GameHistory.of(games)
    print(f"corpus: {len(games)} curated games, {len({g.season for g in games})} seasons\n")

    results = []
    for fold in walk_forward_folds():
        train_games, test_games = split_games(games, fold)
        train_x, train_y = _rows(history, train_games)
        test_x, test_y = _rows(history, test_games)

        model = fit(train_x, train_y, FEATURE_NAMES, l2=DEFAULT_L2)
        probs = model.predict_proba(test_x)
        result = evaluate(test_y, probs, constant=BASE_RATE)

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
        results.append((fold, model, result, version))

        print(f"── fold: {fold}   (model {version})")
        print(f"   train {len(train_games):5d}   test {len(test_games):5d}")
        print(f"   accuracy {result.accuracy:.4f}   log loss {result.log_loss:.4f}   "
              f"AUC {result.roc_auc:.4f}")
        print(f"   constant({BASE_RATE}) log loss {result.comparison.constant_log_loss:.4f}   "
              f"improvement {result.comparison.improvement:+.4f}   "
              f"beats constant: {result.comparison.beats_constant}")
        print("   coefficients: " + "  ".join(
            f"{n} {c:+.4f}" for n, c in zip(FEATURE_NAMES, model.coefficients, strict=True)
        ) + f"   intercept {model.intercept:+.4f}\n")

    accuracies = [r.accuracy for _, _, r, _ in results]
    print("── fold-to-fold spread (D-013: the point of three folds rather than one) ──")
    print(f"   accuracy   min {min(accuracies):.4f}   max {max(accuracies):.4f}   "
          f"spread {max(accuracies) - min(accuracies):.4f}   "
          f"stdev {statistics.stdev(accuracies):.4f}")
    total_test = sum(r.n_games for _, _, r, _ in results)
    print(f"   {total_test} evaluation games across {len(results)} folds\n")

    sealed_fold, sealed_model, sealed, sealed_version = results[-1]
    assert sealed_fold == SEALED_FOLD
    print(f"══ HEADLINE — the sealed fold ({sealed_fold}), model {sealed_version} ══")
    print(f"   accuracy   {sealed.accuracy:.4f}   (target >= {ACCURACY_TARGET})")
    print(f"   log loss   {sealed.log_loss:.4f}   vs constant {sealed.comparison.constant_log_loss:.4f}")
    print(f"   AUC        {sealed.roc_auc:.4f}\n")

    accuracy_ok = sealed.accuracy >= ACCURACY_TARGET
    calibration_ok = sealed.comparison.beats_constant
    print(f"   D-008 half 1 — accuracy >= {ACCURACY_TARGET}:            "
          f"{'MET' if accuracy_ok else 'NOT MET'}  ({sealed.accuracy:.4f})")
    print(f"   D-008 half 2 — log loss beats constant {BASE_RATE}:  "
          f"{'MET' if calibration_ok else 'NOT MET'}  "
          f"({sealed.log_loss:.4f} vs {sealed.comparison.constant_log_loss:.4f})")
    print(f"\n   SHIP CRITERION (both required): "
          f"{'MET' if sealed.meets_ship_criterion else 'NOT MET'}\n")

    print("── calibration on the sealed fold (is a 70% call right ~70% of the time?) ──")
    for b in sealed.calibration:
        if not b.count:
            continue
        print(f"   [{b.lower:.1f},{b.upper:.1f})  n={b.count:5d}  "
              f"predicted {b.mean_predicted:.3f}  observed {b.observed_rate:.3f}")

    return 0 if sealed.meets_ship_criterion else 1


if __name__ == "__main__":
    sys.exit(main())
