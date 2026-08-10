"""Tests for corpus curation (F-042, `backend/model/corpus.py`).

Standard library only, so these run in CI — deliberately, because this module decides which rows
T-009 trains on, and `test_dataset.py` (which needs pandas) skips there.

The fixtures build a synthetic season with the same *shape* as the real corpus — 30 franchises
playing a full schedule, plus an All-Star game between ids that appear once — rather than a toy
league, because the assertions being tested are about that shape.
"""

from datetime import UTC, datetime, timedelta

import pytest

from model.corpus import (
    EXPECTED_EXHIBITION_COUNTS,
    MIN_SEASON_GAMES_FOR_A_REAL_TEAM,
    NBA_TEAMS_PER_SEASON,
    CorpusIntegrityError,
    exclude_exhibitions,
    exhibition_team_ids,
    partition_exhibitions,
)
from model.features import Game

_EPOCH = datetime(2024, 1, 1, 19, 0, tzinfo=UTC)


def _game(game_id: str, day: float, home: str, away: str, season: int = 2024) -> Game:
    return Game(
        game_id=game_id,
        date=_EPOCH + timedelta(days=day),
        season=season,
        home_id=home,
        away_id=away,
        home_score=110,
        away_score=100,
    )


def _franchise_season(season: int = 2024, *, teams: int = NBA_TEAMS_PER_SEASON, rounds: int = 2):
    """A round-robin season: every team plays every other `rounds` times, so each team gets
    `(teams - 1) * rounds` games — comfortably above the threshold, as a real 82-game season is."""
    ids = [f"t{i:02d}" for i in range(teams)]
    games, n = [], 0
    for r in range(rounds):
        for i in range(teams):
            for j in range(i + 1, teams):
                home, away = (ids[i], ids[j]) if r % 2 == 0 else (ids[j], ids[i])
                games.append(_game(f"s{season}-g{n}", n * 0.01, home, away, season))
                n += 1
    return games


def _all_star(season: int = 2024, count: int = 1):
    """Exhibition games between ids that appear nowhere else — the real corpus's shape."""
    return [
        _game(f"s{season}-as{i}", 500 + i, f"phantom{season}a{i}", f"phantom{season}b{i}", season)
        for i in range(count)
    ]


def test_a_clean_season_has_no_exhibitions():
    games = _franchise_season()
    assert exhibition_team_ids(games) == frozenset()
    nba, exhibitions = partition_exhibitions(games)
    assert exhibitions == []
    assert len(nba) == len(games)


def test_exhibition_ids_are_identified_by_games_played():
    games = [*_franchise_season(), *_all_star(count=2)]
    ids = exhibition_team_ids(games)
    assert ids == {"phantom2024a0", "phantom2024b0", "phantom2024a1", "phantom2024b1"}
    assert all(not t.startswith("t") for t in ids)


def test_partition_removes_exactly_the_exhibition_games():
    season = _franchise_season()
    games = [*season, *_all_star(count=3)]
    nba, exhibitions = partition_exhibitions(games)
    assert len(exhibitions) == 3
    assert len(nba) == len(season)
    assert {g.game_id for g in nba} == {g.game_id for g in season}


def test_a_game_mixing_a_franchise_and_an_exhibition_id_is_excluded():
    """No real game does this today. A rule that only caught both-sides cases would silently pass
    one that did, which is why the check is `or`, not `and`."""
    games = [*_franchise_season(), _game("mixed", 500, "t00", "phantom-x")]
    nba, exhibitions = partition_exhibitions(games)
    assert [g.game_id for g in exhibitions] == ["mixed"]
    assert "phantom-x" in exhibition_team_ids(games)


def test_identification_is_per_season_not_across_the_corpus():
    """A team real in one season must not be condemned by a season it sits out — and an exhibition
    id must not be rescued by appearing in several seasons."""
    games = [
        *_franchise_season(2024),
        *_franchise_season(2025, teams=NBA_TEAMS_PER_SEASON),
        # the same phantom id appearing once in each of two seasons: still an exhibition in both
        _game("as-2024", 500, "phantom-shared", "phantom-other", 2024),
        _game("as-2025", 500, "phantom-shared", "phantom-other", 2025),
    ]
    ids = exhibition_team_ids(games)
    assert "phantom-shared" in ids
    assert not any(t.startswith("t") for t in ids)


def test_exclude_verifies_every_season_keeps_exactly_thirty_teams():
    games = [*_franchise_season(), *_all_star(count=1)]
    kept = exclude_exhibitions(games, expected={2024: 1})
    assert len({g.home_id for g in kept} | {g.away_id for g in kept}) == NBA_TEAMS_PER_SEASON


def test_a_season_left_with_the_wrong_team_count_is_refused():
    """The assertion that turns an upstream shape change into a loud failure rather than a model
    quietly trained on the wrong rows."""
    games = [*_franchise_season(teams=29), *_all_star(count=1)]
    with pytest.raises(CorpusIntegrityError, match="do not have exactly 30 team ids"):
        exclude_exhibitions(games, expected={2024: 1})


def test_an_exhibition_count_that_does_not_match_the_pin_is_refused():
    games = [*_franchise_season(), *_all_star(count=3)]
    with pytest.raises(CorpusIntegrityError, match="do not match the pinned expected"):
        exclude_exhibitions(games, expected={2024: 1})


def test_an_unpinned_season_is_refused_rather_than_curated_unverified():
    """F-028's lesson, applied here: D-017 adds a season, and nothing keeps two structures in sync
    except a check that refuses the gap."""
    games = [*_franchise_season(2027), *_all_star(2027, count=1)]
    with pytest.raises(CorpusIntegrityError, match="no pinned exhibition count"):
        exclude_exhibitions(games, expected={2024: 1})


def test_verification_of_counts_can_be_opted_out_but_the_team_check_still_runs():
    """`expected=None` is for measuring a new season before its count is pinned. It must not also
    disable the structural check — that one needs no pin to be meaningful."""
    games = [*_franchise_season(2027), *_all_star(2027, count=2)]
    kept = exclude_exhibitions(games, expected=None)
    assert len(kept) == len(_franchise_season(2027))

    with pytest.raises(CorpusIntegrityError, match="do not have exactly 30 team ids"):
        exclude_exhibitions([*_franchise_season(2027, teams=28), *_all_star(2027)], expected=None)


def test_the_pinned_counts_are_the_ones_measured_from_the_real_corpus():
    """Guards the constants themselves against an edit that makes a failure "go away" — the same
    role EXPECTED_COMPLETED_COUNTS plays for the loader."""
    assert EXPECTED_EXHIBITION_COUNTS == {2022: 1, 2023: 1, 2024: 1, 2025: 3, 2026: 4}
    assert sum(EXPECTED_EXHIBITION_COUNTS.values()) == 10
    assert MIN_SEASON_GAMES_FOR_A_REAL_TEAM == 20
    # The threshold must sit clear of BOTH populations observed in the corpus (<=3 and >=82).
    assert 3 < MIN_SEASON_GAMES_FOR_A_REAL_TEAM < 82
