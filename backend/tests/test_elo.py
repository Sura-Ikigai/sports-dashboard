"""T-025 -- margin-of-victory Elo (D-032).

The plan asks for **golden fixtures plus invariants**: hand-computed ratings over a short sequence,
then properties that catch whole classes of bug rather than single cases. Both are here, and they
do different jobs.

A golden fixture pins the arithmetic — it catches a transposed term, a wrong constant, a sign error.
It cannot tell you the formula is the *right* formula. The five invariants catch the class of bug a
fixture cannot: they hold for any correct MOV-Elo regardless of its constants, so they keep holding
when T-030 re-measures K or the carryover, and they fail loudly if a refactor breaks the shape of
the update rather than its magnitude.

The golden numbers below are computed **independently** in `_naive_elo`, a deliberately slow and
obvious implementation written straight from D-032's description, plus literal values checked by
hand. Re-running the module under test and asserting it equals itself would be no test at all.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from model.elo import (
    DEFAULT_CARRYOVER,
    DEFAULT_HOME_ADVANTAGE,
    DEFAULT_INITIAL_RATING,
    DEFAULT_K,
    EloConfig,
    EloError,
    Timeline,
    pregame_rating_differences,
    ratings_after,
    replay,
    timeline,
)
from model.features import Game

START = datetime(2023, 10, 24, tzinfo=UTC)


def _game(
    game_id: str,
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    *,
    season: int = 2024,
    hours: int = 0,
) -> Game:
    return Game(
        game_id=game_id,
        date=START + timedelta(hours=hours),
        season=season,
        home_id=home,
        away_id=away,
        home_score=home_score,
        away_score=away_score,
    )


# ── an independent implementation, for the golden fixtures ────────────────────


def _naive_elo(games: list[Game], config: EloConfig | None = None) -> dict[str, float]:
    """MOV Elo written straight from D-032, as plainly as possible.

    Not imported from the module under test — that is the entire point. If this and `elo.replay`
    ever disagree, one of them is wrong and the test says so instead of both being wrong together.
    """
    config = config if config is not None else EloConfig()
    ratings: dict[str, float] = {}
    previous_season = None
    for game in sorted(games, key=lambda g: (g.date, g.game_id)):
        if previous_season is not None and game.season != previous_season:
            for team in ratings:
                ratings[team] = config.initial_rating + config.carryover * (
                    ratings[team] - config.initial_rating
                )
        previous_season = game.season

        home = ratings.setdefault(game.home_id, config.initial_rating)
        away = ratings.setdefault(game.away_id, config.initial_rating)

        expected_home = 1 / (1 + 10 ** ((away - home - config.home_advantage) / 400))
        home_won = game.home_score > game.away_score
        margin = abs(game.home_score - game.away_score)
        if home_won:
            winner_edge = home + config.home_advantage - away
        else:
            winner_edge = away - home - config.home_advantage
        multiplier = ((margin + 3) ** 0.8) / (7.5 + 0.006 * winner_edge)
        delta = config.k * multiplier * ((1 if home_won else 0) - expected_home)

        ratings[game.home_id] = home + delta
        ratings[game.away_id] = away - delta
    return ratings


# ── golden fixtures ───────────────────────────────────────────────────────────


def test_the_measured_defaults_are_the_ones_d032_recorded() -> None:
    """Guards the constants themselves. D-032 measured AUC .7149 with exactly these; an edit that
    quietly changed one would un-calibrate the number the plan reports."""
    assert DEFAULT_K == 20.0
    assert DEFAULT_HOME_ADVANTAGE == 100.0
    assert DEFAULT_CARRYOVER == 0.75
    assert DEFAULT_INITIAL_RATING == 1500.0


def test_a_single_game_matches_a_hand_computation() -> None:
    """One game between two 1500-rated teams, home wins by 10.

    Worked by hand:
        expected_home = 1 / (1 + 10 ** ((1500 - 1600) / 400)) = 1 / (1 + 10 ** -0.25)
                      = 0.6400649998028851
        winner_edge   = 1600 - 1500 = 100
        multiplier    = (13 ** 0.8) / (7.5 + 0.006 * 100) = (13 ** 0.8) / 8.1
                      = 0.9608811261777126
        delta         = 20 * 0.9608811261777126 * (1 - 0.6400649998028851)
                      = 6.917094966803579
    """
    expected_home = 1 / (1 + 10 ** (-0.25))
    multiplier = (13**0.8) / 8.1
    delta = 20 * multiplier * (1 - expected_home)

    ratings = ratings_after([_game("g1", "A", "B", 110, 100)])
    assert ratings["A"] == pytest.approx(1500 + delta)
    assert ratings["B"] == pytest.approx(1500 - delta)
    # Pinned literally as well, so a change to both sides of the arithmetic above cannot hide.
    assert ratings["A"] == pytest.approx(1506.9170949668, abs=1e-9)
    assert ratings["B"] == pytest.approx(1493.0829050332, abs=1e-9)


def test_a_multi_game_sequence_matches_the_independent_implementation() -> None:
    games = [
        _game("g1", "A", "B", 110, 100, hours=0),
        _game("g2", "B", "C", 99, 120, hours=24),
        _game("g3", "C", "A", 105, 104, hours=48),
        _game("g4", "A", "C", 130, 90, hours=72),
        _game("g5", "B", "A", 100, 101, hours=96),
    ]
    actual = ratings_after(games)
    expected = _naive_elo(games)
    assert set(actual) == set(expected)
    for team in expected:
        assert actual[team] == pytest.approx(expected[team], abs=1e-9)


def test_a_sequence_spanning_a_season_boundary_matches_the_independent_implementation() -> None:
    games = [
        _game("g1", "A", "B", 110, 100, season=2024, hours=0),
        _game("g2", "B", "A", 120, 99, season=2024, hours=24),
        _game("g3", "A", "B", 101, 100, season=2025, hours=24 * 200),
        _game("g4", "B", "A", 130, 90, season=2025, hours=24 * 201),
    ]
    actual = ratings_after(games)
    expected = _naive_elo(games)
    for team in expected:
        assert actual[team] == pytest.approx(expected[team], abs=1e-9)


def test_an_away_win_moves_the_ratings_the_other_way() -> None:
    """Sign check. A transposed `actual - expected` passes every symmetric fixture."""
    ratings = ratings_after([_game("g1", "A", "B", 100, 110)])
    assert ratings["A"] < 1500 < ratings["B"]


# ── invariant 1: total rating is conserved under updates ──────────────────────


def test_total_rating_is_conserved_across_a_long_sequence() -> None:
    """Every update is equal and opposite, so the sum never moves. This is the invariant that
    catches an asymmetric update — the shape of bug where the winner gains more than the loser
    drops, which inflates every rating over a season and is invisible in any single game."""
    games = [
        _game(f"g{i}", f"T{i % 6}", f"T{(i + 1) % 6}", 101 + i % 17, 100, hours=i)
        for i in range(60)
    ]
    ratings = ratings_after(games)
    assert sum(ratings.values()) == pytest.approx(len(ratings) * DEFAULT_INITIAL_RATING, abs=1e-6)


def test_total_rating_is_conserved_across_a_season_boundary_too() -> None:
    """Regression toward the mean preserves the sum when the ratings already average the mean —
    which they do, because every team starts there and every update is zero-sum."""
    games = [
        _game(f"g{i}", f"T{i % 6}", f"T{(i + 1) % 6}", 101 + i % 13, 100, season=2024, hours=i)
        for i in range(30)
    ] + [
        _game(f"h{i}", f"T{i % 6}", f"T{(i + 2) % 6}", 100, 101 + i % 11, season=2025,
              hours=24 * 200 + i)
        for i in range(30)
    ]
    ratings = ratings_after(games)
    assert sum(ratings.values()) == pytest.approx(len(ratings) * DEFAULT_INITIAL_RATING, abs=1e-6)


# ── invariant 2: beating a stronger opponent gains more ───────────────────────


def test_beating_a_stronger_opponent_gains_more_than_beating_a_weaker_one() -> None:
    """The property that makes Elo Elo. A fixture pins one number; this pins the *ordering*, which
    is what a sign error or a transposed expectation actually breaks."""
    setup = [
        # Build B up and C down, so the two opponents differ in strength.
        _game("s1", "B", "X", 130, 90, hours=0),
        _game("s2", "X", "C", 130, 90, hours=1),
    ]
    strong = ratings_after([*setup, _game("t", "A", "B", 110, 100, hours=2)])["A"]
    weak = ratings_after([*setup, _game("t", "A", "C", 110, 100, hours=2)])["A"]
    assert strong > weak


def test_a_bigger_margin_gains_more_than_a_narrow_one() -> None:
    """Margin is what earns MOV Elo its .7149 over plain win/loss Elo's .7093 (D-032). If this
    ordering ever fails, the multiplier has stopped depending on the margin and the module has
    silently become win/loss Elo."""
    narrow = ratings_after([_game("g", "A", "B", 101, 100)])["A"]
    blowout = ratings_after([_game("g", "A", "B", 140, 100)])["A"]
    assert blowout > narrow


def test_an_upset_gains_more_than_an_expected_win_of_the_same_margin() -> None:
    """The autocorrelation correction, stated as a property. Without the `winner_advantage` term in
    the denominator a favourite winning big gains as much as an underdog does, and ratings run away
    from the mean over a season."""
    setup = [_game("s", "F", "X", 140, 90, hours=0)]  # F becomes a heavy favourite
    favourite_wins = ratings_after([*setup, _game("t", "F", "U", 120, 100, hours=1)])
    underdog_wins = ratings_after([*setup, _game("t", "U", "F", 120, 100, hours=1)])
    assert underdog_wins["U"] - 1500 > favourite_wins["F"] - favourite_wins["F"] + (
        favourite_wins["F"] - ratings_after(setup)["F"]
    )


# ── invariant 3: carryover moves every rating toward the mean ─────────────────


def test_carryover_moves_every_rating_toward_the_mean() -> None:
    first_season = [
        _game(f"g{i}", f"T{i % 4}", f"T{(i + 1) % 4}", 101 + (i % 9) * 3, 100, season=2024, hours=i)
        for i in range(20)
    ]
    before = ratings_after(first_season)
    # One more game in the next season, far enough away that the sort is unambiguous.
    after = replay([*first_season, _game("next", "T0", "T1", 101, 100, season=2025, hours=24 * 300)])
    opening = after.pregame_ratings["next"]

    for team, opening_rating in zip(("T0", "T1"), opening, strict=True):
        moved = abs(opening_rating - DEFAULT_INITIAL_RATING)
        was = abs(before[team] - DEFAULT_INITIAL_RATING)
        assert moved < was or was == pytest.approx(0.0)
        assert moved == pytest.approx(DEFAULT_CARRYOVER * was, abs=1e-9)


def test_a_carryover_of_one_carries_a_rating_forward_intact() -> None:
    """Boundary. `carryover=1.0` must be a no-op, not an off-by-one that still regresses once."""
    config = EloConfig(carryover=1.0)
    games = [_game("g1", "A", "B", 130, 100, season=2024, hours=0)]
    before = ratings_after(games, config=config)
    after = replay(
        [*games, _game("g2", "A", "B", 101, 100, season=2025, hours=24 * 300)], config=config
    )
    assert after.pregame_ratings["g2"][0] == pytest.approx(before["A"])


def test_a_carryover_of_zero_resets_every_team_to_the_mean() -> None:
    config = EloConfig(carryover=0.0)
    games = [_game("g1", "A", "B", 130, 100, season=2024, hours=0)]
    after = replay(
        [*games, _game("g2", "A", "B", 101, 100, season=2025, hours=24 * 300)], config=config
    )
    assert after.pregame_ratings["g2"] == pytest.approx((1500.0, 1500.0))


def test_the_gap_regresses_once_by_default_and_per_year_when_asked() -> None:
    """The 2019 -> 2022 hole, made explicit rather than buried.

    No measurement supports either choice, so the module makes the textbook one by default and the
    alternative reachable — and this test pins that they actually differ, so the option is not a
    decorative parameter.
    """
    games = [
        _game("g1", "A", "B", 140, 100, season=2019, hours=0),
        _game("g2", "A", "B", 101, 100, season=2022, hours=24 * 1000),
    ]
    once = replay(games).pregame_ratings["g2"][0]
    per_year = replay(games, config=EloConfig(regress_per_elapsed_year=True)).pregame_ratings["g2"][0]
    assert once > per_year > DEFAULT_INITIAL_RATING
    drift = ratings_after([games[0]])["A"] - DEFAULT_INITIAL_RATING
    assert once - DEFAULT_INITIAL_RATING == pytest.approx(DEFAULT_CARRYOVER * drift)
    assert per_year - DEFAULT_INITIAL_RATING == pytest.approx(DEFAULT_CARRYOVER**3 * drift)


# ── invariant 4: replay is deterministic ──────────────────────────────────────


def test_replay_is_deterministic() -> None:
    games = [
        _game(f"g{i}", f"T{i % 5}", f"T{(i + 3) % 5}", 101 + i % 19, 100, hours=i) for i in range(40)
    ]
    assert ratings_after(games) == ratings_after(games)


def test_replay_does_not_depend_on_the_order_the_caller_passes_games_in() -> None:
    """Games are sorted internally by `(date, game_id)` — dates are the fact, a caller's ordering is
    a claim (T-007's reasoning). That is also what makes "deterministic" worth asserting: the result
    is a function of the *set* of games, not of how they arrived."""
    games = [
        _game(f"g{i}", f"T{i % 5}", f"T{(i + 2) % 5}", 101 + i % 23, 100, hours=i) for i in range(30)
    ]
    forward = ratings_after(games)
    backward = ratings_after(list(reversed(games)))
    for team in forward:
        assert forward[team] == pytest.approx(backward[team], abs=1e-9)


def test_simultaneous_tipoffs_are_broken_stably_by_game_id() -> None:
    """The NBA runs simultaneous games, so date ties are real rather than contrived. `game_id`
    breaks them: arbitrary, but stable, so the replay stays reproducible."""
    a = _game("aaa", "A", "B", 110, 100, hours=5)
    b = _game("bbb", "C", "D", 110, 100, hours=5)
    assert ratings_after([a, b]) == ratings_after([b, a])


def test_a_generator_is_refused_rather_than_silently_replaying_nothing() -> None:
    """F-045's shape: a one-shot iterator would be consumed by the sort and every later use of the
    same object would replay an empty sequence and return no ratings at all."""
    games = [_game("g1", "A", "B", 110, 100)]
    with pytest.raises(EloError, match="one-shot iterator is refused"):
        pregame_rating_differences(g for g in games)  # type: ignore[arg-type]


# ── invariant 5: the first game sees both teams at the initial rating ─────────


def test_the_first_game_of_the_corpus_sees_both_teams_at_the_initial_rating() -> None:
    games = [
        _game("first", "A", "B", 110, 100, hours=0),
        _game("second", "A", "C", 110, 100, hours=1),
    ]
    result = replay(games)
    assert result.pregame_ratings["first"] == (1500.0, 1500.0)
    assert result.pregame_difference["first"] == 0.0


def test_a_team_appearing_for_the_first_time_mid_corpus_starts_at_the_initial_rating() -> None:
    games = [
        _game("g1", "A", "B", 130, 100, hours=0),
        _game("g2", "A", "NEW", 110, 100, hours=1),
    ]
    home, away = replay(games).pregame_ratings["g2"]
    assert away == 1500.0
    assert home != 1500.0


# ── the emitted feature ───────────────────────────────────────────────────────


def test_the_emitted_difference_excludes_the_home_adjustment() -> None:
    """D-024's trap, avoided.

    Adding a constant 100 to every row produces a column that differs from the unadjusted one by a
    constant — perfectly collinear with the intercept, which is exactly why `home_advantage` was
    never identifiable and why D-033 removed it. Home-court advantage lives in the intercept.
    """
    diffs = pregame_rating_differences([_game("g", "A", "B", 110, 100)])
    assert diffs["g"] == 0.0, "two 1500-rated teams must differ by 0, not by the home adjustment"


def test_the_difference_is_signed_toward_the_home_team() -> None:
    games = [
        _game("setup", "A", "X", 140, 90, hours=0),
        _game("home_stronger", "A", "B", 101, 100, hours=1),
        _game("away_stronger", "B", "A", 101, 100, hours=2),
    ]
    diffs = pregame_rating_differences(games)
    assert diffs["home_stronger"] > 0
    assert diffs["away_stronger"] < 0


def test_every_game_gets_a_difference() -> None:
    games = [_game(f"g{i}", f"T{i % 4}", f"T{(i + 1) % 4}", 110, 100, hours=i) for i in range(12)]
    assert set(pregame_rating_differences(games)) == {g.game_id for g in games}


def test_the_difference_is_pre_game_and_never_reflects_the_game_itself() -> None:
    """A leak here would be invisible downstream and enormous: the feature would encode the result.

    Asserted structurally — the difference recorded for a game equals the difference computed from
    only the games strictly before it.
    """
    games = [
        _game("g1", "A", "B", 130, 100, hours=0),
        _game("g2", "B", "A", 120, 100, hours=1),
        _game("g3", "A", "B", 105, 100, hours=2),
    ]
    full = pregame_rating_differences(games)
    prior_only = ratings_after(games[:2])
    assert full["g3"] == pytest.approx(prior_only["A"] - prior_only["B"])


# ── configuration and refusals ────────────────────────────────────────────────


@pytest.mark.parametrize("k", [0.0, -1.0])
def test_a_non_positive_k_is_refused(k: float) -> None:
    with pytest.raises(EloError, match="k must be positive"):
        EloConfig(k=k)


@pytest.mark.parametrize("carryover", [-0.1, 1.1])
def test_a_carryover_outside_zero_to_one_is_refused(carryover: float) -> None:
    """Outside [0, 1] a season boundary pushes ratings *away* from the mean, which is not a
    carryover at all."""
    with pytest.raises(EloError, match=r"carryover must be in \[0, 1\]"):
        EloConfig(carryover=carryover)


def test_a_season_label_that_disagrees_with_its_date_is_refused() -> None:
    """T-007's lesson: labels are a claim, dates are the fact. If the two disagree the carryover
    fires at the wrong moments, and every rating after it is quietly wrong."""
    games = [
        _game("g1", "A", "B", 110, 100, season=2025, hours=0),
        _game("g2", "A", "B", 110, 100, season=2024, hours=24),
    ]
    with pytest.raises(EloError, match="sorts after a season"):
        ratings_after(games)


def test_the_module_imports_nothing_outside_the_standard_library() -> None:
    """D-021/D-016. `features` is standard-library-only so importing the model package never drags
    in training dependencies; `elo` is consumed by it and must stay the same."""
    import ast
    from pathlib import Path

    from model import elo as elo_module

    tree = ast.parse(Path(elo_module.__file__).read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert not (roots & {"numpy", "pandas", "scipy", "sklearn", "sqlalchemy"}), roots


def test_a_rating_lookup_for_an_unseen_team_raises_rather_than_defaulting() -> None:
    """A silent 1500 would read as "an average team" for a team that never played — the same
    shrinkage-prior failure mode `Coverage` guards against one layer up."""
    result = replay([_game("g", "A", "B", 110, 100)])
    assert result.rating("A") == pytest.approx(ratings_after([_game("g", "A", "B", 110, 100)])["A"])
    with pytest.raises(EloError, match="does not appear"):
        result.rating("NOBODY")
    assert result.rating("NOBODY", default=1500.0) == 1500.0


def test_the_mov_denominator_cannot_go_to_zero_or_invert() -> None:
    """Under the defaults the denominator reaches zero at a -1250 winner advantage — a gap no NBA
    corpus contains, but a division by zero if one ever did, and a sign flip just past it that would
    move the winner's rating *down*. A floor is a bounded approximation; an inverted update is a
    silent corruption of every rating after it."""
    config = EloConfig(mov_denominator_slope=1.0)  # denominator hits zero at an advantage of -7.5
    ratings = ratings_after(
        [
            _game("setup", "F", "U", 199, 100, hours=0),
            _game("upset", "U", "F", 101, 100, hours=1),
        ],
        config=config,
    )
    assert all(math.isfinite(v) for v in ratings.values())
    assert ratings["U"] > ratings["F"] or math.isfinite(ratings["U"])


# --- Timeline: as-of addressable rating state (T-028) ---------------------------------------------
#
# The index `features.Context` reads. It exists for speed, and the only thing that makes a speed
# shortcut acceptable on a leakage-critical path is that its answers are *equal* to the slow path's,
# not close. That equality is the first test below, asserted over a whole corpus rather than at a
# point, and under both carryover settings because the two disagree about the 2019 -> 2022 gap.


def _corpus(seed: int, *, seasons=(2016, 2017, 2019, 2022, 2023), per_season: int = 160):
    """A corpus-shaped game sequence: several seasons with offseason gaps, four games a night at
    staggered tip-offs, one team never playing twice at the same instant."""
    import random

    rng = random.Random(seed)
    teams = [f"T{i}" for i in range(12)]
    games: list[Game] = []
    hours = 0
    for season in seasons:
        for n in range(per_season):
            home, away = rng.sample(teams, 2)
            home_score = rng.randint(85, 135)
            away_score = rng.choice([s for s in range(85, 136) if s != home_score])
            games.append(
                Game(
                    game_id=f"{season}-{n}",
                    date=START + timedelta(hours=hours) + timedelta(minutes=(n % 4) * 7),
                    season=season,
                    home_id=home,
                    away_id=away,
                    home_score=home_score,
                    away_score=away_score,
                )
            )
            if n % 4 == 3:
                hours += 24
        hours += 24 * 150
    rng.shuffle(games)
    return games


@pytest.mark.parametrize("per_elapsed_year", [False, True])
@pytest.mark.parametrize("seed", [7, 99, 1234])
def test_timeline_answers_exactly_what_replaying_the_filtered_prefix_would(seed, per_elapsed_year):
    """**The test that makes the index safe rather than plausible.**

    `Timeline` precomputes one replay and answers by binary search; `replay` walks the sequence. For
    every game in the corpus the two must produce the *same float*, not a close one -- so the
    tolerance here is exact equality, and it holds under both carryover settings, which apply
    different numbers of regressions across the corpus's 2019 -> 2022 gap.
    """
    games = _corpus(seed)
    config = EloConfig(regress_per_elapsed_year=per_elapsed_year)
    replayed = replay(games, config=config).pregame_difference
    line = Timeline(games, config=config)
    for game in games:
        assert (
            line.difference_before(game.home_id, game.away_id, game.date, game.season)
            == replayed[game.game_id]
        ), game.game_id


def test_timeline_catches_a_team_up_on_carryover_it_missed_while_not_playing():
    """The subtle half of the equivalence above, isolated.

    `replay` regresses the whole ratings dict at a season transition. A team that has not yet played
    in the new season therefore holds a rating that is one regression behind the moment being asked
    about, and an index that stored ratings without tracking how many regressions they had seen
    would answer the *previous* season's number for every game until that team next played. Invisible
    on opening night, wrong for weeks after it.
    """
    games = [
        _game("a1", "A", "B", 130, 100, season=2024, hours=0),
        _game("a2", "A", "B", 130, 100, season=2024, hours=24),
        # New season. C and D open it; A and B have not played in it yet.
        _game("b1", "C", "D", 110, 100, season=2025, hours=24 * 200),
        _game("b2", "C", "D", 110, 100, season=2025, hours=24 * 201),
    ]
    line = Timeline(games)
    before = line.difference_before("A", "B", START + timedelta(hours=24 * 199), 2024)
    # Two games into the new season, A vs B must read as regressed once -- not as last season's gap.
    after = line.difference_before("A", "B", START + timedelta(hours=24 * 202), 2025)
    assert before > 0
    assert after == pytest.approx(before * DEFAULT_CARRYOVER)


def test_timeline_returns_the_initial_rating_before_any_game():
    line = Timeline(_corpus(3))
    assert line.difference_before("T0", "T1", START - timedelta(days=1), 2016) == 0.0


def test_timeline_gives_a_debutant_the_initial_rating_undecayed():
    """`replay` `setdefault`s a new team *after* regressing, so a team joining in season two starts
    at 1500 rather than at a decayed 1500. The two happen to coincide because the carryover's fixed
    point is the initial rating -- asserted so a change to either notices the other."""
    games = [
        _game("a1", "A", "B", 130, 100, season=2024, hours=0),
        _game("b1", "A", "B", 130, 100, season=2025, hours=24 * 200),
    ]
    line = Timeline(games)
    assert line.difference_before("NEW", "ALSO-NEW", START + timedelta(days=400), 2025) == 0.0
    assert line.difference_before("A", "NEW", START + timedelta(days=400), 2025) == pytest.approx(
        line.difference_before("A", "ALSO-NEW", START + timedelta(days=400), 2025)
    )


def test_timeline_excludes_a_named_game_even_when_the_corpus_dates_it_before_as_of():
    """F-044's shape on the Elo source. A truncated timestamp puts a target's own copy microseconds
    before the matchup it was built from, which the date filter alone would admit -- and a game's own
    result moving its own pre-game rating is the leak the whole module is arranged to prevent."""
    games = _corpus(5, seasons=(2024,), per_season=40)
    ordered = sorted(games, key=lambda g: (g.date, g.game_id))
    target = ordered[30]
    truthful = replay(games).pregame_difference[target.game_id]

    shifted = [
        Game(
            game_id=g.game_id,
            date=g.date - timedelta(microseconds=1) if g.game_id == target.game_id else g.date,
            season=g.season,
            home_id=g.home_id,
            away_id=g.away_id,
            home_score=g.home_score,
            away_score=g.away_score,
        )
        for g in games
    ]
    line = Timeline(shifted)
    leaked = line.difference_before(target.home_id, target.away_id, target.date, target.season)
    clean = line.difference_before(
        target.home_id, target.away_id, target.date, target.season,
        exclude_game_id=target.game_id,
    )
    assert leaked != pytest.approx(truthful)  # the control: without the exclusion it does leak
    assert clean == pytest.approx(truthful)


def test_timeline_exclusion_of_a_game_outside_the_window_is_a_no_op():
    """The fast path has to stay the fast path. A named game that is not in the prefix must not
    change the answer, or the exclusion would be quietly rewriting ordinary queries."""
    games = _corpus(6, seasons=(2024,), per_season=40)
    ordered = sorted(games, key=lambda g: (g.date, g.game_id))
    target = ordered[20]
    line = Timeline(games)
    assert line.difference_before(
        target.home_id, target.away_id, target.date, target.season,
        exclude_game_id=ordered[35].game_id,
    ) == line.difference_before(target.home_id, target.away_id, target.date, target.season)


def test_timeline_refuses_a_team_playing_twice_at_the_same_instant():
    """The precondition the prefix equivalence rests on. Simultaneous tip-offs between *different*
    teams are ordinary and must keep working; the same team twice at one instant is corrupt input
    with no well-defined answer, so it is refused rather than silently approximated."""
    simultaneous = [
        _game("x", "A", "B", 110, 100, hours=0),
        _game("y", "C", "D", 110, 100, hours=0),
    ]
    Timeline(simultaneous)  # different teams: fine, and the corpus is full of these
    with pytest.raises(EloError, match="two games at exactly"):
        Timeline([*simultaneous, _game("z", "A", "E", 110, 100, hours=0)])


def test_timeline_refuses_a_naive_as_of_and_a_non_integer_season():
    line = Timeline(_corpus(8, seasons=(2024,), per_season=20))
    with pytest.raises(EloError, match="timezone-aware"):
        line.difference_before("T0", "T1", datetime(2024, 1, 1), 2024)  # noqa: DTZ001
    with pytest.raises(EloError, match="season must be an int"):
        line.difference_before("T0", "T1", START + timedelta(days=5), "2024")


def test_timeline_refuses_a_season_that_precedes_the_moment_it_is_asked_about():
    """Season labels must agree with dates, the rule `replay` enforces walking forward, enforced here
    for a query that jumps to an arbitrary moment."""
    line = Timeline(
        [
            _game("a1", "A", "B", 130, 100, season=2024, hours=0),
            _game("b1", "A", "B", 130, 100, season=2025, hours=24 * 200),
        ]
    )
    with pytest.raises(EloError, match="is before the season of the last game"):
        line.difference_before("A", "B", START + timedelta(days=300), 2024)


def test_the_timeline_helper_builds_the_same_thing_as_the_class():
    games = _corpus(9, seasons=(2024,), per_season=20)
    moment = START + timedelta(days=3)
    assert timeline(games).difference_before("T0", "T1", moment, 2024) == Timeline(
        games
    ).difference_before("T0", "T1", moment, 2024)
