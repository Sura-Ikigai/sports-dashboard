"""Every number in the v2 analysis, re-derived from the corpus (T-035).

    PYTHONPATH=backend python -m model.report_v2 postgresql://.../corpus \
        --spend-the-sealed-fold --out docs/results/t035-figures.json

## Why a figures file exists at all

T-010's standard is that no number in an analysis document is estimated, rounded from memory, or
carried over from an earlier run. The way to hold a document to that is to compute the figures once,
commit them, and then check the prose against them mechanically -- which is what
`tests/test_analysis_figures.py` does.

The split matters because of D-043. Re-deriving these figures needs a **running Postgres holding a
verified ingest**, and CI has a Postgres but no data release (the corpus never enters git). So the
data-dependent half runs locally and the committed JSON is its output; the prose-checking half runs
in CI against that JSON and needs no data at all. Two halves, and the one that can be automated is.

This is the same shape as T-033's contract: a committed intermediary, checked from both ends. A
number can be wrong here only if someone edits the JSON by hand, and regenerating it overwrites that.

## The sealed fold, and why this needs the flag too

The headline is the sealed season, so generating this report **evaluates it** -- and `evaluate_fold`
appends to `docs/results/t030-sealed-runs.jsonl` whether or not anyone meant it to. That log is the
control working: two lines carrying the same `model_version` are a re-derivation, two carrying
different ones are a second attempt, and the file makes the difference legible.

The flag is required here for the same reason `run_evaluation` requires it: the log makes a sealed
evaluation *visible*, and the flag makes it *deliberate*. Two mechanisms, two jobs. A report
generator that could reach the sealed fold without saying so would defeat the second.

## What is *not* re-derived, stated plainly

Phase 1's figures. They were produced by a feature set this code no longer contains --
`point_diff_diff`, `form_diff` and `home_advantage` were removed by D-032/D-033 -- so they cannot be
recomputed without restoring superseded code. They are quoted from `docs/analysis/PHASE-1-RESULT.md`,
marked as quoted, and the deltas against them are computed here.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from . import selection
from .features import FEATURE_NAMES, Context, compute_training_features, to_vector
from .run_evaluation import evaluate_fold
from .splits import SEALED_FOLD, walk_forward_folds
from .store import load_context, load_games

#: Phase 1's sealed-season figures, quoted from `docs/analysis/PHASE-1-RESULT.md` §1-§2. Not
#: recomputable: D-032/D-033 removed the features that produced them.
PHASE_1: dict = {
    "source": "docs/analysis/PHASE-1-RESULT.md",
    "sealed_model_version": "972d33a83ad9",
    "corpus_games": 6605,
    "folds": {
        2024: {"accuracy": 0.6444, "log_loss": 0.6242, "roc_auc": 0.7083},
        2025: {"accuracy": 0.6503, "log_loss": 0.6129, "roc_auc": 0.7138},
        2026: {"accuracy": 0.6762, "log_loss": 0.6020, "roc_auc": 0.7323},
    },
    "accuracy_stdev": 0.0169,
    "accuracy_spread": 0.0318,
}

#: A team's first games of a season, the window F-141 says `avail_diff` is computed from the previous
#: season's roster in. Matches `availability.AvailabilityConfig.rotation_games`.
EARLY_SEASON_GAMES = 15


def _rows(context: Context, games):
    vectors = [to_vector(compute_training_features(context, g)) for g in games]
    labels = [g.home_win for g in games]
    return vectors, labels


def _fold_figures(context: Context, games, spend_the_sealed_fold: bool) -> list[dict]:
    """Every fold, through the same `evaluate_fold` the evaluation command uses.

    Deliberately not a reimplementation: a report that computed its own accuracy would be a second
    implementation of the number it is reporting, which is the defect this whole project keeps
    designing out.
    """
    folds = walk_forward_folds()
    if not spend_the_sealed_fold:
        folds = tuple(f for f in folds if f.test_season != SEALED_FOLD.test_season)
    return [evaluate_fold(context, games, fold)[0] for fold in folds]


def _ablation(url: str) -> dict:
    """What each frozen feature is worth, measured by removing it. **Dev folds only.**

    Removing a feature and scoring on the sealed season would be selection on sealed data, which is
    the one thing the fold is sealed against. `selection.DEV_FOLDS` is what this may touch, and
    `selection._dev_folds` refuses to build a set containing the sealed season.
    """
    rows = selection.build_rows(url)
    full = selection.score(rows, FEATURE_NAMES, "full")
    out = {
        "dev_seasons": list(selection.DEV_SEASONS),
        "full": {"auc": full.auc, "log_loss": full.log_loss, "accuracy": full.accuracy},
        "without": {},
    }
    for name in FEATURE_NAMES:
        reduced = selection.score(rows, selection._without(name), f"without {name}")
        out["without"][name] = {
            "auc": reduced.auc,
            "log_loss": reduced.log_loss,
            "accuracy": reduced.accuracy,
            "auc_cost": full.auc - reduced.auc,
            "log_loss_cost": reduced.log_loss - full.log_loss,
        }
    return out


def _games_played_index(games) -> dict[tuple[int, str], list[str]]:
    """`(season, team) -> that team's game ids in order`, for the early-season split."""
    order: dict[tuple[int, str], list[str]] = {}
    for game in sorted(games, key=lambda g: (g.date, g.game_id)):
        for team in (game.home_id, game.away_id):
            order.setdefault((game.season, team), []).append(game.game_id)
    return order


def _failures(context: Context, games, sealed_row: dict) -> dict:
    """Where the model got it wrong on the sealed season, cut three ways.

    The early/late cut is the one worth having: F-141 says `avail_diff` is computed from the previous
    season's roster for roughly each team's first fifteen games, and this is where that defect would
    show up as an accuracy gap if it costs anything measurable. A null result here is as publishable
    as a positive one (story 18).
    """
    from .estimator import fit
    from .run_evaluation import DEFAULT_L2
    from .splits import split_games

    train_games, test_games = split_games(games, SEALED_FOLD)
    train_x, train_y = _rows(context, train_games)
    test_x, test_y = _rows(context, test_games)
    model = fit(train_x, train_y, FEATURE_NAMES, l2=DEFAULT_L2)
    probs = model.predict_proba(test_x)

    order = _games_played_index(games)

    def _is_early(game) -> bool:
        return all(
            order[(game.season, team)].index(game.game_id) < EARLY_SEASON_GAMES
            for team in (game.home_id, game.away_id)
        )

    def _accuracy(pairs) -> float | None:
        if not pairs:
            return None
        return statistics.fmean(1.0 if (p >= 0.5) == y else 0.0 for p, y in pairs)

    early = [(p, y) for p, y, g in zip(probs, test_y, test_games, strict=True) if _is_early(g)]
    late = [(p, y) for p, y, g in zip(probs, test_y, test_games, strict=True) if not _is_early(g)]

    # Confidence bands, matching `track_record.BANDS` so the document and the live surface cut the
    # season the same way. A different set of boundaries here would make the two incomparable.
    from .track_record import BANDS, band_for

    by_band: dict[str, list] = {b.slug: [] for b in BANDS}
    for p, y in zip(probs, test_y, strict=True):
        by_band[band_for(p).slug].append((p, y))

    # The confident misses: highest-confidence wrong calls.
    misses = sorted(
        (
            (max(p, 1 - p), g.game_id, g.home_id, g.away_id, g.home_score, g.away_score, p)
            for p, y, g in zip(probs, test_y, test_games, strict=True)
            if (p >= 0.5) != y
        ),
        reverse=True,
    )[:5]

    return {
        "sealed_season": SEALED_FOLD.test_season,
        "n_test": len(test_games),
        "early_season": {
            "window_games": EARLY_SEASON_GAMES,
            "n": len(early),
            "accuracy": _accuracy(early),
        },
        "rest_of_season": {"n": len(late), "accuracy": _accuracy(late)},
        "by_band": {
            slug: {
                "n": len(pairs),
                "accuracy": _accuracy(pairs),
                "mean_confidence": (
                    statistics.fmean(max(p, 1 - p) for p, _ in pairs) if pairs else None
                ),
            }
            for slug, pairs in by_band.items()
        },
        "home_calls": {
            "n": sum(1 for p in probs if p >= 0.5),
            "accuracy": _accuracy([(p, y) for p, y in zip(probs, test_y, strict=True) if p >= 0.5]),
        },
        "away_calls": {
            "n": sum(1 for p in probs if p < 0.5),
            "accuracy": _accuracy([(p, y) for p, y in zip(probs, test_y, strict=True) if p < 0.5]),
        },
        "most_confident_misses": [
            {
                "confidence": c,
                "game_id": gid,
                "home_id": home,
                "away_id": away,
                "home_score": hs,
                "away_score": aw,
                "home_win_probability": p,
            }
            for c, gid, home, away, hs, aw, p in misses
        ],
        "sealed_accuracy": sealed_row["accuracy"],
    }


def build(url: str, *, spend_the_sealed_fold: bool) -> dict:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            context = load_context(conn)
            games = load_games(conn)
    finally:
        engine.dispose()

    rows = _fold_figures(context, games, spend_the_sealed_fold)
    accuracies = [r["accuracy"] for r in rows]
    sealed = next(r for r in rows if r["test_season"] == SEALED_FOLD.test_season)

    deltas = {}
    for row in rows:
        prior = PHASE_1["folds"].get(row["test_season"])
        if prior is None:
            continue
        deltas[row["test_season"]] = {
            "accuracy": row["accuracy"] - prior["accuracy"],
            "log_loss": row["log_loss"] - prior["log_loss"],
            "roc_auc": row["roc_auc"] - prior["roc_auc"],
        }

    return {
        "generated_utc": datetime.now(UTC).isoformat(),
        "generated_by": "backend/model/report_v2.py",
        "corpus": {"games": len(games), "seasons": len({g.season for g in games})},
        "feature_names": list(FEATURE_NAMES),
        "folds": rows,
        "spread": {
            "accuracy_min": min(accuracies),
            "accuracy_max": max(accuracies),
            "accuracy_spread": max(accuracies) - min(accuracies),
            "accuracy_stdev": statistics.stdev(accuracies),
            "evaluation_games": sum(r["n_test"] for r in rows),
        },
        "sealed": sealed,
        "ablation": _ablation(url),
        "failures": _failures(context, games, sealed),
        "phase_1": PHASE_1,
        "delta_vs_phase_1": deltas,
        "stdev_ratio": statistics.stdev(accuracies) / PHASE_1["accuracy_stdev"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="SQLAlchemy URL of an ingested corpus")
    parser.add_argument(
        "--spend-the-sealed-fold",
        action="store_true",
        help="required: the report's headline IS the sealed season, and reaching it appends a line "
             "to docs/results/t030-sealed-runs.jsonl",
    )
    parser.add_argument("--out", type=Path, required=True, help="where to write the figures JSON")
    args = parser.parse_args(argv)

    if not args.spend_the_sealed_fold:
        parser.error(
            "the report's headline is the sealed season, so it cannot be built without spending "
            "the fold. Pass --spend-the-sealed-fold to say so deliberately."
        )

    figures = build(args.url, spend_the_sealed_fold=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(figures, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.out}")
    print(f"  corpus {figures['corpus']['games']} games, {len(figures['folds'])} folds")
    print(f"  sealed {figures['sealed']['accuracy']:.4f} acc / "
          f"{figures['sealed']['log_loss']:.4f} ll / {figures['sealed']['roc_auc']:.4f} auc")
    return 0


if __name__ == "__main__":
    sys.exit(main())
