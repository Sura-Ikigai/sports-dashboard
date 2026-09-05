"""Expanding-window walk-forward folds (T-007).

Three folds, each training only on seasons that precede its test season (D-013):

    fold 1   train 2022-2023            test 2024
    fold 2   train 2022-2023-2024       test 2025
    fold 3   train 2022-2023-2024-2025  test 2026   <- the sealed headline

Why three and not one sealed season: a single 1,326-game season carries roughly ±2.6 points of
accuracy noise at 95% confidence, so a 62% gate decided on one season is substantially decided by
chance. Three folds give ~3,900 evaluation games plus a fold-to-fold spread, while the last fold
stays untouched as the headline. Random k-fold is rejected outright — it puts future games in the
training set for past ones, which is the exact leakage this project exists to design out.

## The integrity guarantee, and why it is temporal rather than nominal

T-007's security note is one line: *a fold must never train on its own future.* Stating that in terms
of season numbers ("every training season is less than the test season") is necessary but not
sufficient — it is a claim about labels, and labels can lie. What actually matters is that no
training **game** is dated at or after the first test **game**.

Those coincide here, and that is a measured fact rather than an assumption: NBA seasons are disjoint
in time, each separated from the next by a 120-133 day offseason (verified across 2022-2026 —
2022 ends 2022-06-17, 2023 opens 2022-10-18). So `split_games` asserts the temporal property
directly. If a future source ever labels seasons differently, or backfills a game into the wrong
season, the season-number check would pass and the temporal one would fire.

## Warm-up seasons are Elo state and nothing else (D-037, T-029)

The corpus carries 2016-2019 so that fold 1 trains on **converged** Elo ratings rather than on
burn-in noise. Those seasons must reach `elo.Timeline` and must never reach the estimator: the
pre-2020 home-advantage regime is a different game, and a fold that trains on it is fitting a
constant that no longer holds. The plan's security note for this task is one line — *a fold must
never train on a different home-advantage regime* — and it is enforced twice here, because the two
routes in are different in kind:

  - **Nominally**, at construction. `Fold` refuses to name a warm-up season at all, so a fold that
    would train on one is not a thing that exists and then gets caught. This closes the route where
    someone writes the fold set out wrongly.

  - **Temporally**, in `split_games`. That is the only half that can catch a warm-up *game* carrying
    a modeling season's label — which the season-number check passes by construction, because the
    label is what it reads. Same reasoning as the train/test assertion above, applied to the other
    boundary: labels are a claim, dates are the fact. The two eras are separated by **858 days**
    (last warm-up game 2019-06-14, first modeling game 2021-10-19, with no 2020 or 2021 season
    pinned), so `corpus.WARMUP_ERA_END` sits in a gap nothing legitimate is near.

Warm-up games in the input to `split_games` are **dropped, not refused**. That is the shape T-030's
call actually has: Elo needs the whole corpus in its `Context` and the estimator must see none of
it, so one collection is passed to both and this module is what separates them.
`corpus.partition_warmup` is the ergonomic half — it makes the right thing easy; this module makes
the wrong thing impossible, and neither substitutes for the other.

Standard library only (D-021), like `features` and `corpus`: this decides which rows T-009 trains on,
so its tests must run in CI, and `dataset.py`'s do not. It is also why `WARMUP_SEASONS` lives in
`corpus` rather than in `loader`, where the download pins are — `loader` is unavoidably pandas, and
one definition of "which seasons may be trained on" had to sit somewhere both could reach.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .corpus import (
    WARMUP_ERA_END,
    WARMUP_SEASONS,
    assert_curated,
    is_warmup_season,
)
from .features import Game


class FoldError(ValueError):
    """Raised when a fold is malformed, or when splitting a collection would violate the
    train-precedes-test guarantee."""


@dataclass(frozen=True, slots=True)
class Fold:
    """One train/test split, named by season.

    Validated at construction rather than at use: a fold that trains on its own future is not a
    thing that should be constructible and then caught later.
    """

    train_seasons: tuple[int, ...]
    test_season: int

    def __post_init__(self) -> None:
        if not self.train_seasons:
            raise FoldError(f"fold testing on {self.test_season} has no training seasons")
        if len(set(self.train_seasons)) != len(self.train_seasons):
            raise FoldError(f"fold {self} repeats a training season")
        # D-037, the nominal half. A warm-up season is Elo state and nothing else, so a fold naming
        # one is not a fold to catch later — it is a fold that should not be constructible.
        warmup = [s for s in self.seasons if is_warmup_season(s)]
        if warmup:
            raise FoldError(
                f"season(s) {warmup} are warm-up seasons {WARMUP_SEASONS} — they exist to converge "
                "Elo ratings and must never become training or test rows (D-037). Their "
                "home-advantage regime predates the modeling window, so a fold that names one is "
                "training on a different game."
            )
        late = [s for s in self.train_seasons if s >= self.test_season]
        if late:
            raise FoldError(
                f"training season(s) {late} are not before test season {self.test_season} — a fold "
                "must never train on its own future"
            )

    @property
    def seasons(self) -> tuple[int, ...]:
        """Every season this fold touches, train then test."""
        return (*self.train_seasons, self.test_season)

    def __str__(self) -> str:
        return f"train {'-'.join(map(str, self.train_seasons))} → test {self.test_season}"


# The three folds of D-013/D-009. Written out rather than generated: this is the evaluation protocol,
# and a literal is auditable in a way a loop over a range is not.
WALK_FORWARD_FOLDS: tuple[Fold, ...] = (
    Fold((2022, 2023), 2024),
    Fold((2022, 2023, 2024), 2025),
    Fold((2022, 2023, 2024, 2025), 2026),
)

#: The sealed fold — its test season is the headline number (D-013), and nothing may be tuned on it.
SEALED_FOLD: Fold = WALK_FORWARD_FOLDS[-1]


def walk_forward_folds() -> tuple[Fold, ...]:
    """The three expanding-window folds, oldest test season first."""
    return WALK_FORWARD_FOLDS


def split_games(games: Sequence[Game], fold: Fold) -> tuple[list[Game], list[Game]]:
    """Partition `games` into (train, test) for `fold`, verifying the split rather than trusting it.

    Four checks, in the order they can fail usefully:

      1. **The input is curated** (F-067). `load_games` excludes exhibitions by default, but that is a
         property of the producer; three documented paths reach a consumer uncurated with no symptom
         at all, because an exhibition row has ordinary-looking features and a coin-flip label.
      2. **Every season the fold names is present.** A missing season would otherwise yield a smaller
         fold — or an empty test set — and report a perfectly healthy-looking accuracy over whatever
         remained.
      3. **Nothing outside the fold leaks in.** Games from seasons the fold does not name are dropped,
         and that is intentional (fold 1 must not see 2025), so it is asserted rather than assumed.
      4. **Train strictly precedes test in time** — the guarantee that actually matters, and the one
         a season-number comparison only approximates. See the module docstring.
      5. **No row comes from the warm-up era** (D-037). `Fold` already refuses to name a warm-up
         season, so the *nominal* route is shut before this function runs; this is the temporal one,
         and it is the only one that can catch a warm-up game carrying a modeling season's label.

    Warm-up games in the input are **dropped, not refused**. That is the intended shape of T-030's
    call: Elo needs the whole corpus in its Context and the estimator must see none of it, so one
    collection is passed to both and this function is what separates them.
    """
    assert_curated(games)

    by_season: dict[int, list[Game]] = {}
    for game in games:
        by_season.setdefault(game.season, []).append(game)

    missing = [s for s in fold.seasons if s not in by_season]
    if missing:
        raise FoldError(
            f"fold [{fold}] names season(s) {missing} that are absent from the given games — "
            "refusing to evaluate a fold that is quietly smaller than it claims to be"
        )

    train = [g for s in fold.train_seasons for g in by_season[s]]
    test = by_season[fold.test_season]

    # D-037, the temporal half — and the only half that can fire here, which is why it is a date
    # check and not a season-label check duplicating `Fold.__post_init__`. A warm-up game mislabelled
    # into a modeling season passes every season-number test ever written; its *date* does not. The
    # gap it sits in is 858 days wide (see `corpus.WARMUP_ERA_END`), so this cannot fire on anything
    # legitimate.
    for label, rows in (("training", train), ("test", test)):
        stale = [g for g in rows if g.date <= WARMUP_ERA_END]
        if stale:
            first = min(stale, key=lambda g: g.date)
            raise FoldError(
                f"fold [{fold}] has {len(stale)} {label} game(s) from the warm-up era, which ended "
                f"at {WARMUP_ERA_END.isoformat()} — the earliest is {first.game_id!r} at "
                f"{first.date.isoformat()}, labelled season {first.season}. Warm-up seasons "
                f"{WARMUP_SEASONS} converge Elo ratings and must never reach the estimator (D-037): "
                "their home-advantage regime predates the modeling window. A game whose season label "
                "says otherwise is telling you the label is wrong."
            )

    latest_train = max(g.date for g in train)
    earliest_test = min(g.date for g in test)
    if latest_train >= earliest_test:
        raise FoldError(
            f"fold [{fold}] has a training game at {latest_train.isoformat()} at or after the first "
            f"test game at {earliest_test.isoformat()} — the season labels say this fold trains only "
            "on the past, and the dates say otherwise. Trust the dates."
        )

    return train, test
