"""Tests for the `features` deep module (T-006, rebuilt for the v2 feature set in T-028).

Follows the plan's Testing Decisions: assert external behavior -- the values a fixture produces and
the properties that must hold -- never the internal formula. Nothing here restates arithmetic that
lives in the module; the expected numbers are either hand-computed and written as literals, or
cross-checked against the independently tested interface of the deep module that owns them
(`elo.pregame_rating_differences`, `availability.team_availability`), so a failure means the module
changed rather than that both sides changed together.

Four groups carry the acceptance criteria:
  - the golden fixture       -- a hand-built Context with known values for all seven features
  - the leakage property     -- injecting future games **and future player rows** changes nothing,
                                each half paired with a control proving the assertion is not vacuous
  - the feature definitions  -- Elo, rest re-encoding, travel, altitude, availability, one at a time
  - the structural refusals  -- everything F-044/F-045/F-046/F-050/F-068/F-113/F-115 closed, which
                                the Context inherits rather than replaces

Standard library only, like the module: CI installs `requirements.txt`, which has no pandas/numpy.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from model import elo
from model.availability import Appearance, AvailabilityConfig, team_availability
from model.features import (
    FEATURE_NAMES,
    REST_EDGE_CAP,
    Context,
    Coverage,
    FeatureInputError,
    FeatureLeakageError,
    Game,
    GameHistory,
    Matchup,
    compute_features,
    compute_training_features,
    to_vector,
)
from model.venues import city_for, distance_miles

SEASON = 2024
# F-054: deliberately NOT midnight. Every `as_of` here is a whole-day offset from this epoch, so a
# midnight epoch made "truncate as_of to the day" a no-op -- which let a day-granularity as-of filter
# survive the whole suite, including the microsecond test written to catch it. 5,360 of the 6,615
# real games tip off at a non-midnight instant, so 19:00Z is also the realistic choice.
_EPOCH = datetime(2024, 1, 1, 19, 0, tzinfo=UTC)

BOSTON = city_for("Boston", "MA")
DENVER = city_for("Denver", "CO")
MIAMI = city_for("Miami", "FL")
MEXICO_CITY = city_for("Mexico City")

#: A rotation big enough to be a rotation (`availability` reads the top nine by minutes).
ROTATION = 9
STARTER_MINUTES = 30.0


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


def healthy(team: str, games_: list[Game], *, minutes: float = STARTER_MINUTES) -> list[Appearance]:
    """A full, uninjured rotation for `team` across every game it played in `games_`."""
    return [
        Appearance(g.game_id, g.date, f"{team}-p{i}", minutes, False)
        for g in games_
        if team in (g.home_id, g.away_id)
        for i in range(ROTATION)
    ]


def participation(games_: list[Game], extra: list[Game] | None = None) -> dict:
    """A healthy rotation for every team appearing in `games_` (plus any target-only teams)."""
    teams = {t for g in games_ for t in (g.home_id, g.away_id)}
    teams |= {t for g in (extra or []) for t in (g.home_id, g.away_id)}
    return {team: healthy(team, games_) for team in teams}


def ctx(
    games_: list[Game],
    *,
    cities: dict | None = None,
    default_city=BOSTON,
    appearances: dict | None = None,
    coverage: Coverage | None = None,
    targets: list[Matchup] | None = None,
    **kwargs,
) -> Context:
    """A Context over `games_`, defaulting every unspecified venue to Boston and every team to a
    healthy rotation. The defaults exist so a test about rest does not have to describe injuries."""
    resolved = {g.game_id: default_city for g in games_}
    for target in targets or []:
        resolved[target.game_id] = default_city
    resolved.update(cities or {})
    history = games_ if coverage is None else GameHistory(games_, coverage=coverage)
    people = participation(games_) if appearances is None else appearances
    for target in targets or []:
        for team in (target.home_id, target.away_id):
            people.setdefault(team, [])
    return Context(history, people, resolved, **kwargs)


# --- golden fixture ------------------------------------------------------------------------------
#
# Five completed games, then A (home) vs B (away) on day 9. Hand-computed:
#
#   A: day 0 home vs C (+10, Boston) · day 2 at D (-5, Denver) · day 4 home vs B (+20, Boston)
#      last game day 4 -> 5 days elapsed -> 4 days of rest -> bucketed at REST_EDGE_CAP = 3
#      previous venue Boston, target venue Boston -> 0 miles travelled
#
#   B: day 4 at A (-20, Boston) · day 6 home vs C (+9, Miami) · day 8 at D (-12, Denver)
#      last game day 8 -> 1 day elapsed -> 0 days of rest -> a back-to-back
#      previous venue Denver, target venue Boston -> 1,765.98 miles travelled
#
#   elo_diff    24.5659...  cross-checked below against `elo.pregame_rating_differences`
#   home_b2b     0.0        A rested four days
#   away_b2b     1.0        B played yesterday
#   rest_edge    3.0        3 (A, capped) - 0 (B)
#   avail_diff   0.0        both rotations fully healthy throughout
#   travel_diff  -1765.98   0 (A, home stand) - 1765.98 (B, in from Denver)
#   altitude     0.0        Boston is 20 feet above sea level

GOLDEN_HISTORY = [
    game("g1", 0, "A", "C", 110, 100),
    game("g2", 2, "D", "A", 105, 100),
    game("g3", 4, "A", "B", 120, 100),
    game("g4", 6, "B", "C", 99, 90),
    game("g5", 8, "D", "B", 100, 88),
]
GOLDEN_CITIES = {
    "g1": BOSTON,
    "g2": DENVER,
    "g3": BOSTON,
    "g4": MIAMI,
    "g5": DENVER,
    "target": BOSTON,
}
GOLDEN_TARGET = matchup("target", 9, "A", "B")
GOLDEN_AS_OF = at(9)
DENVER_TO_BOSTON = 1765.9803786079297
GOLDEN_EXPECTED = {
    "elo_diff": 24.5659385477843,
    "home_b2b": 0.0,
    "away_b2b": 1.0,
    "rest_edge": 3.0,
    "avail_diff": 0.0,
    "travel_diff": -DENVER_TO_BOSTON,
    "altitude": 0.0,
}


def golden_context(**kwargs) -> Context:
    return ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES, **kwargs)


def golden(context: Context | None = None) -> dict:
    return compute_features(context or golden_context(), GOLDEN_TARGET, GOLDEN_AS_OF)


def test_golden_fixture_produces_the_hand_computed_features():
    assert golden() == pytest.approx(GOLDEN_EXPECTED)


def test_golden_elo_matches_the_independently_tested_replay_interface():
    """The one number in the fixture that is not hand-computable, cross-checked rather than trusted.

    `pregame_rating_differences` is T-025's interface and has its own golden fixtures and invariants;
    it reaches the answer by replaying the sequence, while `Context` reaches it through the
    precomputed `elo.Timeline`. Two code paths, one number, so the literal above is pinned to
    something other than itself. The target is appended as a completed game with arbitrary scores --
    a game's own result cannot affect its own pre-game rating, which is exactly the property being
    relied on.
    """
    appended = [*GOLDEN_HISTORY, game("target", 9, "A", "B", 1, 2)]
    assert golden()["elo_diff"] == elo.pregame_rating_differences(appended)["target"]


def test_output_keys_are_exactly_the_declared_feature_names():
    assert set(golden()) == set(FEATURE_NAMES)


def test_the_phase_one_features_are_gone():
    """D-033 removed `form_diff` and `home_advantage`, D-032 replaced `point_diff_diff`. Asserted on
    the emitted keys and on the module's exports, because a caller that still asks for one of them
    should get an error rather than a KeyError three layers down."""
    import model.features as features_module

    assert not {"form_diff", "home_advantage", "point_diff_diff"} & set(golden())
    for gone in ("FORM_WINDOW", "SHRINKAGE_K", "PRIOR_WIN_RATE", "PRIOR_POINT_DIFF", "MAX_REST_DAYS"):
        assert not hasattr(features_module, gone), f"{gone} outlived the feature it tuned"


def test_features_are_antisymmetric_when_the_target_sides_are_swapped():
    """Every difference feature is signed home-minus-away, so playing the same game the other way
    round negates it. The two indicators swap rather than negate, and `altitude` is a property of
    the venue and does not move.

    Only the *target* is mirrored, not the history: MOV Elo's update applies the home adjustment to
    whichever side was at home, so mirroring completed games changes the ratings themselves and
    would test nothing about the feature's sign convention.
    """
    forward = golden()
    reverse = compute_features(
        golden_context(), matchup("target", 9, "B", "A"), GOLDEN_AS_OF
    )
    for name in ("elo_diff", "rest_edge", "avail_diff", "travel_diff"):
        assert reverse[name] == pytest.approx(-forward[name]), name
    assert reverse["home_b2b"] == forward["away_b2b"]
    assert reverse["away_b2b"] == forward["home_b2b"]
    assert reverse["altitude"] == forward["altitude"]


# --- the leakage property test -------------------------------------------------------------------
#
# "The single most important test in the plan", extended in T-028 from one time-varying source to
# three. Adding anything dated at or after `as_of` -- a game **or a player-box row** -- must not
# change the output. Exact equality, not approx: the claim is "changes nothing", the filtered record
# set is identical, so the arithmetic is bit-identical too.
#
# Each half is paired with a control. Without them a `compute_features` that ignored its inputs
# entirely would satisfy the leakage property perfectly and be worthless.

FUTURE_TEAMS = ("A", "B", "C", "D", "E")


def _random_future_game(
    rng: random.Random, index: int, as_of_day: float, *, boundary: bool
) -> Game:
    """A completed game dated at or after the as-of moment -- i.e. one that did not exist yet.

    Exactly one game per trial sits at `as_of` itself (`boundary`), which is the instant the strict
    filter has to exclude; the rest are spread out and given distinct sub-minute offsets. Distinct
    on purpose: a real schedule never has one team playing two games at the same instant, and
    `elo.Timeline` refuses a corpus that claims otherwise, so colliding instants would fail this
    test on a rule it is not about.
    """
    home, away = rng.sample(FUTURE_TEAMS, 2)
    home_score = rng.randint(85, 135)
    away_score = rng.choice([s for s in range(85, 136) if s != home_score])
    offset = 0.0 if boundary else rng.choice([0.5, 1.0, 7.0, 60.0]) + index * 1e-4
    return game(
        f"future-{index}",
        as_of_day + offset,
        home,
        away,
        home_score,
        away_score,
        # The season label has to agree with the date -- `elo.replay` refuses a corpus where it does
        # not, because that is what decides when carryover is applied. Picking it independently of
        # the day would make this test fail on a rule it is not about.
        season=SEASON + 1 if offset >= 30.0 else SEASON,
    )


def _future_rows(rng: random.Random, future: Game) -> list[Appearance]:
    """Player rows for a game that has not happened, including absences loud enough to move the
    number if they were ever read."""
    rows = []
    for team in (future.home_id, future.away_id):
        for i in range(ROTATION):
            absent = rng.random() < 0.5
            rows.append(
                Appearance(
                    future.game_id,
                    future.date,
                    f"{team}-p{i}",
                    None if absent else rng.uniform(5.0, 40.0),
                    absent,
                )
            )
    return rows


def test_leakage_adding_games_and_player_rows_at_or_after_as_of_does_not_change_the_features():
    rng = random.Random(20260905)
    expected = golden()
    for trial in range(200):
        polluted = list(GOLDEN_HISTORY)
        people = participation(GOLDEN_HISTORY)
        cities = dict(GOLDEN_CITIES)
        for n in range(rng.randint(1, 8)):
            future = _random_future_game(rng, trial * 10 + n, as_of_day=9, boundary=n == 0)
            polluted.append(future)
            cities[future.game_id] = rng.choice([BOSTON, DENVER, MIAMI, MEXICO_CITY])
            for row in _future_rows(rng, future):
                people.setdefault(row.player_id.split("-")[0], []).append(row)
        rng.shuffle(polluted)  # position in the sequence must not matter either
        for rows in people.values():
            rng.shuffle(rows)
        context = Context(polluted, people, cities)
        assert compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF) == expected


def test_leakage_control_a_game_before_as_of_does_change_the_features():
    """First control. Proves a *game* dated before the as-of moment actually reaches the output."""
    with_past = [*GOLDEN_HISTORY, game("past", 8.5, "A", "E", 130, 90)]
    cities = {**GOLDEN_CITIES, "past": MIAMI}
    assert compute_features(ctx(with_past, cities=cities), GOLDEN_TARGET, GOLDEN_AS_OF) != golden()


def test_leakage_control_a_player_row_before_as_of_does_change_the_features():
    """Second control, and the one T-028 adds. Without it the property test above proves only that
    future *games* are filtered, while the participation source could be ignored entirely -- which
    is precisely the failure mode a feature that reads three sources and filters one has."""
    people = participation(GOLDEN_HISTORY)
    # B's whole rotation sat out its most recent game (day 8), which is inside the window.
    people["B"] = [
        Appearance(a.game_id, a.date, a.player_id, None, True)
        if a.game_id == "g5"
        else a
        for a in people["B"]
    ]
    polluted = compute_features(
        ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES, appearances=people),
        GOLDEN_TARGET,
        GOLDEN_AS_OF,
    )
    assert polluted["avail_diff"] > golden()["avail_diff"]


def test_leakage_a_game_at_exactly_the_as_of_instant_is_excluded():
    """The filter is strict (`<`), not inclusive. This is the boundary the training case rests on."""
    boundary = [*GOLDEN_HISTORY, game("boundary", 9, "A", "E", 140, 80)]
    cities = {**GOLDEN_CITIES, "boundary": DENVER}
    assert compute_features(
        ctx(boundary, cities=cities), GOLDEN_TARGET, GOLDEN_AS_OF
    ) == pytest.approx(GOLDEN_EXPECTED)


def test_leakage_a_player_row_at_exactly_the_as_of_instant_is_excluded():
    """The same boundary, on the source T-028 added. A row dated at the prediction moment belongs to
    the game being predicted, which is the textbook form of the leak D-035 exists to prevent."""
    people = participation(GOLDEN_HISTORY)
    for team in ("A", "B"):
        people[team] = [
            *people[team],
            *[Appearance("target", at(9), f"{team}-p{i}", None, True) for i in range(ROTATION)],
        ]
    assert compute_features(
        ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES, appearances=people),
        GOLDEN_TARGET,
        GOLDEN_AS_OF,
    ) == pytest.approx(GOLDEN_EXPECTED)


def test_leakage_a_game_one_microsecond_before_as_of_is_included():
    """...and the exclusion above is about the boundary itself, not a coarser day-level comparison
    that would also drop legitimate same-day earlier games."""
    just_before = [*GOLDEN_HISTORY, game("just-before", 9 - 1e-6 / 86400, "A", "E", 140, 80)]
    cities = {**GOLDEN_CITIES, "just-before": DENVER}
    assert compute_features(
        ctx(just_before, cities=cities), GOLDEN_TARGET, GOLDEN_AS_OF
    ) != pytest.approx(GOLDEN_EXPECTED)


def test_leakage_a_completed_game_is_excluded_from_its_own_features():
    """The training case: `as_of` is the target's own tip-off and the target is in the history."""
    target = game("g6", 9, "A", "B", 150, 70)
    history = [*GOLDEN_HISTORY, target]
    cities = {**GOLDEN_CITIES, "g6": BOSTON}
    context = ctx(history, cities=cities)
    got = compute_training_features(context, target)
    for name, value in GOLDEN_EXPECTED.items():
        # `elo_diff` is the one to watch: a 150-70 result would move it by tens of points.
        assert got[name] == pytest.approx(value), name


def test_leakage_the_targets_own_row_dated_microseconds_early_cannot_reach_elo():
    """F-044's shape, on the source that would silently absorb it.

    The date filter alone excludes the target only because `as_of == target.date`. A history whose
    copy of the target is dated a microsecond earlier -- which `to_pydatetime()` truncation can
    produce -- puts the game *inside* its own as-of window. `GameHistory._records_before` drops it by
    id; the Elo timeline has to be told, and `exclude_game_id` is what tells it.
    """
    early = Game("target", at(9) - timedelta(microseconds=1), SEASON, "A", "B", 160, 60)
    context = ctx([*GOLDEN_HISTORY, early], cities=GOLDEN_CITIES)
    assert compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF)["elo_diff"] == pytest.approx(
        GOLDEN_EXPECTED["elo_diff"]
    )


def test_leakage_the_targets_own_player_rows_dated_microseconds_early_cannot_reach_availability():
    """The same hazard on the participation source. A blowout's DNP rows are exactly the ones that
    would move `avail_diff`, and they are post-game information about the game being predicted."""
    early = at(9) - timedelta(microseconds=1)
    people = participation(GOLDEN_HISTORY)
    for team in ("A", "B"):
        people[team] = [
            *people[team],
            *[Appearance("target", early, f"{team}-p{i}", None, True) for i in range(ROTATION)],
        ]
    got = compute_features(
        ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES, appearances=people), GOLDEN_TARGET, GOLDEN_AS_OF
    )
    assert got["avail_diff"] == pytest.approx(GOLDEN_EXPECTED["avail_diff"])


def test_leakage_as_of_after_tip_off_is_refused():
    with pytest.raises(FeatureLeakageError, match="after tip-off"):
        compute_features(golden_context(), GOLDEN_TARGET, at(9.001))


def test_as_of_at_tip_off_is_allowed_because_that_is_the_training_rule():
    assert compute_features(golden_context(), GOLDEN_TARGET, at(9)) == pytest.approx(GOLDEN_EXPECTED)


def test_as_of_before_tip_off_is_allowed_because_that_is_the_inference_case():
    """Five days out: B's day-8 game has not happened yet, so it is not a back-to-back any more and
    B's rest reads as the cap. That the number *moves* is the point -- D-012 re-predicts daily
    precisely because a rest feature computed days ahead is wrong rather than merely stale."""
    early = compute_features(golden_context(), GOLDEN_TARGET, at(4.5))
    assert early["away_b2b"] == 0.0
    assert early != GOLDEN_EXPECTED


def test_a_completed_game_cannot_be_passed_as_the_target():
    """The one-way door: `Game.matchup` exists so the scores cannot travel with the target."""
    with pytest.raises(FeatureInputError, match="must be a Matchup"):
        compute_features(golden_context(), GOLDEN_HISTORY[0], GOLDEN_AS_OF)


def test_the_as_of_filter_has_sub_day_granularity():
    """A whole-day comparison would let a game earlier the same day into a window it does not belong
    in, and keep one out that does."""
    same_day_earlier = [*GOLDEN_HISTORY, game("earlier", 8.9, "B", "E", 150, 70)]
    cities = {**GOLDEN_CITIES, "earlier": MIAMI}
    got = compute_features(ctx(same_day_earlier, cities=cities), GOLDEN_TARGET, GOLDEN_AS_OF)
    assert got["elo_diff"] != pytest.approx(GOLDEN_EXPECTED["elo_diff"])


# --- elo_diff (D-032) ----------------------------------------------------------------------------


def test_elo_diff_matches_the_replay_for_every_game_in_the_corpus():
    """The equality the `Context`'s precomputed timeline rests on, asserted over a whole corpus
    rather than at one point. `compute_training_features` reaches the number through the index;
    `pregame_rating_differences` reaches it by replaying. They must not merely be close."""
    rng = random.Random(4242)
    teams = [f"T{i}" for i in range(10)]
    games_, day = [], 0.0
    for season in (SEASON - 1, SEASON):
        for n in range(120):
            home, away = rng.sample(teams, 2)
            home_score = rng.randint(85, 135)
            away_score = rng.choice([s for s in range(85, 136) if s != home_score])
            games_.append(game(f"s{season}-{n}", day, home, away, home_score, away_score,
                               season=season))
            day += 0.5
        day += 120
    replayed = elo.pregame_rating_differences(games_)
    context = ctx(games_)
    for g in games_:
        assert compute_training_features(context, g)["elo_diff"] == replayed[g.game_id], g.game_id


def test_elo_diff_is_zero_for_the_first_game_of_the_corpus():
    """Both teams start at the initial rating, so the very first game carries no strength signal --
    an invariant, not a coincidence of this fixture."""
    first = game("only", 0, "A", "B", 120, 100)
    assert compute_training_features(ctx([first], targets=[first.matchup]), first)["elo_diff"] == 0.0


def test_elo_diff_carries_across_a_season_boundary_but_regressed():
    """D-032's carryover: a season opener sees last season's ratings moved 25% toward the mean, not
    reset and not carried forward intact."""
    history = [game(f"h{i}", i, "A", "B", 130, 100) for i in range(6)]
    t1, t2 = matchup("t1", 6, "A", "B"), matchup("t2", 200, "A", "B", season=SEASON + 1)
    end_of_season = compute_features(ctx(history, targets=[t1, t2]), t1, at(6))["elo_diff"]
    opener = compute_features(ctx(history, targets=[t1, t2]), t2, at(200))["elo_diff"]
    assert end_of_season > 0
    assert opener == pytest.approx(end_of_season * elo.DEFAULT_CARRYOVER)


def test_elo_diff_omits_the_home_adjustment():
    """D-024's trap. Adding the 100-point home adjustment would make the column differ from this one
    by a constant, which is perfectly collinear with the intercept. The test that catches it: a
    matchup and its mirror must be exact negations, which a constant offset would break."""
    history = [game(f"h{i}", i, "A", "B", 130, 100) for i in range(4)]
    context = ctx(history, targets=[matchup("t", 5, "A", "B"), matchup("t2", 5, "B", "A")])
    forward = compute_features(context, matchup("t", 5, "A", "B"), at(5))["elo_diff"]
    reverse = compute_features(context, matchup("t2", 5, "B", "A"), at(5))["elo_diff"]
    assert forward == pytest.approx(-reverse)
    assert forward != 0.0


# --- rest: home_b2b, away_b2b, rest_edge (D-034) --------------------------------------------------


@pytest.mark.parametrize(
    ("previous_day", "expected_rest", "expected_b2b"),
    [
        (9.0, 0, 1.0),  # yesterday -- a back-to-back
        (8.0, 1, 0.0),
        (7.0, 2, 0.0),
        (6.0, 3, 0.0),
        (2.0, 3, 0.0),  # eight days: bucketed at the cap, not eight
    ],
)
def test_rest_is_whole_days_bucketed_at_the_cap(previous_day, expected_rest, expected_b2b):
    history = [game("prev", previous_day, "A", "C", 110, 100), game("bprev", 9.0, "B", "D", 110, 100)]
    target = matchup("t", 10, "A", "B")
    got = compute_features(ctx(history, targets=[target]), target, at(10))
    assert got["home_b2b"] == expected_b2b
    assert got["rest_edge"] == float(expected_rest - 0)  # B always played yesterday


def test_the_two_back_to_back_indicators_are_independent():
    """User story 7: separate home and away indicators, so an asymmetric effect can be measured
    rather than assumed symmetric. Both firing at once must be expressible."""
    history = [game("a", 9, "A", "C", 110, 100), game("b", 9.01, "B", "D", 110, 100)]
    target = matchup("t", 10, "A", "B")
    got = compute_features(ctx(history, targets=[target]), target, at(10))
    assert (got["home_b2b"], got["away_b2b"], got["rest_edge"]) == (1.0, 1.0, 0.0)


def test_a_late_tip_off_still_reads_as_a_back_to_back():
    """The trap this feature is one line away from. Every date here is UTC, and a 10:30pm Eastern
    tip-off is already the next calendar day in UTC -- so differencing calendar dates would call a
    genuine back-to-back "two days apart", and would do it for exactly the late games where rest
    matters most. Elapsed hours carry no timezone, which is why they are what is measured."""
    late = _EPOCH.replace(hour=3, minute=30) + timedelta(days=1)  # 10:30pm ET the previous evening
    next_evening = late + timedelta(hours=21)  # 7:30pm ET the following day, two UTC dates later
    assert late.date() != next_evening.date()
    history = [
        Game("prev", late, SEASON, "A", "C", 110, 100),
        Game("bprev", late - timedelta(days=4), SEASON, "B", "D", 110, 100),
    ]
    target = Matchup("t", next_evening, SEASON, "A", "B")
    got = compute_features(ctx(history, targets=[target]), target, next_evening)
    assert got["home_b2b"] == 1.0


def test_no_previous_game_reads_as_fully_rested_and_not_a_back_to_back():
    """A season opener has no previous game at all. The cap produces the honest reading without a
    special case, and the indicator must not fire on an absent gap."""
    history = [game("bprev", 9, "B", "D", 110, 100)]
    target = matchup("t", 10, "A", "B")
    got = compute_features(ctx(history, targets=[target]), target, at(10))
    assert got["home_b2b"] == 0.0
    assert got["rest_edge"] == float(REST_EDGE_CAP)


def test_rest_is_not_season_scoped_and_a_cross_season_gap_lands_on_the_cap():
    """Deliberately unlike travel. A months-long gap reads honestly as "fully rested"; it does not
    read honestly as "flew 2,400 miles", which is why only one of the two is season-scoped."""
    history = [game("prev", 0, "A", "C", 110, 100), game("bprev", 129, "B", "D", 110, 100)]
    target = matchup("t", 130, "A", "B", season=SEASON + 1)
    got = compute_features(ctx(history, targets=[target]), target, at(130))
    assert got["rest_edge"] == float(REST_EDGE_CAP - 0)


def test_rest_is_measured_to_tip_off_not_to_as_of():
    """A prediction made days out still describes the rest the teams will have at tip-off."""
    history = [game("prev", 6, "A", "C", 110, 100), game("bprev", 9, "B", "D", 110, 100)]
    target = matchup("t", 10, "A", "B")
    context = ctx(history, targets=[target])
    assert compute_features(context, target, at(10))["rest_edge"] == pytest.approx(
        compute_features(context, target, at(9.5))["rest_edge"]
    )


# --- travel_diff (D-036) -------------------------------------------------------------------------


def test_travel_is_zero_for_a_home_stand():
    history = [game("prev", 8, "A", "C", 110, 100), game("bprev", 8, "B", "D", 110, 100)]
    target = matchup("t", 10, "A", "B")
    cities = {"prev": BOSTON, "bprev": BOSTON, "t": BOSTON}
    assert compute_features(ctx(history, cities=cities, targets=[target]), target, at(10))[
        "travel_diff"
    ] == 0.0


def test_travel_diff_is_negative_when_the_away_team_flew_further():
    """Signed home-minus-away like every other difference here, so the coefficient is expected to be
    negative: travel is a cost, and the sign convention is about orientation, not about which way is
    good."""
    history = [game("prev", 8, "A", "C", 110, 100), game("bprev", 8, "B", "D", 110, 100)]
    target = matchup("t", 10, "A", "B")
    cities = {"prev": BOSTON, "bprev": DENVER, "t": BOSTON}
    got = compute_features(ctx(history, cities=cities, targets=[target]), target, at(10))
    assert got["travel_diff"] == pytest.approx(-DENVER_TO_BOSTON)
    assert DENVER_TO_BOSTON == pytest.approx(distance_miles(DENVER, BOSTON))


def test_travel_is_season_scoped_so_a_season_opener_carries_none():
    """The offseason case. Last season's last game was in Denver; the flight home happened four
    months ago and is not fatigue. Rest deliberately does *not* work this way -- see its test."""
    history = [game("prev", 0, "D", "A", 110, 100), game("bprev", 129, "B", "C", 110, 100)]
    target = matchup("t", 130, "A", "B", season=SEASON + 1)
    cities = {"prev": DENVER, "bprev": BOSTON, "t": BOSTON}
    got = compute_features(ctx(history, cities=cities, targets=[target]), target, at(130))
    assert got["travel_diff"] == 0.0


def test_a_history_game_without_a_city_is_refused_at_construction():
    """Loudly, once, rather than 6,000 times as a quiet zero."""
    with pytest.raises(FeatureInputError, match="no venue city"):
        Context(GOLDEN_HISTORY, participation(GOLDEN_HISTORY), {"g1": BOSTON})


def test_a_target_without_a_city_is_refused_at_computation():
    context = ctx(GOLDEN_HISTORY, cities={g.game_id: BOSTON for g in GOLDEN_HISTORY})
    with pytest.raises(FeatureInputError, match="no venue city in this Context"):
        compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF)


# --- altitude (D-036) ----------------------------------------------------------------------------


def test_altitude_fires_at_denver_and_not_at_boston():
    history = [game("prev", 8, "A", "C", 110, 100), game("bprev", 8, "B", "D", 110, 100)]
    target = matchup("t", 10, "A", "B")
    for city, expected in ((DENVER, 1.0), (BOSTON, 0.0)):
        cities = {"prev": city, "bprev": city, "t": city}
        got = compute_features(ctx(history, cities=cities, targets=[target]), target, at(10))
        assert got["altitude"] == expected, city.name


def test_altitude_does_not_fire_at_a_high_altitude_neutral_site():
    """Mexico City is 7,350 feet and is in this corpus. The feature's content is the *asymmetry* --
    at Denver the visitor is the unacclimated side, which is a home advantage the model can price.
    At a neutral site both teams flew in, the elevation affects them equally, and it says nothing
    about who wins."""
    history = [game("prev", 8, "A", "C", 110, 100), game("bprev", 8, "B", "D", 110, 100)]
    target = matchup("t", 10, "A", "B", neutral=True)
    cities = {"prev": BOSTON, "bprev": BOSTON, "t": MEXICO_CITY}
    assert MEXICO_CITY.is_high_altitude
    got = compute_features(ctx(history, cities=cities, targets=[target]), target, at(10))
    assert got["altitude"] == 0.0


# --- avail_diff (D-035) --------------------------------------------------------------------------


def _played(team: str, games_: list[Game], absent_players=(), absent_games=()) -> list[Appearance]:
    """A rotation whose minutes differ by player, so "the top nine by minutes" is a real ordering."""
    rows = []
    for g in games_:
        if team not in (g.home_id, g.away_id):
            continue
        for i in range(ROTATION):
            out = i in absent_players and g.game_id in absent_games
            rows.append(
                Appearance(g.game_id, g.date, f"{team}-p{i}", None if out else 34.0 - 2.0 * i, out)
            )
    return rows


def test_a_multi_game_absence_by_a_rotation_player_moves_the_feature():
    """User story 10, and the whole reason this factor exists."""
    history = [game(f"h{i}", i, "A", "B", 110, 100) for i in range(6)]
    target = matchup("t", 7, "A", "B")
    base = {"A": _played("A", history), "B": _played("B", history)}
    injured = {
        "A": _played("A", history, absent_players={0}, absent_games={"h3", "h4", "h5"}),
        "B": _played("B", history),
    }
    healthy_diff = compute_features(
        ctx(history, appearances=base, targets=[target]), target, at(7)
    )["avail_diff"]
    injured_diff = compute_features(
        ctx(history, appearances=injured, targets=[target]), target, at(7)
    )["avail_diff"]
    assert injured_diff < healthy_diff


def test_avail_diff_matches_the_independently_tested_availability_interface():
    """Cross-checked against T-027's own interface over the same as-of filtered rows, so the number
    is pinned to something other than itself."""
    history = [game(f"h{i}", i, "A", "B", 110, 100) for i in range(6)]
    target = matchup("t", 7, "A", "B")
    people = {
        "A": _played("A", history, absent_players={0, 1}, absent_games={"h4", "h5"}),
        "B": _played("B", history),
    }
    got = compute_features(ctx(history, appearances=people, targets=[target]), target, at(7))
    expected = team_availability(
        sorted([a for a in people["A"] if a.date < at(7)], key=lambda a: (a.date, a.game_id)),
        as_of=at(7),
    ) - team_availability(
        sorted([a for a in people["B"] if a.date < at(7)], key=lambda a: (a.date, a.game_id)),
        as_of=at(7),
    )
    assert got["avail_diff"] == expected


def test_a_zero_minute_row_in_a_blowout_does_not_read_as_absence():
    """User story 12. A rotation player logging zero minutes in a blowout is available; counting
    that as an injury would make the feature partly a blowout detector, which is the leak arriving
    by the back door."""
    history = [game(f"h{i}", i, "A", "B", 110, 100) for i in range(6)]
    target = matchup("t", 7, "A", "B")
    base = {"A": _played("A", history), "B": _played("B", history)}
    benched = {
        "A": [
            Appearance(a.game_id, a.date, a.player_id, 0.0, False)
            if a.game_id == "h5" and a.player_id == "A-p0"
            else a
            for a in base["A"]
        ],
        "B": base["B"],
    }
    assert compute_features(ctx(history, appearances=benched, targets=[target]), target, at(7))[
        "avail_diff"
    ] == compute_features(ctx(history, appearances=base, targets=[target]), target, at(7))[
        "avail_diff"
    ]


def test_a_team_with_no_participation_records_is_refused():
    """A team missing from the map reads as league-average availability in every game it plays, which
    is a value the feature legitimately takes -- so it cannot be told apart after the fact."""
    people = participation(GOLDEN_HISTORY)
    del people["B"]
    with pytest.raises(FeatureInputError, match="no participation records"):
        Context(GOLDEN_HISTORY, people, GOLDEN_CITIES)


def test_the_availability_config_is_honoured_through_the_context():
    """The knobs are the deep module's, and the Context must not quietly substitute its own."""
    history = [game(f"h{i}", i, "A", "B", 110, 100) for i in range(6)]
    target = matchup("t", 7, "A", "B")
    people = {"A": _played("A", history, absent_players={0}, absent_games={"h5"}),
              "B": _played("B", history)}
    default = compute_features(ctx(history, appearances=people, targets=[target]), target, at(7))
    narrow = compute_features(
        ctx(
            history,
            appearances=people,
            targets=[target],
            availability_config=AvailabilityConfig(window_games=1),
        ),
        target,
        at(7),
    )
    assert narrow["avail_diff"] != default["avail_diff"]


# --- the Context itself --------------------------------------------------------------------------


def test_context_of_is_idempotent():
    context = golden_context()
    assert Context.of(context) is context


def test_a_bare_history_is_refused_with_a_pointer_to_the_context():
    """T-028 replaced the history argument. Accepting a sequence here would mean silently building a
    Context with no participation and no venues -- the partial Context the class forbids."""
    with pytest.raises(FeatureInputError, match="takes a Context"):
        compute_features(GOLDEN_HISTORY, GOLDEN_TARGET, GOLDEN_AS_OF)


def test_a_context_subclass_is_refused():
    """F-050's rule applied to the Context: a subclass could override how a source is read, which is
    a call site opting out of the as-of filter."""

    class Sneaky(Context):
        pass

    sneaky = Sneaky(GOLDEN_HISTORY, participation(GOLDEN_HISTORY), GOLDEN_CITIES)
    with pytest.raises(FeatureInputError, match="subclasses Context"):
        compute_features(sneaky, GOLDEN_TARGET, GOLDEN_AS_OF)


def test_game_cities_must_contain_cities():
    with pytest.raises(FeatureInputError, match="must be a venues.City"):
        Context(GOLDEN_HISTORY, participation(GOLDEN_HISTORY),
                {**GOLDEN_CITIES, "g1": "Boston"})


def test_game_cities_must_be_a_mapping():
    with pytest.raises(FeatureInputError, match="must be a Mapping"):
        Context(GOLDEN_HISTORY, participation(GOLDEN_HISTORY), [("g1", BOSTON)])


def test_the_context_is_reusable_and_repeated_calls_are_identical():
    context = golden_context()
    assert compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF) == compute_features(
        context, GOLDEN_TARGET, GOLDEN_AS_OF
    )


def test_history_order_does_not_change_the_result():
    shuffled = list(reversed(GOLDEN_HISTORY))
    assert compute_features(
        ctx(shuffled, cities=GOLDEN_CITIES), GOLDEN_TARGET, GOLDEN_AS_OF
    ) == golden()


def test_the_module_does_not_mutate_the_history_it_is_given():
    history = list(GOLDEN_HISTORY)
    before = list(history)
    compute_features(ctx(history, cities=GOLDEN_CITIES), GOLDEN_TARGET, GOLDEN_AS_OF)
    assert history == before


# --- structural refusals inherited from T-006 (F-044 ... F-115) -----------------------------------


def test_a_prebuilt_index_is_equivalent_to_the_raw_sequence():
    """`GameHistory` applies no filtering at construction, so passing one is exactly equivalent to
    passing the games -- there is no "already filtered" state to build once and reuse."""
    prebuilt = GameHistory(GOLDEN_HISTORY)
    assert compute_features(
        ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES), GOLDEN_TARGET, GOLDEN_AS_OF
    ) == compute_features(
        Context(prebuilt, participation(GOLDEN_HISTORY), GOLDEN_CITIES),
        GOLDEN_TARGET,
        GOLDEN_AS_OF,
    )


def test_game_history_of_is_idempotent():
    index = GameHistory(GOLDEN_HISTORY)
    assert GameHistory.of(index) is index


def test_a_game_history_subclass_cannot_bypass_the_as_of_filter():
    class Leaky(GameHistory):
        def _records_before(self, team, as_of, exclude_game_id, opponent_id):
            return self._records.get(team, ())

    with pytest.raises(FeatureInputError, match="subclasses GameHistory"):
        Context(Leaky(GOLDEN_HISTORY), participation(GOLDEN_HISTORY), GOLDEN_CITIES)


def test_naive_datetimes_are_refused_everywhere_they_could_enter():
    naive = datetime(2024, 1, 11, 19, 0)  # noqa: DTZ001 -- the point of the test
    with pytest.raises(FeatureInputError, match="naive datetime"):
        compute_features(golden_context(), GOLDEN_TARGET, naive)
    with pytest.raises(FeatureInputError, match="naive datetime"):
        Matchup("x", naive, SEASON, "A", "B")
    with pytest.raises(FeatureInputError, match="naive datetime"):
        Game("x", naive, SEASON, "A", "B", 110, 100)


def test_a_tied_final_score_is_refused_as_corrupt_input():
    with pytest.raises(FeatureInputError, match="tied"):
        game("x", 1, "A", "B", 100, 100)


def test_a_team_cannot_play_itself():
    with pytest.raises(FeatureInputError, match="same team"):
        game("x", 1, "A", "A", 110, 100)
    with pytest.raises(FeatureInputError, match="same team"):
        matchup("x", 1, "A", "A")


def test_history_must_contain_game_records():
    with pytest.raises(FeatureInputError, match="must contain Game records"):
        GameHistory([object()])


def test_duplicate_game_ids_in_history_are_refused():
    """F-046: a duplicated game is counted twice in every feature derived from it."""
    with pytest.raises(FeatureInputError, match="duplicate game_id"):
        GameHistory([*GOLDEN_HISTORY, GOLDEN_HISTORY[0]])


def test_a_one_shot_iterator_is_refused_rather_than_silently_yielding_priors():
    """F-045: an index built from a generator consumes it, so the second call sees an empty history
    and returns values indistinguishable from a legitimate cold start."""
    with pytest.raises(FeatureInputError, match="one-shot iterator"):
        GameHistory(g for g in GOLDEN_HISTORY)


def test_mistyped_keys_are_refused_rather_than_missing_every_lookup():
    with pytest.raises(FeatureInputError, match="season must be an int"):
        Matchup("x", at(1), "2024", "A", "B")
    with pytest.raises(FeatureInputError, match="home_id must be a str"):
        Matchup("x", at(1), SEASON, 1, "B")


def test_non_integer_scores_are_refused_including_nan():
    """F-049: `nan != nan`, so a NaN score sails through the tie check and poisons the vector."""
    for bad in (float("nan"), 110.5, "110"):
        with pytest.raises(FeatureInputError, match="must be an int"):
            Game("x", at(1), SEASON, "A", "B", bad, 100)


def test_bool_is_not_accepted_as_a_season_or_a_score():
    with pytest.raises(FeatureInputError, match="season must be an int"):
        Matchup("x", at(1), True, "A", "B")
    with pytest.raises(FeatureInputError, match="must be an int"):
        Game("x", at(1), SEASON, "A", "B", True, 100)


def test_game_id_must_be_a_string():
    with pytest.raises(FeatureInputError, match="game_id must be a str"):
        Matchup(42, at(1), SEASON, "A", "B")


def test_a_game_id_collision_against_a_different_opponent_is_refused():
    """F-068: the exclusion must not be a blunt id match. Dropping any record sharing the target's
    id made the control fail OPEN -- it silently deleted a real historical game from both windows."""
    collision = Game("target", at(3), SEASON, "A", "E", 130, 90)
    context = ctx([*GOLDEN_HISTORY, collision], cities={**GOLDEN_CITIES, "target": BOSTON})
    with pytest.raises(FeatureInputError, match="game ids must identify one game"):
        compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF)


def test_index_is_a_function_of_the_set_of_games_not_their_order():
    rng = random.Random(11)
    expected = golden()
    for _ in range(20):
        shuffled = list(GOLDEN_HISTORY)
        rng.shuffle(shuffled)
        assert compute_features(ctx(shuffled, cities=GOLDEN_CITIES), GOLDEN_TARGET, GOLDEN_AS_OF) == expected


def test_neutral_site_defaults_to_false_on_both_types():
    assert game("x", 1, "A", "B", 110, 100).neutral_site is False
    assert matchup("x", 1, "A", "B").neutral_site is False


# --- Coverage: the completeness half of the guarantee (F-113, F-115) ------------------------------


def test_an_incomplete_history_silently_returns_priors_when_it_declares_nothing():
    """The failure this machinery exists for, demonstrated. A history missing B's games produces a
    perfectly ordinary-looking vector -- which is why the declaration, not the symptom, is the check.
    """
    without_b = [g for g in GOLDEN_HISTORY if "B" not in (g.home_id, g.away_id)]
    cities = {g.game_id: GOLDEN_CITIES[g.game_id] for g in without_b}
    cities["target"] = BOSTON
    people = participation(without_b)
    people["B"] = []
    got = compute_features(Context(without_b, people, cities), GOLDEN_TARGET, GOLDEN_AS_OF)
    assert set(got) == set(FEATURE_NAMES)
    assert got != golden()


def test_a_team_narrowed_history_refuses_a_target_it_does_not_cover():
    coverage = Coverage(teams=frozenset({"A", "C", "D"}))
    context = ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES, coverage=coverage)
    with pytest.raises(FeatureInputError, match="does not include"):
        compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF)


def test_a_team_narrowed_history_still_computes_the_teams_it_does_cover():
    coverage = Coverage(teams=frozenset({"A", "B", "C", "D"}))
    context = ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES, coverage=coverage)
    assert compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF) == golden()


def test_declaring_an_empty_coverage_is_identical_to_declaring_nothing():
    context = ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES, coverage=Coverage())
    assert compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF) == golden()


def test_a_history_declaring_a_start_is_refused_because_lookback_is_unbounded():
    """Blunt on purpose: rest is not season-scoped and `elo_diff` is running state over every prior
    season including D-037's warm-up, so no lower bound is ever sufficient."""
    coverage = Coverage(complete_from=at(-30))
    context = ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES, coverage=coverage)
    with pytest.raises(FeatureInputError, match="cannot support features that look back"):
        compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF)


def test_an_as_of_past_the_declared_record_end_is_refused():
    coverage = Coverage(complete_to=at(8.5))
    context = ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES, coverage=coverage)
    with pytest.raises(FeatureInputError, match="declared record end"):
        compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF)


def test_the_complete_to_boundary_is_inclusive_at_the_exact_instant():
    coverage = Coverage(complete_to=GOLDEN_AS_OF)
    context = ctx(GOLDEN_HISTORY, cities=GOLDEN_CITIES, coverage=coverage)
    assert compute_features(context, GOLDEN_TARGET, GOLDEN_AS_OF) == golden()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"complete_from": datetime(2024, 1, 1)}, "naive datetime"),  # noqa: DTZ001
        ({"complete_from": at(5), "complete_to": at(1)}, "must be strictly before"),
        ({"teams": {"A"}}, "must be a frozenset"),
        ({"teams": frozenset()}, "covering no teams"),
    ],
)
def test_a_malformed_coverage_is_refused_at_construction(kwargs, match):
    with pytest.raises(FeatureInputError, match=match):
        Coverage(**kwargs)


def test_a_zero_width_coverage_range_is_refused():
    with pytest.raises(FeatureInputError, match="must be strictly before"):
        Coverage(complete_from=at(1), complete_to=at(1))


def test_a_coverage_subclass_is_refused():
    """F-115: a frozen-dataclass subclass overriding `__post_init__` without `super()` skips every
    validation, and was demonstrated taking a *mutable* set that could be widened after the check."""

    @dataclass(frozen=True)
    class Widened(Coverage):
        def __post_init__(self):
            pass

    with pytest.raises(FeatureInputError, match="must be exactly a Coverage"):
        GameHistory(GOLDEN_HISTORY, coverage=Widened())


# --- the estimator contract ----------------------------------------------------------------------


def test_to_vector_emits_features_in_the_declared_order():
    assert to_vector(GOLDEN_EXPECTED) == pytest.approx(
        tuple(GOLDEN_EXPECTED[name] for name in FEATURE_NAMES)
    )


def test_to_vector_order_is_pinned_to_literals_not_to_feature_names():
    """A test written as `tuple(features[n] for n in FEATURE_NAMES)` passes no matter how the order
    changes, which is exactly the drift that silently re-labels a fitted artifact's coefficients."""
    assert FEATURE_NAMES == (
        "elo_diff",
        "home_b2b",
        "away_b2b",
        "rest_edge",
        "avail_diff",
        "travel_diff",
        "altitude",
    )
    assert to_vector(
        {
            "elo_diff": 1.0,
            "home_b2b": 2.0,
            "away_b2b": 3.0,
            "rest_edge": 4.0,
            "avail_diff": 5.0,
            "travel_diff": 6.0,
            "altitude": 7.0,
        }
    ) == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)


def test_to_vector_refuses_a_mapping_that_does_not_match_the_contract():
    with pytest.raises(FeatureInputError, match="missing="):
        to_vector({name: 0.0 for name in FEATURE_NAMES[:-1]})
    with pytest.raises(FeatureInputError, match="unexpected="):
        to_vector({**{name: 0.0 for name in FEATURE_NAMES}, "form_diff": 1.0})


def test_to_vector_refuses_non_finite_and_non_numeric_values():
    for bad in (float("nan"), float("inf"), "1.0", True):
        values = {name: 0.0 for name in FEATURE_NAMES}
        values["elo_diff"] = bad
        with pytest.raises(FeatureInputError):
            to_vector(values)


def test_compute_training_features_uses_the_tip_off_instant_exactly():
    """The rule with no parameter: a training loop cannot pick a leaky as-of because it picks none."""
    target = game("g6", 9, "A", "B", 150, 70)
    context = ctx([*GOLDEN_HISTORY, target], cities={**GOLDEN_CITIES, "g6": BOSTON})
    assert compute_training_features(context, target) == compute_features(
        context, target.matchup, target.date
    )
