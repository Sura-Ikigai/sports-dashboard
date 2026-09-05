"""T-026 -- venue geography (D-036).

The plan asks for behavioural tests: **known distances, the altitude flag at Denver, and a same-city
pair at zero.** Those are here, plus the one property T-026's acceptance turns on -- *a venue absent
from the table raises rather than defaulting* -- and the DST case that makes timezone shift a real
computation rather than a lookup.

D-036 sets expectations honestly: travel and altitude are worth roughly **+.002 AUC** and are
included because they are nearly free, not because they are expected to matter. These tests are
sized accordingly -- they pin the arithmetic and the refusals, not a model outcome.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from model.venues import (
    CITIES,
    HIGH_ALTITUDE_FEET,
    City,
    VenueError,
    altitude_feet,
    assert_covers,
    city_for,
    distance_miles,
    is_high_altitude,
    timezone_shift_hours,
    travel_miles,
)

# Every (city, state) pair hosting a game in the corpus, observed 2026-09-04 across 45 venues.
# Pinned here so coverage is asserted in CI, where there is no ingested corpus to read. If the
# corpus grows a city the runtime path raises (by design) and this list is updated deliberately --
# the same discipline as the loader's pinned counts.
CORPUS_CITY_STATES: tuple[tuple[str, str | None], ...] = (
    ("Atlanta", "GA"), ("Austin", "TX"), ("Berlin", None), ("Boston", "MA"), ("Brooklyn", "NY"),
    ("Charlotte", "NC"), ("Chicago", "IL"), ("Cleveland", "OH"), ("Dallas", "TX"), ("Denver", "CO"),
    ("Detroit", "MI"), ("Houston", "TX"), ("Indianapolis", "IN"), ("Inglewood", "CA"),
    ("Las Vegas", "NV"), ("London", None), ("Los Angeles", "CA"), ("Memphis", "TN"),
    ("Mexico City", None), ("Miami", "FL"), ("Milwaukee", "WI"), ("Minneapolis", "MN"),
    ("New Orleans", "LA"), ("New York", "NY"), ("Oakland", "CA"), ("Oklahoma City", "OK"),
    ("Orlando", "FL"), ("Paris", None), ("Philadelphia", "PA"), ("Phoenix", "AZ"),
    ("Portland", "OR"), ("Sacramento", "CA"), ("Salt Lake City", "UT"), ("San Antonio", "TX"),
    ("San Francisco", "CA"), ("Toronto", "ON"), ("Washington", "DC"),
)


# ── coverage ──────────────────────────────────────────────────────────────────


def test_every_city_in_the_corpus_is_in_the_table() -> None:
    """T-026's acceptance: the static table covers every venue in the corpus.

    Verified against the live ingested corpus on 2026-09-04 (37 pairs across 45 venues); pinned here
    so it keeps being checked in CI, which has no corpus to read.
    """
    assert_covers(CORPUS_CITY_STATES)


def test_the_pinned_corpus_cities_are_the_ones_measured() -> None:
    """Guards the pin itself, the same role `EXPECTED_COMPLETED_COUNTS` plays for the loader."""
    assert len(CORPUS_CITY_STATES) == 37
    assert len(set(CORPUS_CITY_STATES)) == 37


def test_the_table_contains_nothing_the_corpus_does_not_use() -> None:
    """Not a correctness requirement, but a drift check: an entry nobody uses is an entry nobody
    verifies, and it would quietly rot until the day a game is played there."""
    assert set(CITIES) == set(CORPUS_CITY_STATES)


def test_every_entry_has_a_plausible_coordinate_and_a_real_timezone() -> None:
    from zoneinfo import ZoneInfo

    for key, city in CITIES.items():
        assert -90 <= city.latitude <= 90, key
        assert -180 <= city.longitude <= 180, key
        assert city.elevation_feet >= 0, key
        ZoneInfo(city.timezone)  # raises if the zone name is not real


# ── known distances ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "origin,destination,expected_miles",
    [
        (("Los Angeles", "CA"), ("New York", "NY"), 2448),
        (("Boston", "MA"), ("New York", "NY"), 188),
        (("Portland", "OR"), ("Miami", "FL"), 2704),
        (("Denver", "CO"), ("Salt Lake City", "UT"), 371),
        (("Toronto", "ON"), ("Chicago", "IL"), 435),
        (("London", None), ("New York", "NY"), 3457),
    ],
)
def test_known_distances(
    origin: tuple[str, str | None], destination: tuple[str, str | None], expected_miles: float
) -> None:
    """Great-circle distances against independently known values, at 1% tolerance -- the haversine's
    sphere approximation is good to roughly 0.5% against the true ellipsoid."""
    actual = travel_miles(origin[0], origin[1], destination[0], destination[1])
    assert actual == pytest.approx(expected_miles, rel=0.01)


def test_a_same_city_pair_is_zero() -> None:
    """The plan names this one explicitly. It is also the value the module must never produce by
    accident -- see `test_an_unknown_city_raises_rather_than_returning_zero_travel`."""
    denver = city_for("Denver", "CO")
    assert distance_miles(denver, denver) == 0.0


def test_two_arenas_in_the_same_metro_are_close_but_not_identical() -> None:
    """Brooklyn and Manhattan are separate corpus cities. A team moving between them travels almost
    nothing, which is the right answer -- and not the same answer as an unknown venue."""
    assert 0 < travel_miles("Brooklyn", "NY", "New York", "NY") < 15


def test_distance_is_symmetric() -> None:
    there = travel_miles("Boston", "MA", "Los Angeles", "CA")
    back = travel_miles("Los Angeles", "CA", "Boston", "MA")
    assert there == pytest.approx(back)


def test_distance_satisfies_the_triangle_inequality() -> None:
    """A property rather than a case: it holds for any correct great-circle metric and fails for a
    whole class of coordinate or formula errors that individual distances can survive."""
    a, b, c = city_for("Portland", "OR"), city_for("Denver", "CO"), city_for("Miami", "FL")
    assert distance_miles(a, c) <= distance_miles(a, b) + distance_miles(b, c) + 1e-9


def test_the_longest_trip_in_the_corpus_is_transatlantic() -> None:
    """Sanity on the international venues, which are exactly the games a defaulting lookup would
    have reported as zero travel."""
    pairs = [
        (x, y)
        for x in CITIES.values()
        for y in CITIES.values()
    ]
    longest = max(pairs, key=lambda p: distance_miles(*p))
    assert {longest[0].name, longest[1].name} & {"Berlin", "London", "Paris"}


# ── altitude ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "city,state,elevation",
    [("Denver", "CO", 5280), ("Salt Lake City", "UT", 4226), ("Mexico City", None, 7350)],
)
def test_the_high_altitude_cities_are_flagged(city: str, state: str | None, elevation: float) -> None:
    """User story 9: Denver and Salt Lake City are not ordinary road games."""
    assert altitude_feet(city, state) == elevation
    assert is_high_altitude(city, state)


@pytest.mark.parametrize(
    "city,state", [("Las Vegas", "NV"), ("Phoenix", "AZ"), ("Oklahoma City", "OK"), ("Boston", "MA")]
)
def test_ordinary_elevations_are_not_flagged(city: str, state: str | None) -> None:
    """Non-vacuity. A flag that fired everywhere would satisfy the test above and measure nothing."""
    assert not is_high_altitude(city, state)


def test_the_altitude_threshold_sits_clear_of_both_observed_populations() -> None:
    """The same reasoning `corpus.MIN_SEASON_GAMES_FOR_A_REAL_TEAM` uses, and the same warning.

    High: Mexico City 7,350 / Denver 5,280 / Salt Lake City 4,226. Next down: Las Vegas at 2,001.
    Nothing sits between, so every threshold in that band separates the populations identically and
    none is near an edge. If this ever needs moving to make something pass, check which population
    changed rather than moving the number.
    """
    elevations = sorted(city.elevation_feet for city in CITIES.values())
    high = [e for e in elevations if e >= HIGH_ALTITUDE_FEET]
    low = [e for e in elevations if e < HIGH_ALTITUDE_FEET]
    assert high == [4226.0, 5280.0, 7350.0]
    assert max(low) == 2001.0
    assert max(low) < HIGH_ALTITUDE_FEET < min(high)


# ── a missing venue raises. This is the module's whole security note. ─────────


def test_an_unknown_city_raises_rather_than_returning_zero_travel() -> None:
    """T-026's acceptance criterion and its security note, in one test.

    A silent zero reads as "no travel" -- the strongest possible signal for a home stand -- and it
    would be produced for exactly the games this feature was added to describe.
    """
    with pytest.raises(VenueError, match="is not in the venue table"):
        city_for("Nowhere", "ZZ")
    with pytest.raises(VenueError, match="is not in the venue table"):
        travel_miles("Denver", "CO", "Nowhere", "ZZ")
    with pytest.raises(VenueError, match="is not in the venue table"):
        altitude_feet("Nowhere", "ZZ")


def test_the_error_message_says_what_to_do_rather_than_only_what_failed() -> None:
    """Whoever hits this is looking at a game that will not compute. The fix -- add the city with
    coordinates, elevation and a timezone -- has to be in the message, and so does the reason not to
    just make it default."""
    with pytest.raises(VenueError) as excinfo:
        city_for("Nowhere", "ZZ")
    message = str(excinfo.value)
    assert "coordinates" in message and "elevation" in message
    assert "do not let it default" in message


def test_a_blank_city_raises(  ) -> None:
    for blank in ("", "   "):
        with pytest.raises(VenueError, match="blank city name"):
            city_for(blank, "CO")


def test_the_state_is_part_of_the_key() -> None:
    """"Portland" is Oregon in this corpus and Maine elsewhere. A city-name-only table would map one
    onto the other silently, and the distance error would be ~2,500 miles."""
    assert city_for("Portland", "OR").latitude == pytest.approx(45.5316)
    with pytest.raises(VenueError):
        city_for("Portland", "ME")


def test_the_international_cities_are_keyed_with_no_state() -> None:
    """The corpus leaves `state` NULL for them, which is why `corpus_venues.state` is nullable."""
    for name in ("London", "Paris", "Berlin", "Mexico City"):
        assert city_for(name).state is None


def test_lookup_tolerates_case_and_surrounding_whitespace_but_not_a_different_name() -> None:
    """Normalization must never map an unrecognized name onto a recognized one -- that is the silent
    default this module exists to refuse."""
    assert city_for("  denver ", "co") is city_for("Denver", "CO")
    with pytest.raises(VenueError):
        city_for("Denver City", "CO")


def test_assert_covers_reports_every_missing_pair_at_once() -> None:
    """A caller checking a freshly read corpus wants the whole list, not the first failure."""
    with pytest.raises(VenueError) as excinfo:
        assert_covers([("Denver", "CO"), ("Nowhere", "ZZ"), ("Elsewhere", "YY")])
    message = str(excinfo.value)
    assert "Nowhere" in message and "Elsewhere" in message and "2 city/state" in message


# ── timezone shift ────────────────────────────────────────────────────────────


def test_travelling_east_is_a_positive_shift() -> None:
    at = datetime(2024, 1, 15, tzinfo=UTC)
    assert timezone_shift_hours(city_for("Los Angeles", "CA"), city_for("New York", "NY"), at) == 3.0
    assert timezone_shift_hours(city_for("New York", "NY"), city_for("Los Angeles", "CA"), at) == -3.0


def test_arizona_makes_the_shift_depend_on_the_date() -> None:
    """The case that makes this a computation rather than a lookup.

    Arizona does not observe DST, so Phoenix-to-Denver is **zero hours in January and one hour in
    June** -- and an NBA season spans both. A fixed per-city offset would be wrong for half of it.
    """
    phoenix, denver = city_for("Phoenix", "AZ"), city_for("Denver", "CO")
    winter = timezone_shift_hours(phoenix, denver, datetime(2024, 1, 15, tzinfo=UTC))
    summer = timezone_shift_hours(phoenix, denver, datetime(2024, 6, 15, tzinfo=UTC))
    assert winter == 0.0
    assert summer == 1.0


def test_a_same_city_shift_is_zero_in_every_season() -> None:
    denver = city_for("Denver", "CO")
    for month in (1, 4, 7, 10):
        assert timezone_shift_hours(denver, denver, datetime(2024, month, 15, tzinfo=UTC)) == 0.0


def test_a_naive_datetime_is_refused() -> None:
    """The same rule every date in this project follows: a naive datetime takes its offset from
    whatever zone the process happens to run in, which makes the answer depend on the machine."""
    with pytest.raises(VenueError, match="must be timezone-aware"):
        timezone_shift_hours(
            city_for("Denver", "CO"), city_for("Boston", "MA"), datetime(2024, 1, 15)  # noqa: DTZ001
        )


def test_a_transatlantic_shift_is_large_and_signed_correctly() -> None:
    at = datetime(2024, 1, 15, tzinfo=UTC)
    assert timezone_shift_hours(city_for("New York", "NY"), city_for("Paris", None), at) == 6.0
    assert timezone_shift_hours(city_for("Paris", None), city_for("New York", "NY"), at) == -6.0


# ── module hygiene ────────────────────────────────────────────────────────────


def test_the_module_imports_nothing_outside_the_standard_library() -> None:
    """D-021/D-016 -- consumed by `features`, which must never drag in training dependencies."""
    import ast
    from pathlib import Path

    from model import venues as venues_module

    tree = ast.parse(Path(venues_module.__file__).read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert not (roots & {"numpy", "pandas", "scipy", "sklearn", "sqlalchemy", "geopy"}), roots


def test_city_records_are_immutable() -> None:
    """The table is shared module state. A caller that could edit an elevation would change it for
    every later lookup in the process."""
    denver = city_for("Denver", "CO")
    with pytest.raises((AttributeError, TypeError)):
        denver.elevation_feet = 0.0  # type: ignore[misc]


def test_haversine_is_finite_at_the_antipodes_and_the_poles() -> None:
    """`asin(sqrt(a))` with a floating-point `a` marginally over 1.0 raises `ValueError`. The corpus
    contains no such pair, but the formula should not be one rounding error from an exception."""
    north = City("N", None, 90.0, 0.0, 0.0, "UTC")
    south = City("S", None, -90.0, 0.0, 0.0, "UTC")
    d = distance_miles(north, south)
    assert math.isfinite(d)
    assert d == pytest.approx(math.pi * 3958.7613, rel=1e-6)
