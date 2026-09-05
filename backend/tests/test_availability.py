"""T-027 -- lagged rotation availability (D-035).

The plan names four behavioural tests: **a high-minutes player missing three games moves the number;
a low-minutes player missing three does not; a zero-minute row in a blowout does not read as
absence; a season opener with no history returns the prior.** All four are here, plus the leakage
tripwire T-027's security note demands.

That security note says this feature is *one line of code away from leakage* -- reading participation
for the game being predicted rather than prior games. The tests below check both structural defences:
the module holds no database handle to point at the wrong game, and it **refuses** rather than drops
an appearance dated at or after the as-of moment. Dropping would let a mis-filtered caller work by
accident, which is precisely how the as-of control migrates out of `features.py`, where T-006's
property test actually proves it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from model.availability import (
    DEFAULT_PRIOR,
    Appearance,
    AppearanceIndex,
    AvailabilityConfig,
    AvailabilityError,
    availability_difference,
    team_availability,
)

START = datetime(2024, 1, 1, tzinfo=UTC)

# A plausible nine-man rotation: one star, three starters, five reserves.
ROTATION = {
    "star": 36.0,
    "s2": 32.0,
    "s3": 30.0,
    "s4": 28.0,
    "r1": 20.0,
    "r2": 18.0,
    "r3": 15.0,
    "r4": 12.0,
    "r5": 10.0,
}


def _game_rows(
    index: int, *, absent: set[str] | None = None, zero_minute: set[str] | None = None
) -> list[Appearance]:
    """One team-game. `absent` players are DNPs; `zero_minute` players are active on 0 minutes."""
    absent = absent or set()
    zero_minute = zero_minute or set()
    rows = []
    for player_id, minutes in ROTATION.items():
        if player_id in absent:
            rows.append(
                Appearance(f"g{index}", START + timedelta(days=index), player_id, None, True)
            )
        elif player_id in zero_minute:
            rows.append(
                Appearance(f"g{index}", START + timedelta(days=index), player_id, 0.0, False)
            )
        else:
            rows.append(
                Appearance(f"g{index}", START + timedelta(days=index), player_id, minutes, False)
            )
    return rows


def _history(n: int, **kwargs) -> list[Appearance]:
    return [row for i in range(n) for row in _game_rows(i, **kwargs)]


def _history_with_recent(n: int, recent: int, **kwargs) -> list[Appearance]:
    """`n` clean games, then `recent` games with `kwargs` applied. The rotation is defined over the
    whole span, so the absent player is still a rotation member when he goes missing."""
    rows = [row for i in range(n) for row in _game_rows(i)]
    rows += [row for i in range(n, n + recent) for row in _game_rows(i, **kwargs)]
    return rows


# ── the four behavioural tests the plan names ─────────────────────────────────


def test_a_high_minutes_player_missing_three_games_moves_the_number() -> None:
    """The whole point of the feature. A 36-minute star is roughly 18% of a nine-man rotation's
    weight, so his absence has to be visible."""
    healthy = team_availability(_history(12))
    injured = team_availability(_history_with_recent(9, 3, absent={"star"}))
    assert injured < healthy
    assert healthy - injured > 0.05, "a star's absence must be more than a rounding difference"


def test_a_low_minutes_player_missing_three_games_barely_moves_it() -> None:
    """The other half. A 10-minute reserve is real but small, and a feature that treated him like
    the star would be measuring roster churn rather than availability."""
    healthy = team_availability(_history(12))
    reserve_out = team_availability(_history_with_recent(9, 3, absent={"r5"}))
    star_out = team_availability(_history_with_recent(9, 3, absent={"star"}))
    assert reserve_out < healthy
    assert healthy - reserve_out < 0.03
    assert (healthy - star_out) > 3 * (healthy - reserve_out)


def test_a_zero_minute_row_in_a_blowout_does_not_read_as_absence() -> None:
    """User story 12, and the distinction the whole T-021 schema was built around.

    In the real corpus 31,769 rows are `did_not_play` with null minutes while 700 are active on
    exactly zero minutes. Counting the second as absence would make this feature partly a blowout
    detector -- and blowouts are outcomes, so that is leakage arriving by the back door.
    """
    healthy = team_availability(_history(12))
    garbage_time = team_availability(_history_with_recent(9, 3, zero_minute={"star", "s2", "s3"}))
    assert garbage_time == pytest.approx(healthy)


def test_no_history_returns_the_prior_exactly() -> None:
    """A season opener. The prior is the corpus mean (.8764), not 1.0 -- a team with no record is
    average, not perfectly healthy, and 1.0 would make every opener the healthiest game of the
    year."""
    assert team_availability([]) == DEFAULT_PRIOR
    assert team_availability([]) == pytest.approx(0.8764)


# ── the leakage tripwire (T-027's security note) ──────────────────────────────


def test_an_appearance_at_or_after_the_as_of_moment_is_refused() -> None:
    """The security note's "one line of code away from leakage", closed structurally.

    Note it refuses at `>=`, not `>`: an appearance dated exactly at the prediction moment is the
    game being predicted, which is the single row that must never be read.
    """
    history = _history(5)
    as_of = START + timedelta(days=4)  # game index 4 is dated exactly here
    with pytest.raises(AvailabilityError, match="dated at or after as_of"):
        team_availability(history, as_of=as_of)


def test_the_tripwire_refuses_rather_than_dropping() -> None:
    """A dropping filter would make a mis-filtered caller work by accident, and the as-of control
    would quietly migrate out of `features` where T-006's property test proves it. The error names
    that reasoning so a future reader does not "fix" it into a filter."""
    with pytest.raises(AvailabilityError) as excinfo:
        team_availability(_history(5), as_of=START + timedelta(days=2))
    message = str(excinfo.value)
    assert "refused rather than dropped" in message
    assert "D-035" in message


def test_correctly_filtered_history_passes_the_tripwire() -> None:
    """Non-vacuity: a tripwire that refused everything would satisfy the two tests above and make
    the feature uncomputable."""
    history = _history(5)  # games dated day 0..4
    as_of = START + timedelta(days=5)
    assert 0.0 <= team_availability(history, as_of=as_of) <= 1.0


def test_a_naive_as_of_is_refused() -> None:
    with pytest.raises(AvailabilityError, match="as_of must be timezone-aware"):
        team_availability(_history(3), as_of=datetime(2024, 1, 9))  # noqa: DTZ001


def test_the_module_holds_no_database_handle() -> None:
    """T-027's security note: the data must arrive through the Context and the as-of filter, never
    from a query this module makes. There is no handle here to point at the wrong game."""
    import ast
    from pathlib import Path

    from model import availability as module

    tree = ast.parse(Path(module.__file__).read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert not (roots & {"sqlalchemy", "psycopg2", "pandas", "numpy"}), roots
    assert "store" not in Path(module.__file__).read_text().split("import")[0]


def test_a_generator_is_refused() -> None:
    """F-045's shape: consumed on first use, then the prior forever -- indistinguishable from a team
    with no history, which is the failure this feature is least able to notice."""
    rows = _history(4)
    with pytest.raises(AvailabilityError, match="one-shot iterator is refused"):
        team_availability(r for r in rows)  # type: ignore[arg-type]


def test_a_naive_appearance_date_is_refused() -> None:
    with pytest.raises(AvailabilityError, match="naive date"):
        Appearance("g1", datetime(2024, 1, 1), "star", 30.0, False)  # noqa: DTZ001


# ── the construction ──────────────────────────────────────────────────────────


def test_a_fully_present_rotation_scores_high_but_never_reaches_one() -> None:
    """Worth knowing when reading feature values: shrinkage caps a perfectly healthy team at
    `5/6 * 1.0 + 1/6 * prior` = **.9794**, not 1.0. Five games of evidence is not certainty, and the
    ceiling is a consequence of saying so rather than an error."""
    value = team_availability(_history(10))
    assert value == pytest.approx(5 / 6 + DEFAULT_PRIOR / 6)
    assert 0.97 < value < 1.0


def test_an_entirely_absent_rotation_scores_near_zero() -> None:
    """Shrinkage keeps it off exactly zero, which is correct: five games of evidence is not
    certainty."""
    value = team_availability(_history_with_recent(10, 5, absent=set(ROTATION)))
    assert value < 0.20


def test_the_result_is_always_a_share() -> None:
    for history in (
        [],
        _history(1),
        _history(20),
        _history_with_recent(10, 5, absent={"star", "s2"}),
        _history_with_recent(10, 5, absent=set(ROTATION)),
    ):
        assert 0.0 <= team_availability(history) <= 1.0


def test_absence_is_weighted_by_minutes_not_by_headcount() -> None:
    """Two reserves out should hurt less than the star out, even though it is more players. A
    headcount-based measure would get this backwards."""
    star_out = team_availability(_history_with_recent(9, 3, absent={"star"}))
    two_reserves_out = team_availability(_history_with_recent(9, 3, absent={"r4", "r5"}))
    assert star_out < two_reserves_out


def test_a_longer_absence_reads_as_less_available_than_a_one_game_absence() -> None:
    """The window is what makes this a *multi-game* absence detector, per D-035."""
    one_game = team_availability(_history_with_recent(11, 1, absent={"star"}))
    three_games = team_availability(_history_with_recent(9, 3, absent={"star"}))
    assert three_games < one_game


def test_an_old_absence_falls_out_of_the_window() -> None:
    """A player who missed games long ago and has since returned must not still read as absent."""
    recovered = [row for i in range(3) for row in _game_rows(i, absent={"star"})]
    recovered += [row for i in range(3, 15) for row in _game_rows(i)]
    assert team_availability(recovered) == pytest.approx(team_availability(_history(15)), abs=0.02)


def test_a_short_history_is_shrunk_toward_the_prior() -> None:
    """A team with one game of evidence must not speak with the confidence of one with five.

    Both teams below are perfectly healthy, so both read above the prior — but the one-game reading
    is pulled further back toward it. That is `n/(n+k)` doing its job, D-015's shape.
    """
    one = team_availability(_history(1))
    five = team_availability(_history(5))
    assert DEFAULT_PRIOR < one < five
    assert one == pytest.approx(0.5 + DEFAULT_PRIOR / 2)


def test_a_team_whose_rotation_has_never_played_returns_the_prior() -> None:
    """The degenerate case: every row in the only game is a DNP, so there is no rotation to measure
    against. That is "no history", not "zero availability" — and the difference matters, because
    zero would be the loudest possible signal derived from no evidence at all."""
    assert team_availability(_history(1, absent=set(ROTATION))) == DEFAULT_PRIOR


def test_players_outside_the_rotation_do_not_affect_the_number() -> None:
    """A deep-bench player's DNP is not news. Including him would make the feature track roster size
    rather than availability."""
    base = _history(12)
    with_bench = list(base)
    for i in range(12):
        with_bench.append(
            Appearance(f"g{i}", START + timedelta(days=i), "bench15", None, True)
        )
    assert team_availability(with_bench) == pytest.approx(team_availability(base))


def test_appearances_are_ordered_by_date_not_by_caller_order() -> None:
    """`elo` and T-007's lesson again: dates are the fact, a caller's ordering is a claim."""
    history = _history_with_recent(9, 3, absent={"star"})
    assert team_availability(list(reversed(history))) == pytest.approx(
        team_availability(history)
    )


# ── the difference feature ────────────────────────────────────────────────────


def test_the_difference_is_signed_toward_the_home_team() -> None:
    healthy = _history(12)
    injured = _history_with_recent(9, 3, absent={"star", "s2"})
    assert availability_difference(healthy, injured) > 0
    assert availability_difference(injured, healthy) < 0


def test_two_equally_healthy_teams_differ_by_zero() -> None:
    assert availability_difference(_history(12), _history(12)) == pytest.approx(0.0)


def test_the_difference_is_bounded_by_plus_or_minus_one() -> None:
    all_out = _history_with_recent(10, 5, absent=set(ROTATION))
    assert -1.0 <= availability_difference(_history(15), all_out) <= 1.0


# ── configuration ─────────────────────────────────────────────────────────────


def test_the_measured_defaults_are_the_ones_recorded() -> None:
    """Guards the constants against an edit that makes something pass. These came from 13,120
    team-games in the ingested corpus, not from taste."""
    config = AvailabilityConfig()
    assert config.prior == 0.8764
    assert config.rotation_size == 9
    assert config.rotation_games == 15
    assert config.window_games == 5


@pytest.mark.parametrize("field", ["rotation_games", "rotation_size", "window_games"])
def test_a_non_positive_window_is_refused(field: str) -> None:
    with pytest.raises(AvailabilityError, match="must be at least 1"):
        AvailabilityConfig(**{field: 0})


@pytest.mark.parametrize("prior", [-0.1, 1.1])
def test_a_prior_outside_zero_to_one_is_refused(prior: float) -> None:
    """Availability is a share of rotation weight present. A prior outside [0, 1] is not a value the
    feature can take, so it would be a silent out-of-range default for every season opener."""
    with pytest.raises(AvailabilityError, match=r"prior must be a share in \[0, 1\]"):
        AvailabilityConfig(prior=prior)


def test_a_smaller_rotation_concentrates_the_weight() -> None:
    """The knobs are real, not decorative: a five-man rotation makes the star a bigger share, so his
    absence hurts more."""
    nine = AvailabilityConfig(rotation_size=9)
    five = AvailabilityConfig(rotation_size=5)
    history = _history_with_recent(9, 3, absent={"star"})
    assert team_availability(history, config=five) < team_availability(history, config=nine)


# --- AppearanceIndex: as-of addressable participation (T-028) --------------------------------------
#
# `features.Context` reads this instead of calling `team_availability` with a team's whole history,
# for the same reason `elo.Timeline` exists: the naive form is O(n) per feature vector. And for the
# same reason, the first test is the one that makes the shortcut safe -- the index must produce the
# *same float* as the unsliced call, at every moment, not merely a close one.


def _long_history(n: int, seed: int = 17) -> list[Appearance]:
    """A season-length history with absences scattered through it, so a truncated window and a full
    one have every opportunity to disagree."""
    import random

    rng = random.Random(seed)
    rows: list[Appearance] = []
    for i in range(n):
        absent = {p for p in ROTATION if rng.random() < 0.2}
        rows.extend(_game_rows(i, absent=absent))
    rng.shuffle(rows)  # the index must not depend on the order it is handed
    return rows


@pytest.mark.parametrize("seed", [17, 205, 3001])
def test_the_index_answers_exactly_what_the_unsliced_call_would(seed):
    """**The test that makes the slice safe rather than plausible.**

    The index hands `team_availability` `lookback_games + 1` games; the reference hands it the whole
    as-of filtered history. Exact equality at every moment in a 60-game season, so the slice is
    proven to be a performance decision and not a change of definition.
    """
    rows = _long_history(60, seed=seed)
    index = AppearanceIndex({"A": rows})
    for i in range(1, 61):
        as_of = START + timedelta(days=i)
        reference = team_availability(
            sorted([r for r in rows if r.date < as_of], key=lambda r: (r.date, r.game_id)),
            as_of=as_of,
        )
        assert index.availability_before("A", as_of) == reference, i


def test_lookback_games_is_the_wider_of_the_two_windows():
    """The accessor exists so a caller slicing a history does not have to know *which* window is
    wider -- a caller that hard-coded 15 would silently truncate the rotation the day
    `rotation_games` moved."""
    assert AvailabilityConfig().lookback_games == max(
        AvailabilityConfig().rotation_games, AvailabilityConfig().window_games
    )
    assert AvailabilityConfig(rotation_games=30, window_games=5).lookback_games == 30
    assert AvailabilityConfig(rotation_games=3, window_games=9).lookback_games == 9


def test_the_index_returns_the_prior_for_a_team_it_has_never_seen():
    """Which is exactly why `features.Context` refuses to be built with participation it cannot
    vouch for: this value is indistinguishable from a legitimate cold start."""
    assert AppearanceIndex({"A": _history(5)}).availability_before(
        "NOBODY", START + timedelta(days=10)
    ) == DEFAULT_PRIOR


def test_the_index_excludes_a_named_game_even_when_it_is_inside_the_window():
    """F-044's shape on the participation source: a target's own box rows dated microseconds before
    its matchup date. The DNP rows of a blowout are precisely what would move this number, and they
    are post-game information about the game being predicted."""
    rows = _long_history(20, seed=42)
    index = AppearanceIndex({"A": rows})
    as_of = START + timedelta(days=20)
    with_it = index.availability_before("A", as_of)
    without = index.availability_before("A", as_of, exclude_game_id="g19")
    reference = team_availability(
        sorted(
            [r for r in rows if r.date < as_of and r.game_id != "g19"],
            key=lambda r: (r.date, r.game_id),
        ),
        as_of=as_of,
    )
    assert without == reference
    assert without != with_it  # the control: the excluded game was actually contributing


def test_the_index_difference_is_the_two_teams_availabilities_subtracted():
    a, b = _long_history(30, seed=1), _long_history(30, seed=2)
    index = AppearanceIndex({"A": a, "B": b})
    as_of = START + timedelta(days=30)
    assert index.difference_before("A", "B", as_of) == (
        index.availability_before("A", as_of) - index.availability_before("B", as_of)
    )


def test_the_index_reports_the_teams_it_covers():
    assert AppearanceIndex({"A": _history(3), "B": _history(3)}).teams() == frozenset({"A", "B"})


def test_the_index_refuses_a_naive_as_of():
    index = AppearanceIndex({"A": _history(5)})
    with pytest.raises(AvailabilityError, match="timezone-aware"):
        index.availability_before("A", datetime(2024, 1, 10))  # noqa: DTZ001 -- the point of the test


def test_the_index_refuses_a_one_shot_iterator_and_a_non_mapping():
    """F-045 again: a generator consumed at construction leaves the team looking like it never
    played, which is a value the feature legitimately takes."""
    with pytest.raises(AvailabilityError, match="one-shot iterator"):
        AppearanceIndex({"A": (row for row in _history(5))})
    with pytest.raises(AvailabilityError, match="must be a Mapping"):
        AppearanceIndex([("A", _history(5))])


def test_the_index_refuses_rows_that_are_not_appearances():
    with pytest.raises(AvailabilityError, match="must contain Appearance records"):
        AppearanceIndex({"A": [object()]})


def test_the_index_honours_a_non_default_config():
    """The knobs belong to this module, and the index must not quietly substitute its own -- which a
    hard-coded slice width would do the moment `rotation_games` changed."""
    rows = _long_history(40, seed=88)
    as_of = START + timedelta(days=40)
    narrow = AvailabilityConfig(window_games=1)
    assert AppearanceIndex({"A": rows}, config=narrow).availability_before("A", as_of) == (
        team_availability(
            sorted([r for r in rows if r.date < as_of], key=lambda r: (r.date, r.game_id)),
            as_of=as_of,
            config=narrow,
        )
    )
