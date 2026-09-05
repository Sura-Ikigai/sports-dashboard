"""Venue geography: travel distance, altitude and timezone shift (T-026, D-036).

The corpus already carries `venue_id`, `venue_address_city` and `venue_address_state` on every
schedule row, and `corpus_venues` stores them. What it does not carry is *where those cities are* --
so this module is the static table that supplies coordinates, elevation and timezone, and the
arithmetic over them.

D-036 is honest about the expected value: **roughly +.002 AUC**. Travel and altitude are included
because they are nearly free given D-034's work, not because they are expected to matter on their
own. That is the standard to judge them against when T-030 reports.

## A missing city raises. It never defaults.

The single most important line in this module is that `city_for` has no fallback. A silent zero for
an unknown venue reads as **"no travel"** -- the strongest possible signal for a home stand -- and it
would be produced precisely for the games this feature was added to describe: the London, Paris,
Berlin and Mexico City games, which are the longest trips in the corpus. A missing city is a data
problem to fix, never a value to invent.

## Keyed by city, not by venue

D-036 says "a static city table", and that is the right grain. Arenas are renamed constantly (the
corpus holds `crypto.com Arena`, `Rocket Arena`, `Mortgage Matchup Center` -- all recent renames of
existing buildings) and a team may move between arenas in the same city without moving at all. The
venue -> city mapping already lives in the database; this module maps city -> geography, so a new
arena in a known city needs no change here.

Both halves of the key matter: `(city, state)`. "Portland" is Oregon in this corpus and Maine
elsewhere, and the international venues carry no state at all.

## Standard library only

D-021/D-016. `math` for the haversine, `zoneinfo` for offsets. No numpy, no third-party geocoding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

# Mean Earth radius. The haversine is a great-circle distance on a sphere, which is accurate to
# about 0.5% against the true ellipsoid -- far tighter than this feature needs, and it has no
# dependencies.
EARTH_RADIUS_MILES = 3958.7613

# A game is "high altitude" above this. The threshold sits in a wide empty gap in the observed data
# rather than at a round number chosen for its own sake: the corpus's high-elevation cities are
# Mexico City (7,350 ft), Denver (5,280) and Salt Lake City (4,226), and the next one down is Las
# Vegas at 2,001. Nothing sits between 2,001 and 4,226, so any threshold in that band separates the
# two populations identically and none of them is near an edge.
#
# This is the same reasoning `corpus.MIN_SEASON_GAMES_FOR_A_REAL_TEAM` uses, and it carries the same
# warning: if this ever needs adjusting to make something pass, check which population changed
# rather than moving the number.
HIGH_ALTITUDE_FEET = 3000.0


class VenueError(KeyError):
    """Raised when a city is not in the table. Never swallowed, never defaulted."""


@dataclass(frozen=True, slots=True)
class City:
    """One city's geography. `state` is None for the international venues."""

    name: str
    state: str | None
    latitude: float
    longitude: float
    elevation_feet: float
    timezone: str

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.name, self.state)

    @property
    def is_high_altitude(self) -> bool:
        return self.elevation_feet >= HIGH_ALTITUDE_FEET


def _c(name: str, state: str | None, lat: float, lon: float, elev: float, tz: str) -> City:
    return City(name=name, state=state, latitude=lat, longitude=lon, elevation_feet=elev,
                timezone=tz)


# Every city hosting a game in the corpus as of 2026-09-04 -- 37 of them across 45 venues (arenas
# outnumber cities because Milwaukee, Detroit, Sacramento, San Antonio, Mexico City, London and
# Paris each appear with more than one building).
#
# Coordinates are the arena's, elevation is the city's in feet, timezone is IANA. Phoenix is
# `America/Phoenix` deliberately: Arizona does not observe DST, so its shift against every other US
# city changes twice a season, and a fixed offset would be wrong for half of it.
_CITY_LIST: tuple[City, ...] = (
    _c("Atlanta", "GA", 33.7573, -84.3963, 1050.0, "America/New_York"),
    _c("Austin", "TX", 30.2849, -97.7341, 489.0, "America/Chicago"),
    _c("Berlin", None, 52.5064, 13.4432, 112.0, "Europe/Berlin"),
    _c("Boston", "MA", 42.3662, -71.0621, 20.0, "America/New_York"),
    _c("Brooklyn", "NY", 40.6826, -73.9754, 30.0, "America/New_York"),
    _c("Charlotte", "NC", 35.2251, -80.8392, 751.0, "America/New_York"),
    _c("Chicago", "IL", 41.8807, -87.6742, 594.0, "America/Chicago"),
    _c("Cleveland", "OH", 41.4965, -81.6882, 653.0, "America/New_York"),
    _c("Dallas", "TX", 32.7905, -96.8103, 430.0, "America/Chicago"),
    _c("Denver", "CO", 39.7487, -105.0077, 5280.0, "America/Denver"),
    _c("Detroit", "MI", 42.3410, -83.0552, 600.0, "America/New_York"),
    _c("Houston", "TX", 29.7508, -95.3621, 43.0, "America/Chicago"),
    _c("Indianapolis", "IN", 39.7640, -86.1555, 715.0, "America/Indiana/Indianapolis"),
    _c("Inglewood", "CA", 33.9450, -118.3417, 118.0, "America/Los_Angeles"),
    _c("Las Vegas", "NV", 36.1028, -115.1783, 2001.0, "America/Los_Angeles"),
    _c("London", None, 51.5030, 0.0032, 20.0, "Europe/London"),
    _c("Los Angeles", "CA", 34.0430, -118.2673, 289.0, "America/Los_Angeles"),
    _c("Memphis", "TN", 35.1382, -90.0505, 260.0, "America/Chicago"),
    _c("Mexico City", None, 19.4326, -99.1332, 7350.0, "America/Mexico_City"),
    _c("Miami", "FL", 25.7814, -80.1870, 7.0, "America/New_York"),
    _c("Milwaukee", "WI", 43.0451, -87.9172, 617.0, "America/Chicago"),
    _c("Minneapolis", "MN", 44.9795, -93.2760, 830.0, "America/Chicago"),
    _c("New Orleans", "LA", 29.9490, -90.0821, 7.0, "America/Chicago"),
    _c("New York", "NY", 40.7505, -73.9934, 43.0, "America/New_York"),
    _c("Oakland", "CA", 37.7503, -122.2030, 43.0, "America/Los_Angeles"),
    _c("Oklahoma City", "OK", 35.4634, -97.5151, 1201.0, "America/Chicago"),
    _c("Orlando", "FL", 28.5392, -81.3839, 82.0, "America/New_York"),
    _c("Paris", None, 48.8386, 2.3783, 115.0, "Europe/Paris"),
    _c("Philadelphia", "PA", 39.9012, -75.1720, 39.0, "America/New_York"),
    _c("Phoenix", "AZ", 33.4457, -112.0712, 1086.0, "America/Phoenix"),
    _c("Portland", "OR", 45.5316, -122.6668, 50.0, "America/Los_Angeles"),
    _c("Sacramento", "CA", 38.5802, -121.4997, 30.0, "America/Los_Angeles"),
    _c("Salt Lake City", "UT", 40.7683, -111.9011, 4226.0, "America/Denver"),
    _c("San Antonio", "TX", 29.4269, -98.4375, 650.0, "America/Chicago"),
    _c("San Francisco", "CA", 37.7680, -122.3878, 10.0, "America/Los_Angeles"),
    _c("Toronto", "ON", 43.6435, -79.3791, 249.0, "America/Toronto"),
    _c("Washington", "DC", 38.8981, -77.0209, 25.0, "America/New_York"),
)

CITIES: dict[tuple[str, str | None], City] = {city.key: city for city in _CITY_LIST}


def _normalize(value: str | None) -> str | None:
    """Trim and collapse, but do not case-fold the stored key -- lookups fold instead.

    The corpus writes city names as ESPN publishes them and they have been stable, so normalization
    here is defensive rather than load-bearing. What it must NOT do is map an unrecognized name onto
    a recognized one; that would be the silent default this module exists to refuse.
    """
    if value is None:
        return None
    trimmed = " ".join(value.split())
    return trimmed or None


_LOOKUP: dict[tuple[str, str | None], City] = {
    (city.name.casefold(), city.state.casefold() if city.state else None): city
    for city in _CITY_LIST
}


def city_for(name: str, state: str | None = None) -> City:
    """The city's geography, or `VenueError`.

    **There is no default and there must never be one.** A zero distance for an unknown venue reads
    as "no travel" -- the strongest possible signal for a home stand -- and it would be produced for
    exactly the games this feature exists to describe. See the module docstring.
    """
    key = (_normalize(name), _normalize(state))
    if key[0] is None:
        raise VenueError("a blank city name cannot be looked up -- the corpus row is incomplete")
    folded = (key[0].casefold(), key[1].casefold() if key[1] else None)
    if folded not in _LOOKUP:
        raise VenueError(
            f"city {name!r} (state {state!r}) is not in the venue table. Add it with coordinates, "
            "elevation and an IANA timezone -- do not let it default. A missing city returns zero "
            "travel, which reads as a home stand for what is probably the longest trip in the "
            f"corpus. Known cities: {len(_LOOKUP)}."
        )
    return _LOOKUP[folded]


def distance_miles(origin: City, destination: City) -> float:
    """Great-circle distance in miles.

    Haversine on a sphere: accurate to roughly 0.5% against the true ellipsoid, which is far tighter
    than a feature worth +.002 AUC requires, and it needs nothing outside `math`.
    """
    lat1, lon1 = math.radians(origin.latitude), math.radians(origin.longitude)
    lat2, lon2 = math.radians(destination.latitude), math.radians(destination.longitude)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def travel_miles(origin_city: str, origin_state: str | None,
                 destination_city: str, destination_state: str | None) -> float:
    """Distance between two cities named as the corpus names them. Raises on either being unknown."""
    return distance_miles(
        city_for(origin_city, origin_state), city_for(destination_city, destination_state)
    )


def altitude_feet(name: str, state: str | None = None) -> float:
    return city_for(name, state).elevation_feet


def is_high_altitude(name: str, state: str | None = None) -> bool:
    """User story 9: Denver and Salt Lake City are not ordinary road games."""
    return city_for(name, state).is_high_altitude


def timezone_shift_hours(origin: City, destination: City, at: datetime) -> float:
    """Hours of clock change moving from `origin` to `destination`, at a specific moment.

    Positive means the destination's clock is ahead (travelling east); negative means behind. The
    moment matters and is required rather than defaulted: Arizona does not observe DST, so the
    Phoenix-to-Denver shift is zero in summer and one hour in winter, and an NBA season spans both.

    Requires a timezone-aware `at`, for the same reason every other date in this project does -- a
    naive datetime silently assumes the machine's zone, which makes the answer depend on where the
    code runs.
    """
    if at.tzinfo is None or at.tzinfo.utcoffset(at) is None:
        raise VenueError(
            f"`at` must be timezone-aware, got {at!r} -- a naive datetime would take the offset "
            "from whatever zone this process happens to run in"
        )
    origin_offset = at.astimezone(ZoneInfo(origin.timezone)).utcoffset()
    destination_offset = at.astimezone(ZoneInfo(destination.timezone)).utcoffset()
    assert origin_offset is not None and destination_offset is not None  # noqa: S101
    return (destination_offset - origin_offset).total_seconds() / 3600.0


def assert_covers(city_states: object) -> None:
    """Raise unless every `(city, state)` pair given is in the table.

    For a caller that has just read the corpus and wants to fail at the boundary rather than
    mid-feature-computation: `venues.assert_covers(store.load_venues(conn).values())` shaped input,
    or any iterable of `(city, state)` pairs.
    """
    missing = []
    for entry in city_states:  # type: ignore[union-attr]
        name, state = entry
        try:
            city_for(name, state)
        except VenueError:
            missing.append((name, state))
    if missing:
        raise VenueError(
            f"{len(missing)} city/state pair(s) in the corpus are not in the venue table: "
            f"{sorted(missing)}. Add them with coordinates, elevation and an IANA timezone."
        )
