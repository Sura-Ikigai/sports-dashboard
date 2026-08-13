"""Tests for the expanding-window fold generator (T-007, `backend/model/splits.py`).

Standard library only, so these run in CI — this decides which rows T-009 trains on.

Written against the lesson T-006 paid three review rounds for: a test earns its place only if it
would FAIL when the module is subtly wrong. So the fold set is asserted as literals rather than
re-derived, the train-precedes-test property is checked with a fixture whose seasons are deliberately
out of order, and the temporal guard is driven by a fixture where the season labels are correct and
the dates are not — the case a season-number comparison cannot catch.
"""

from datetime import UTC, datetime, timedelta

import pytest

from model.corpus import NBA_TEAMS_PER_SEASON, CorpusIntegrityError
from model.features import Game
from model.splits import (
    SEALED_FOLD,
    WALK_FORWARD_FOLDS,
    Fold,
    FoldError,
    split_games,
    walk_forward_folds,
)

_EPOCH = datetime(2021, 10, 19, 19, 0, tzinfo=UTC)


def _game(gid: str, day: float, home: str, away: str, season: int) -> Game:
    return Game(
        game_id=gid,
        date=_EPOCH + timedelta(days=day),
        season=season,
        home_id=home,
        away_id=away,
        home_score=110,
        away_score=100,
    )


def _season(season: int, *, day_offset: float, teams: int = NBA_TEAMS_PER_SEASON) -> list[Game]:
    """A curated season: 30 franchises, single round-robin, so every team clears `min_games`."""
    ids = [f"t{i:02d}" for i in range(teams)]
    out, n = [], 0
    for i in range(teams):
        for j in range(i + 1, teams):
            out.append(_game(f"s{season}g{n}", day_offset + n * 0.01, ids[i], ids[j], season))
            n += 1
    return out


def _corpus(seasons=(2022, 2023, 2024, 2025, 2026)) -> list[Game]:
    """Seasons laid out disjointly in time, ~365 days apart, as the real corpus is."""
    return [g for k, s in enumerate(seasons) for g in _season(s, day_offset=k * 365)]


# --- the fold set --------------------------------------------------------------------------------


def test_exactly_the_three_expanding_window_folds_are_produced():
    """Asserted as literals, not re-derived from a range — a loop that generated these would agree
    with a loop in the module however wrong both were (the F-055 shape)."""
    assert walk_forward_folds() == (
        Fold((2022, 2023), 2024),
        Fold((2022, 2023, 2024), 2025),
        Fold((2022, 2023, 2024, 2025), 2026),
    )
    assert len(WALK_FORWARD_FOLDS) == 3
    assert [f.test_season for f in WALK_FORWARD_FOLDS] == [2024, 2025, 2026]


def test_every_folds_training_seasons_precede_its_test_season():
    for fold in walk_forward_folds():
        assert all(s < fold.test_season for s in fold.train_seasons), fold


def test_no_season_appears_on_both_sides_of_a_fold():
    for fold in walk_forward_folds():
        assert fold.test_season not in fold.train_seasons, fold
        assert len(set(fold.seasons)) == len(fold.seasons), fold


def test_the_window_actually_expands():
    """Each fold trains on everything the previous one did, plus that fold's test season. A set of
    fixed-width sliding windows would satisfy every other assertion here."""
    # offset pairs, so the sequences are deliberately different lengths — no strict=
    for earlier, later in zip(WALK_FORWARD_FOLDS, WALK_FORWARD_FOLDS[1:], strict=False):
        assert set(earlier.train_seasons) < set(later.train_seasons)
        assert earlier.test_season in later.train_seasons


def test_the_sealed_fold_is_the_last_one_and_tests_the_newest_season():
    assert SEALED_FOLD is WALK_FORWARD_FOLDS[-1]
    assert SEALED_FOLD.test_season == 2026
    assert SEALED_FOLD.test_season > max(f.test_season for f in WALK_FORWARD_FOLDS[:-1])


# --- a Fold refuses to be constructed wrong ------------------------------------------------------


@pytest.mark.parametrize(
    ("train", "test"),
    [((2024, 2025), 2024), ((2025,), 2024), ((2022, 2026), 2024)],
    ids=["test-in-train", "train-after-test", "one-train-season-after-test"],
)
def test_a_fold_that_would_train_on_its_own_future_cannot_be_constructed(train, test):
    with pytest.raises(FoldError, match="must never train on its own future"):
        Fold(train, test)


def test_a_fold_with_no_training_seasons_or_a_repeat_is_refused():
    with pytest.raises(FoldError, match="no training seasons"):
        Fold((), 2024)
    with pytest.raises(FoldError, match="repeats a training season"):
        Fold((2022, 2022), 2024)


# --- splitting -----------------------------------------------------------------------------------


def test_split_puts_every_named_season_on_the_right_side():
    games = _corpus()
    for fold in walk_forward_folds():
        train, test = split_games(games, fold)
        assert {g.season for g in train} == set(fold.train_seasons)
        assert {g.season for g in test} == {fold.test_season}
        assert not ({g.game_id for g in train} & {g.game_id for g in test})


def test_split_drops_seasons_the_fold_does_not_name():
    """Fold 1 must not see 2025 or 2026 — dropping them is the point, so it is asserted."""
    train, test = split_games(_corpus(), WALK_FORWARD_FOLDS[0])
    assert {g.season for g in train} == {2022, 2023}
    assert {g.season for g in test} == {2024}
    assert 2025 not in {g.season for g in [*train, *test]}


def test_split_refuses_a_fold_naming_a_season_that_is_absent():
    """Otherwise the fold is quietly smaller than it claims and reports a healthy-looking number over
    whatever remained."""
    games = _corpus((2022, 2023))  # 2024 missing entirely
    with pytest.raises(FoldError, match=r"season\(s\) \[2024\]"):
        split_games(games, WALK_FORWARD_FOLDS[0])


def test_split_refuses_uncurated_input():
    """F-067 — `load_games` excludes by default, but that is the producer's property. An exhibition
    row has ordinary features and a coin-flip label, so contamination has no symptom here."""
    exhibition = [_game(f"as{i}", 800 + i, "phantomA", "phantomB", 2024) for i in range(2)]
    with pytest.raises(CorpusIntegrityError, match="has not been curated"):
        split_games([*_corpus(), *exhibition], WALK_FORWARD_FOLDS[0])


def test_split_trusts_the_dates_over_the_season_labels():
    """The guarantee that actually matters. Season labels are correct here and the DATES are not — a
    2022-labelled game dated after the 2024 season opens. A season-number comparison cannot see this;
    it is the case the temporal assertion exists for."""
    games = _corpus()
    # a game labelled 2022 but played during the 2024 window (day_offset 2*365)
    intruder = _game("backfilled", 2 * 365 + 5, "t00", "t01", 2022)
    with pytest.raises(FoldError, match="Trust the dates"):
        split_games([*games, intruder], WALK_FORWARD_FOLDS[0])


def test_split_is_temporally_clean_on_every_fold():
    """The property stated positively, across all three folds."""
    games = _corpus()
    for fold in walk_forward_folds():
        train, test = split_games(games, fold)
        assert max(g.date for g in train) < min(g.date for g in test), fold


def test_split_does_not_mutate_its_input():
    games = _corpus()
    before = list(games)
    split_games(games, WALK_FORWARD_FOLDS[0])
    assert games == before
