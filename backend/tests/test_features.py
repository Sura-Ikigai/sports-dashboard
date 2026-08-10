"""Tests for the `features` deep module (T-006).

Follows PLAN-v1's Testing Decisions: assert external behavior -- the values a fixture produces and
the properties that must hold -- never the internal formula. Nothing here restates the arithmetic of
`_shrink`; the expected numbers below were computed by hand from `n/(n+k)` with k=5 and are written
out as literals so a test failure means the module changed, not that both sides changed together.

Three groups carry the acceptance criteria:
  - golden fixtures      -- a hand-built history with known values
  - the leakage property -- adding games dated at or after `as_of` changes nothing (plus a control
                            proving the assertion is not vacuous)
  - shrinkage boundaries -- 0, 1 and k prior games

Standard library only, like the module: CI installs `requirements.txt`, which has no pandas/numpy.
"""

import random
from datetime import UTC, datetime, timedelta

import pytest

from model.features import (
    FEATURE_NAMES,
    FORM_WINDOW,
    MAX_REST_DAYS,
    PRIOR_WIN_RATE,
    SHRINKAGE_K,
    FeatureInputError,
    FeatureLeakageError,
    Game,
    GameHistory,
    Matchup,
    compute_features,
    compute_training_features,
    to_vector,
)

SEASON = 2024
# F-054: deliberately NOT midnight. Every `as_of` here is a whole-day offset from this epoch,
# so a midnight epoch made "truncate as_of to the day" a no-op -- which let a day-granularity
# as-of filter survive the whole suite, including the microsecond test written to catch it.
# 5,360 of the 6,615 real games tip off at a non-midnight instant, so 19:00Z is also the
# realistic choice. Nothing else in the suite depends on the epoch's time-of-day.
_EPOCH = datetime(2024, 1, 1, 19, 0, tzinfo=UTC)


def at(day: float) -> datetime:
    """A tz-aware instant `day` days after the fixture epoch."""
    return _EPOCH + timedelta(days=day)


def game(
    game_id: str,
    day: float,
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    *,
    season: int = SEASON,
    neutral: bool = False,
) -> Game:
    return Game(
        game_id=game_id,
        date=at(day),
        season=season,
        home_id=home,
        away_id=away,
        home_score=home_score,
        away_score=away_score,
        neutral_site=neutral,
    )


def matchup(
    game_id: str, day: float, home: str, away: str, *, season: int = SEASON, neutral: bool = False
) -> Matchup:
    return Matchup(
        game_id=game_id,
        date=at(day),
        season=season,
        home_id=home,
        away_id=away,
        neutral_site=neutral,
    )


# --- golden fixture ------------------------------------------------------------------------------
#
# Five completed games, then A (home) vs B (away) on day 10. Hand-computed, k=5:
#
#   A: day 0 beat C by +10 · day 2 lost to D by -5 · day 4 beat B by +20
#      3 games, 2 wins -> observed 2/3, w = 3/(3+5) = 0.375
#      form  = 0.375*(2/3) + 0.625*0.5 = 0.5625
#      pdiff = 0.375*(25/3)            = 3.125          (margins +10, -5, +20 -> mean 25/3)
#      last game day 4 -> 6 days to tip-off -> capped at MAX_REST_DAYS = 5.0
#
#   B: day 4 lost to A by -20 · day 6 beat C by +9 · day 8 lost to D by -12
#      3 games, 1 win -> observed 1/3, w = 0.375
#      form  = 0.375*(1/3) + 0.625*0.5 = 0.4375
#      pdiff = 0.375*(-23/3)           = -2.875         (margins -20, +9, -12 -> mean -23/3)
#      last game day 8 -> 2 days of rest
#
#   home_advantage  1.0 (not neutral)
#   form_diff       0.5625 - 0.4375 =  0.125
#   rest_diff       5.0    - 2.0    =  3.0
#   point_diff_diff 3.125  - -2.875 =  6.0

GOLDEN_HISTORY = [
    game("g1", 0, "A", "C", 110, 100),
    game("g2", 2, "D", "A", 105, 100),
    game("g3", 4, "A", "B", 120, 100),
    game("g4", 6, "B", "C", 99, 90),
    game("g5", 8, "D", "B", 100, 88),
]
GOLDEN_TARGET = matchup("target", 10, "A", "B")
GOLDEN_AS_OF = at(10)
GOLDEN_EXPECTED = {
    "home_advantage": 1.0,
    "form_diff": 0.125,
    "rest_diff": 3.0,
    "point_diff_diff": 6.0,
}


def test_golden_fixture_produces_the_hand_computed_features():
    assert compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF) == pytest.approx(GOLDEN_EXPECTED)


def test_output_keys_are_exactly_the_declared_feature_names():
    features = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    assert tuple(features) == FEATURE_NAMES


def test_features_are_symmetric_when_the_two_teams_are_swapped():
    """Swapping home and away must negate every difference feature. Catches a sign error that a
    single-fixture assertion cannot -- a swapped subtraction still produces plausible numbers."""
    normal = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    swapped = compute_features(GOLDEN_HISTORY, matchup("target", 10, "B", "A"), GOLDEN_AS_OF)
    for name in ("form_diff", "rest_diff", "point_diff_diff"):
        assert swapped[name] == pytest.approx(-normal[name])
    assert swapped["home_advantage"] == 1.0  # not a difference; unaffected by the swap


def test_a_better_team_gets_a_positive_form_and_point_differential():
    """Direction sanity: the fixture's A is the stronger side, so both accumulating features favour
    it. A model whose coefficients came out backwards would trace to here."""
    features = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    assert features["form_diff"] > 0
    assert features["point_diff_diff"] > 0


# --- the leakage property test -------------------------------------------------------------------
#
# PLAN-v1: "the single most important test in the plan". Adding any game dated at or after `as_of`
# to the input must not change the output. Exact equality, not approx: the claim is "changes
# nothing", and the filtered record set is identical, so the arithmetic is bit-identical too.

FUTURE_TEAMS = ("A", "B", "C", "D", "E")


def _random_future_game(rng: random.Random, index: int, as_of_day: float) -> Game:
    """A completed game dated at or after the as-of moment -- i.e. one that did not exist yet."""
    home, away = rng.sample(FUTURE_TEAMS, 2)
    home_score = rng.randint(85, 135)
    away_score = rng.choice([s for s in range(85, 136) if s != home_score])
    return game(
        f"future-{index}",
        # 0.0 puts a game at exactly `as_of`, the boundary the strict filter has to exclude.
        as_of_day + rng.choice([0.0, 0.0, 0.5, 1.0, 7.0, 60.0]),
        home,
        away,
        home_score,
        away_score,
        season=rng.choice([SEASON, SEASON + 1]),
    )


def test_leakage_adding_games_at_or_after_as_of_does_not_change_the_features():
    rng = random.Random(20260810)
    expected = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    for trial in range(200):
        polluted = list(GOLDEN_HISTORY)
        for n in range(rng.randint(1, 8)):
            polluted.append(_random_future_game(rng, trial * 10 + n, as_of_day=10))
        rng.shuffle(polluted)  # position in the sequence must not matter either
        assert compute_features(polluted, GOLDEN_TARGET, GOLDEN_AS_OF) == expected


def test_leakage_control_adding_a_game_before_as_of_does_change_the_features():
    """The control that makes the test above meaningful. A `compute_features` that ignored history
    entirely, or filtered everything out, would satisfy the leakage property perfectly -- and be
    useless. This asserts the input actually reaches the output when it is allowed to."""
    expected = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    with_past_game = [*GOLDEN_HISTORY, game("past", 9, "A", "E", 130, 90)]
    assert compute_features(with_past_game, GOLDEN_TARGET, GOLDEN_AS_OF) != expected


def test_leakage_a_game_at_exactly_the_as_of_instant_is_excluded():
    """The filter is strict (`<`), not inclusive. This is the boundary the training case rests on."""
    at_boundary = [*GOLDEN_HISTORY, game("boundary", 10, "A", "E", 140, 80)]
    assert compute_features(at_boundary, GOLDEN_TARGET, GOLDEN_AS_OF) == pytest.approx(GOLDEN_EXPECTED)


def test_leakage_a_game_one_microsecond_before_as_of_is_included():
    """...and the exclusion above is about the boundary itself, not about a coarser day-level
    comparison that would also drop legitimate same-day earlier games."""
    just_before = [*GOLDEN_HISTORY, game("just-before", 10 - 1e-6 / 86400, "A", "E", 140, 80)]
    assert compute_features(just_before, GOLDEN_TARGET, GOLDEN_AS_OF) != pytest.approx(GOLDEN_EXPECTED)


def test_leakage_a_completed_game_is_excluded_from_its_own_features():
    """The training case: `as_of` is the target's own tip-off and the target is in the history."""
    target_game = game("target", 10, "A", "B", 111, 100)
    history_with_target = [*GOLDEN_HISTORY, target_game]

    assert compute_training_features(history_with_target, target_game) == pytest.approx(GOLDEN_EXPECTED)
    # ...and identically so whether or not it was there, which is the actual guarantee.
    assert compute_training_features(history_with_target, target_game) == compute_features(
        GOLDEN_HISTORY, target_game.matchup, target_game.date
    )


def test_leakage_as_of_after_tip_off_is_refused():
    with pytest.raises(FeatureLeakageError, match="after tip-off"):
        compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, at(10.001))


def test_as_of_at_tip_off_is_allowed_because_that_is_the_training_rule():
    compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_TARGET.date)


def test_as_of_before_tip_off_is_allowed_because_that_is_the_inference_case():
    features = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, at(7))
    # Predicting from day 7 cannot see B's day-8 loss, so it is a different vector than at day 10.
    assert features != pytest.approx(GOLDEN_EXPECTED)


def test_a_completed_game_cannot_be_passed_as_the_target():
    """The target's own scores are structurally out of reach, not merely ignored."""
    target_game = game("target", 10, "A", "B", 111, 100)
    with pytest.raises(FeatureInputError, match="must be a Matchup"):
        compute_features(GOLDEN_HISTORY, target_game, target_game.date)


# --- shrinkage boundaries: 0, 1 and k prior games ------------------------------------------------
#
# The home team accumulates games against filler opponents so the away team stays at zero, which
# pins the away side at the priors and makes each difference read directly as the home team's
# shrunk value minus its prior.


def _home_streak(wins: int, margin: int, *, start_day: float = 0.0) -> list[Game]:
    fillers = "CDEFGHIJKLMNOPQRSTUVWXYZ"
    return [
        game(f"s{i}", start_day + i, "A", fillers[i], 100 + margin, 100)
        for i in range(wins)
    ]


def _home_results(results: str, *, margin: int = 10, start_day: float = 0.0) -> list[Game]:
    """A history for team A from a result string, e.g. "LLLLLWWWWWW" -- oldest first, one game a day.

    Deliberately supports NON-uniform records. Every form fixture used to be a uniform streak, which
    made the first `FORM_WINDOW` games and the last `FORM_WINDOW` games numerically identical, so
    nothing in the suite could tell a rolling window from a fixed one (F-051).
    """
    fillers = "CDEFGHIJKLMNOPQRSTUVWXYZ"
    out = []
    for i, r in enumerate(results):
        won = r.upper() == "W"
        out.append(
            game(
                f"r{i}",
                start_day + i,
                "A",
                fillers[i % len(fillers)],
                100 + (margin if won else -margin),
                100,
            )
        )
    return out


def test_shrinkage_at_zero_prior_games_yields_the_priors_exactly():
    features = compute_features([], matchup("t", 5, "A", "B"), at(5))
    assert features["form_diff"] == 0.0
    assert features["point_diff_diff"] == 0.0
    # Both teams sit on the prior, so the difference is 0 -- and rest is the cap on both sides too.
    assert features["rest_diff"] == 0.0


def test_shrinkage_at_one_prior_game_weights_it_one_sixth():
    # w = 1/(1+5) = 1/6. form = (1/6)*1.0 + (5/6)*0.5 = 7/12; diff against the away prior (0.5) = 1/12.
    # pdiff = (1/6)*12 + (5/6)*0 = 2.0.
    features = compute_features(_home_streak(1, margin=12), matchup("t", 5, "A", "B"), at(5))
    assert features["form_diff"] == pytest.approx(1 / 12)
    assert features["point_diff_diff"] == pytest.approx(2.0)


def test_shrinkage_at_k_prior_games_lands_exactly_halfway():
    # n = k = 5 -> w = 0.5, the defining boundary: the shrunk value is the midpoint of the team's
    # own observation and the prior.
    features = compute_features(_home_streak(5, margin=10), matchup("t", 6, "A", "B"), at(6))
    observed_win_rate = 1.0
    assert features["form_diff"] == pytest.approx((observed_win_rate + PRIOR_WIN_RATE) / 2 - PRIOR_WIN_RATE)
    assert features["form_diff"] == pytest.approx(0.25)
    assert features["point_diff_diff"] == pytest.approx(5.0)


def test_shrinkage_weight_rises_monotonically_with_games_played():
    """The property D-015 actually asks for: a team with more games is trusted more. Asserted as a
    trend across n rather than by recomputing n/(n+k)."""
    diffs = [
        compute_features(_home_streak(n, margin=10), matchup("t", 20, "A", "B"), at(20))[
            "point_diff_diff"
        ]
        for n in range(0, FORM_WINDOW + 1)
    ]
    assert diffs == sorted(diffs)
    assert diffs[0] == 0.0  # n=0 -> the prior
    assert all(d < 10.0 for d in diffs)  # never reaches the raw observation
    assert diffs[-1] > diffs[1]


def test_form_uses_at_most_the_window_and_shrinks_on_the_window_not_the_season():
    """15 straight wins: the average is over the last 10, and so is n. If n were the season count
    (15), the shrunk value would be 15/20*1.0 + 5/20*0.5 = 0.875 rather than the 5/6 asserted here."""
    features = compute_features(_home_streak(15, margin=10), matchup("t", 20, "A", "B"), at(20))
    n = FORM_WINDOW
    expected_form = (n / (n + SHRINKAGE_K)) * 1.0 + (SHRINKAGE_K / (n + SHRINKAGE_K)) * PRIOR_WIN_RATE
    assert features["form_diff"] == pytest.approx(expected_form - PRIOR_WIN_RATE)
    assert features["form_diff"] == pytest.approx(5 / 6 - 0.5)


def test_point_differential_uses_the_whole_season_not_the_form_window():
    """Season-to-date, not a rolling window -- the two accumulating features differ on purpose."""
    history = [
        *_home_streak(FORM_WINDOW, margin=2),  # days 0..9, the whole form window
        *[game(f"early{i}", -20 + i, "A", "Z", 130, 100) for i in range(3)],  # 3 earlier +30s
    ]
    windowed_only = compute_features(_home_streak(FORM_WINDOW, margin=2), matchup("t", 20, "A", "B"), at(20))
    with_earlier = compute_features(history, matchup("t", 20, "A", "B"), at(20))
    assert with_earlier["form_diff"] == pytest.approx(windowed_only["form_diff"])
    assert with_earlier["point_diff_diff"] > windowed_only["point_diff_diff"]


# --- season scoping ------------------------------------------------------------------------------


def test_form_and_point_differential_ignore_other_seasons():
    """A previous season describes a different roster (D-020), and scoping here is what makes the
    cold start recur every autumn rather than only in the first season of the corpus."""
    last_season = [
        game(f"prev{i}", -200 + i, "A", "C", 130, 100, season=SEASON - 1) for i in range(20)
    ]
    features = compute_features(last_season, matchup("t", 5, "A", "B"), at(5))
    assert features["form_diff"] == 0.0
    assert features["point_diff_diff"] == 0.0


def test_rest_is_not_season_scoped_but_a_cross_season_gap_lands_on_the_cap():
    """Rest deliberately looks across the season boundary -- it needs no empty case, because the
    offseason gap and 'no previous game at all' both read as fully rested."""
    last_season = [game("prev", -200, "A", "C", 130, 100, season=SEASON - 1)]
    features = compute_features(last_season, matchup("t", 5, "A", "B"), at(5))
    assert features["rest_diff"] == 0.0  # both teams at the cap


# --- rest days -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("home_last_game_day", "expected_home_rest"),
    [
        (4.0, 1.0),  # back to back
        (3.0, 2.0),
        (2.0, 3.0),
        (0.0, MAX_REST_DAYS),  # 5 days, exactly at the cap
        (-10.0, MAX_REST_DAYS),  # 15 days -> capped
    ],
)
def test_rest_days_are_measured_to_tip_off_and_capped(home_last_game_day, expected_home_rest):
    history = [game("h", home_last_game_day, "A", "C", 110, 100)]
    features = compute_features(history, matchup("t", 5, "A", "B"), at(5))
    # B has no games, so it sits at the cap and rest_diff = home_rest - MAX_REST_DAYS.
    assert features["rest_diff"] == pytest.approx(expected_home_rest - MAX_REST_DAYS)


def test_rest_is_measured_to_tip_off_not_to_as_of():
    """D-012's stated behavior: a prediction made days early carries a rest value that is wrong
    rather than stale, which is why predictions are re-appended daily instead of updated in place.
    Computing rest from `as_of` would hide that by making the number self-consistently meaningless."""
    history = [game("h", 2, "A", "C", 110, 100)]
    target = matchup("t", 5, "A", "B")
    early = compute_features(history, target, at(3))  # predicting two days before tip-off
    at_tip = compute_features(history, target, target.date)
    assert early["rest_diff"] == pytest.approx(at_tip["rest_diff"])  # both measure day 2 -> day 5 = 3 days
    assert early["rest_diff"] == pytest.approx(3.0 - MAX_REST_DAYS)


def test_no_previous_game_reads_as_fully_rested():
    features = compute_features([], matchup("t", 5, "A", "B"), at(5))
    assert features["rest_diff"] == 0.0
    one_sided = compute_features(
        [game("h", 4, "A", "C", 110, 100)], matchup("t", 5, "A", "B"), at(5)
    )
    assert one_sided["rest_diff"] == pytest.approx(1.0 - MAX_REST_DAYS)  # away's "no game" is the cap


# --- home indicator ------------------------------------------------------------------------------


def test_home_advantage_is_one_normally_and_zero_at_a_neutral_site():
    assert compute_features([], matchup("t", 5, "A", "B"), at(5))["home_advantage"] == 1.0
    neutral = matchup("t", 5, "A", "B", neutral=True)
    assert compute_features([], neutral, at(5))["home_advantage"] == 0.0


# --- purity and determinism ----------------------------------------------------------------------


def test_history_order_does_not_change_the_result():
    rng = random.Random(7)
    expected = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    for _ in range(25):
        shuffled = list(GOLDEN_HISTORY)
        rng.shuffle(shuffled)
        assert compute_features(shuffled, GOLDEN_TARGET, GOLDEN_AS_OF) == expected


def test_the_module_does_not_mutate_the_history_it_is_given():
    history = list(GOLDEN_HISTORY)
    before = list(history)
    compute_features(history, GOLDEN_TARGET, GOLDEN_AS_OF)
    assert history == before


def test_repeated_calls_are_identical():
    first = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    second = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    assert first == second


def test_a_prebuilt_index_is_equivalent_to_the_raw_sequence():
    """The index is an efficiency structure and nothing more -- it holds no as-of state a caller
    could build once and reuse against a later moment."""
    index = GameHistory.of(GOLDEN_HISTORY)
    assert compute_features(index, GOLDEN_TARGET, GOLDEN_AS_OF) == compute_features(
        GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF
    )
    # ...and it answers different as-of moments differently, from the one index.
    assert compute_features(index, GOLDEN_TARGET, at(7)) == compute_features(
        GOLDEN_HISTORY, GOLDEN_TARGET, at(7)
    )


def test_game_history_of_is_idempotent():
    index = GameHistory.of(GOLDEN_HISTORY)
    assert GameHistory.of(index) is index


# --- input validation ----------------------------------------------------------------------------


def test_naive_datetimes_are_refused_everywhere_they_could_enter():
    naive = datetime(2024, 1, 10)  # noqa: DTZ001 -- the point of the test
    with pytest.raises(FeatureInputError, match="naive datetime"):
        compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, naive)
    with pytest.raises(FeatureInputError, match="naive datetime"):
        Matchup(game_id="t", date=naive, season=SEASON, home_id="A", away_id="B")
    with pytest.raises(FeatureInputError, match="naive datetime"):
        Game(
            game_id="g",
            date=naive,
            season=SEASON,
            home_id="A",
            away_id="B",
            home_score=110,
            away_score=100,
        )


def test_a_tied_final_score_is_refused_as_corrupt_input():
    with pytest.raises(FeatureInputError, match="tied"):
        game("g", 0, "A", "B", 100, 100)


def test_a_team_cannot_play_itself():
    with pytest.raises(FeatureInputError, match="same team"):
        game("g", 0, "A", "A", 110, 100)
    with pytest.raises(FeatureInputError, match="same team"):
        matchup("t", 0, "A", "A")


def test_history_must_contain_game_records():
    with pytest.raises(FeatureInputError, match="must contain Game records"):
        compute_features([GOLDEN_TARGET], GOLDEN_TARGET, GOLDEN_AS_OF)


# --- the estimator contract ----------------------------------------------------------------------


def test_to_vector_emits_features_in_the_declared_order():
    features = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    assert to_vector(features) == pytest.approx(tuple(GOLDEN_EXPECTED[name] for name in FEATURE_NAMES))


def test_to_vector_refuses_a_mapping_that_does_not_match_the_contract():
    features = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    with pytest.raises(FeatureInputError, match="missing="):
        to_vector({k: v for k, v in features.items() if k != "rest_diff"})
    with pytest.raises(FeatureInputError, match="unexpected="):
        to_vector({**features, "spread": 1.0})


# --- regression tests for the T-006 review gate (F-044..F-060) ------------------------------------
#
# Each of these pins something a reviewer demonstrated was unpinned. The logic-reviewer's standard is
# the one that matters here: a test earns its place only if it FAILS when the module is subtly wrong.
# The mutation each test kills is named, so a future reader can re-run that mutation to check the
# test still has teeth.


def test_form_is_rolling_not_merely_windowed():
    """F-051 — kills `[-FORM_WINDOW:]` -> `[:FORM_WINDOW]` (5,096 of 6,615 real vectors differ).

    Every previous form fixture was a uniform streak, so the oldest ten games and the newest ten were
    numerically identical and nothing asserted *recency*. Here the early record is bad and the late
    record is good, so reading the window from the wrong end inverts the sign of the feature.
    """
    # 11 games: 5 losses, then 6 wins. The last 10 are LLLL WWWWWW -> 6 wins in 10.
    history = _home_results("LLLLLWWWWWW")
    features = compute_features(history, matchup("t", 20, "A", "B"), at(20))

    n = FORM_WINDOW
    observed_last_10 = 6 / 10
    expected = (n / (n + SHRINKAGE_K)) * observed_last_10 + (
        SHRINKAGE_K / (n + SHRINKAGE_K)
    ) * PRIOR_WIN_RATE
    assert features["form_diff"] == pytest.approx(expected - PRIOR_WIN_RATE)
    assert features["form_diff"] == pytest.approx(2 / 3 * 0.6 + 1 / 3 * 0.5 - 0.5)
    assert features["form_diff"] > 0  # the recent record is winning; the oldest ten are 5-5

    # The discriminating assertion: reading the OLDEST ten (LLLLLWWWWW = 5 wins) would give exactly
    # the prior and a form_diff of 0.0. It must not.
    assert features["form_diff"] != pytest.approx(0.0)


def test_form_window_reads_the_most_recent_games_when_the_record_reverses():
    """F-051, the mirror case — a good early record and a bad late one must produce a NEGATIVE
    form_diff. Together with the test above this pins direction as well as magnitude."""
    history = _home_results("WWWWWLLLLLL")
    features = compute_features(history, matchup("t", 20, "A", "B"), at(20))
    assert features["form_diff"] == pytest.approx(2 / 3 * 0.4 + 1 / 3 * 0.5 - 0.5)
    assert features["form_diff"] < 0


def test_season_point_differential_shrinks_on_the_full_season_count():
    """F-052 — kills both `n = min(len(margins), FORM_WINDOW)` (5,809 real vectors differ) and
    `n = len(records)` (5,225 differ).

    The old test asserted only a direction (`with_earlier > windowed_only`), which both mutations
    satisfy. This pins the literal, with a season count deliberately larger than FORM_WINDOW so that
    capping n at the window is arithmetically distinguishable.
    """
    # 12 games at a known mean margin: 12 wins by +10 -> mean +10, n = 12 (not 10).
    history = _home_streak(12, margin=10)
    features = compute_features(history, matchup("t", 20, "A", "B"), at(20))

    n = 12
    assert features["point_diff_diff"] == pytest.approx((n / (n + SHRINKAGE_K)) * 10.0)
    assert features["point_diff_diff"] == pytest.approx(12 / 17 * 10.0)
    # If n were capped at FORM_WINDOW it would be 10/15*10 = 6.667; if it counted all seasons it
    # would still be 12 here, so the prior-season guard below completes the pin.
    assert features["point_diff_diff"] != pytest.approx(10 / 15 * 10.0)


def test_point_differential_count_ignores_other_seasons():
    """F-052, second half — kills `n = len(records)` (the all-seasons count) specifically, which the
    single-season fixture above cannot distinguish."""
    history = [
        *[game(f"p{i}", -200 + i, "A", "C", 130, 100, season=SEASON - 1) for i in range(8)],
        *_home_streak(12, margin=10),
    ]
    features = compute_features(history, matchup("t", 20, "A", "B"), at(20))
    # n must be 12 (this season only), not 20 (all seasons).
    assert features["point_diff_diff"] == pytest.approx(12 / 17 * 10.0)
    assert features["point_diff_diff"] != pytest.approx(20 / 25 * 10.0)


def test_compute_training_features_uses_the_tip_off_instant_exactly():
    """F-053 — kills `as_of = game.date - 1 day` and `as_of = midnight of the tip-off day`.

    `compute_training_features` is the single entry point T-009 uses for every training row, and no
    test pinned its `as_of`. The history below contains a game a few HOURS before tip-off, so any
    coarsening of the as-of moment drops it and changes the answer.
    """
    target_game = game("target", 10, "A", "B", 111, 100)
    hours_before = game("recent", 10 - 3 / 24, "A", "E", 130, 90)  # 3 hours before tip-off
    history = [*GOLDEN_HISTORY, hours_before, target_game]

    assert compute_training_features(history, target_game) == compute_features(
        history, target_game.matchup, target_game.date
    )
    # ...and that game is genuinely inside the window, so a coarser as_of would be detectable.
    without_recent = compute_features(
        [*GOLDEN_HISTORY, target_game], target_game.matchup, target_game.date
    )
    assert compute_training_features(history, target_game) != without_recent


def test_the_as_of_filter_has_sub_day_granularity():
    """F-054 — kills truncating `as_of` to midnight (77 real vectors differ).

    The corpus has 83 team-days with two games on the same UTC date, so day-granularity is not a
    theoretical concern. The fixture epoch is 19:00Z precisely so that this can fail.
    """
    earlier_same_day = game("same-day", 10 - 2 / 24, "A", "E", 140, 80)  # 2h before, same UTC date
    baseline = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    with_earlier = compute_features(
        [*GOLDEN_HISTORY, earlier_same_day], GOLDEN_TARGET, GOLDEN_AS_OF
    )
    assert with_earlier != baseline  # it is before as_of, so it must count

    later_same_day = game("later", 10 + 2 / 24, "A", "E", 140, 80)  # 2h after, same UTC date
    with_later = compute_features([*GOLDEN_HISTORY, later_same_day], GOLDEN_TARGET, GOLDEN_AS_OF)
    assert with_later == baseline  # after as_of, so it must not — even on the same calendar day


def test_to_vector_order_is_pinned_to_literals_not_to_feature_names():
    """F-055 — kills `to_vector` returning `tuple(features.values())`.

    The old assertion built its expected tuple from FEATURE_NAMES, so both sides moved together and
    a mapping-order implementation passed. This pins literals in a fixed order and feeds in a
    deliberately shuffled mapping.
    """
    features = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    assert to_vector(features) == pytest.approx((1.0, 0.125, 3.0, 6.0))

    shuffled = {k: features[k] for k in reversed(list(features))}
    assert list(shuffled) != list(FEATURE_NAMES)  # the input really is out of order
    assert to_vector(shuffled) == pytest.approx((1.0, 0.125, 3.0, 6.0))


def test_to_vector_refuses_non_finite_and_non_numeric_values():
    """F-049 — a NaN reaching the estimator produces NaN coefficients, and the first symptom is a
    nonsense headline number."""
    features = compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(FeatureInputError, match="finite"):
            to_vector({**features, "rest_diff": bad})
    with pytest.raises(FeatureInputError, match="real number"):
        to_vector({**features, "rest_diff": "3.0"})


def test_a_game_cannot_contribute_to_its_own_features_even_if_as_of_moves_past_tip_off():
    """F-044 — the target's own result was reachable through `history`.

    The `Matchup` type closes the direct route; this closes the one the scores actually live on. The
    exclusion is by `game_id`, so it holds however the caller's timestamps line up.
    """
    target_game = game("target", 10, "A", "B", 200, 80)  # a blowout, to make leakage obvious
    history = [*GOLDEN_HISTORY, target_game]

    honest = compute_training_features(history, target_game)
    assert honest == pytest.approx(GOLDEN_EXPECTED)

    # A Matchup built by hand, dated microseconds later than the Game in history — the case that
    # previously let the 200-80 result into its own feature vector.
    later = Matchup(
        game_id="target",
        date=at(10) + timedelta(microseconds=1),
        season=SEASON,
        home_id="A",
        away_id="B",
    )
    assert compute_features(history, later, later.date) == pytest.approx(GOLDEN_EXPECTED)


def test_duplicate_game_ids_in_history_are_refused():
    """F-046 — F-027's "right count, wrong rows" one layer downstream. `load_games((2022, 2022))`
    returned 2,648 games with 1,324 unique ids and no error."""
    with pytest.raises(FeatureInputError, match="duplicate game_id"):
        compute_features([*GOLDEN_HISTORY, GOLDEN_HISTORY[0]], GOLDEN_TARGET, GOLDEN_AS_OF)


def test_a_one_shot_iterator_is_refused_rather_than_silently_yielding_priors():
    """F-045 — building the index consumed the generator, so the SECOND call saw an empty history
    and returned priors with no exception: indistinguishable from a legitimate cold start."""
    with pytest.raises(FeatureInputError, match="Sequence"):
        compute_features((g for g in GOLDEN_HISTORY), GOLDEN_TARGET, GOLDEN_AS_OF)


def test_mistyped_keys_are_refused_rather_than_missing_every_lookup():
    """F-045 — a str season or an int team id misses every index lookup and returns priors."""
    with pytest.raises(FeatureInputError, match="season must be an int"):
        Matchup(game_id="t", date=at(5), season="2024", home_id="A", away_id="B")
    with pytest.raises(FeatureInputError, match="home_id must be a str"):
        Matchup(game_id="t", date=at(5), season=SEASON, home_id=1, away_id="B")
    with pytest.raises(FeatureInputError, match="season must be an int"):
        game("g", 0, "A", "B", 110, 100, season="2024")


def test_non_integer_scores_are_refused_including_nan():
    """F-049 — `nan != nan`, so a NaN score sailed straight through the tie check."""
    with pytest.raises(FeatureInputError, match="home_score must be an int"):
        Game(
            game_id="g", date=at(0), season=SEASON, home_id="A", away_id="B",
            home_score=float("nan"), away_score=float("nan"),
        )
    with pytest.raises(FeatureInputError, match="must be an int"):
        Game(
            game_id="g", date=at(0), season=SEASON, home_id="A", away_id="B",
            home_score=110.5, away_score=100,
        )


def test_a_game_history_subclass_cannot_bypass_the_as_of_filter():
    """F-050 — `isinstance` let a subclass through unwrapped, so overriding `_records_before` in six
    lines handed back unfiltered history. `of` now demands the exact type."""

    class Unfiltered(GameHistory):
        def _records_before(self, team, as_of, exclude_game_id):  # noqa: ARG002
            return self._records.get(team, ())

    leaky = Unfiltered([*GOLDEN_HISTORY, game("future", 30, "A", "E", 200, 80)])
    # Refused outright, rather than quietly rebuilt: a caller whose override is silently ignored
    # never learns it does not run.
    with pytest.raises(FeatureInputError, match="subclasses GameHistory"):
        compute_features(leaky, GOLDEN_TARGET, GOLDEN_AS_OF)


def test_index_is_a_function_of_the_set_of_games_not_their_order():
    """F-058 — kills dropping the `(date, game_id)` sort tiebreaker.

    The GameHistory docstring claimed the tiebreaker made the index "a pure function of the set of
    input games ... asserted in the tests", and it was not. Two same-instant games alone are not
    enough: if both sit inside the form window and share a date they contribute identically however
    they are ordered, so the features come out the same either way and the mutation survives. That
    is why the first attempt at this test still let it through.

    The tiebreaker is only observable when two same-instant games STRADDLE the window boundary, so
    exactly one of them is counted. Below: 11 season games, two of them at the same instant on day 0
    with opposite results. The window keeps the newest 10, which drops precisely one of that pair —
    and without a deterministic tiebreaker, which one depends on input order.
    """
    pair = [
        game("z-second", 0, "A", "C", 100, 130),  # a loss, id sorts LAST
        game("a-first", 0, "A", "D", 130, 100),  # a win, id sorts FIRST
    ]
    rest = [game(f"f{i}", 1 + i, "A", "EFGHIJKLM"[i], 130, 100) for i in range(9)]
    history = [*pair, *rest]
    target = matchup("t", 20, "A", "B")

    baseline = compute_features(history, target, at(20))
    rng = random.Random(4242)
    for _ in range(40):
        shuffled = list(history)
        rng.shuffle(shuffled)
        assert compute_features(shuffled, target, at(20)) == baseline

    # The pair really does straddle the boundary: 11 season games, window keeps 10.
    assert len(history) == FORM_WINDOW + 1
    # `a-first` sorts first and is therefore the one dropped, so the window holds 9 wins and 1 loss.
    assert baseline["form_diff"] == pytest.approx(2 / 3 * 0.9 + 1 / 3 * 0.5 - 0.5)


def test_neutral_site_defaults_to_false_on_both_types():
    """F-059 — the helpers always passed the flag, so flipping the dataclass default to True passed
    the whole suite."""
    assert Matchup(game_id="t", date=at(5), season=SEASON, home_id="A", away_id="B").neutral_site is False
    assert Game(
        game_id="g", date=at(0), season=SEASON, home_id="A", away_id="B",
        home_score=110, away_score=100,
    ).neutral_site is False
    bare = Matchup(game_id="t", date=at(5), season=SEASON, home_id="A", away_id="B")
    assert compute_features([], bare, at(5))["home_advantage"] == 1.0
