"""Lagged rotation availability (T-027, D-035).

The only genuinely new *factor* in this cycle. Elo, travel and the rest are better rulers for things
the model already measured; this is the one that measures something else. The plan's problem
statement is that `point_diff_diff`, Elo and `form_diff` correlate at **.87–.92** -- three
measurements of one latent variable -- and that moving AUC needs a second factor rather than a
better ruler.

Measured on the 2024+2025 dev seasons (2026 untouched, T-030): **availability correlates with
`elo_diff` at +.255**. That is the number this module exists to produce.

## Structurally leakage-free, not procedurally

D-035: availability is derived **strictly from games completed before the as-of moment**. Player box
scores record who actually played, which is post-game information; using them for the game being
predicted is textbook leakage, and "played 0 minutes" additionally correlates with blowouts, so it
would partly encode the outcome.

T-027's security note says this feature is *one line of code away from leakage*. Two structural
choices close that line rather than watching it:

1. **This module never queries anything.** It receives a sequence of `Appearance` records and reads
   nothing else. There is no database handle here to point at the wrong game. Data arrives through
   the Context and the as-of filter, per the security note; a test asserts the module imports no
   database machinery.
2. **`as_of` is an optional tripwire, not a filter.** Pass it and the module *refuses* any
   appearance dated at or after that moment. It does not silently drop them -- dropping would make a
   mis-filtered caller work by accident, which is exactly how the control migrates out of
   `features.py` where T-006's property test proves it.

## What it catches, and what it structurally cannot

It catches **multi-game absences**, which is most star injuries. It **cannot** catch a game-day
scratch -- a player ruled out an hour before tip-off appears available here, because the only
evidence of his absence is the box score of the game being predicted.

That limitation is a consequence of choosing a construction whose leakage-freedom is structural, and
D-035 requires it be reported rather than hidden. It is the honest cost of the guarantee above.

## The construction

For a team, going into a game:

1. **Rotation** -- over the `rotation_games` most recent prior games, total each player's minutes and
   take the top `rotation_size`. Each player's weight is his mean minutes per game across that
   window, so a 34-minute starter outweighs a 12-minute reserve by roughly three to one.
2. **Participation** -- over the `window_games` most recent prior games, compute the share of that
   rotation weight that was *present*. Present means "not a DNP", never "played minutes".
3. **Shrinkage** -- blend toward the prior with weight `n/(n+k)`, so a team with two games of history
   is not treated as confidently as one with five. Mirrors D-015's shape.

## Why "present", not "played minutes"

User story 12, and the distinction the whole T-021 schema was built around. In the real corpus
**31,769** rows are `did_not_play` with null minutes (absence) while **700** are active with
`minutes == 0` (dressed, played nothing). A rotation player logging zero minutes in a blowout is
*available* -- counting that as absence is garbage time masquerading as an injury, and it would make
the feature partly a blowout detector, which is the leakage D-035 warns about arriving by the back
door.

## Standard library only

D-021/D-016. Consumed by `features` (T-028), which must never drag training dependencies into the
served image.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

# Measured against the ingested corpus on 2026-09-04 over 13,120 team-games:
#   mean .8764  median .8982  p5 .6988  p25 .8371  p75 .9466  p95 .9896
# The prior is that mean. A team with no history is neither fully healthy nor notably injured; it is
# average, and average is .8764 rather than 1.0. Using 1.0 would make every season opener read as
# the healthiest game of the year.
DEFAULT_PRIOR = 0.8764

# Rotation of nine: the corpus averages 13.08 players listed, 10.69 playing and 9.04 playing at
# least ten minutes per team-game. Nine is the shape of the thing being measured, not a round
# number -- past it the weights are small enough that an absence is noise.
DEFAULT_ROTATION_SIZE = 9
DEFAULT_ROTATION_GAMES = 15
DEFAULT_WINDOW_GAMES = 5

# Shrinkage constant. With a five-game window this puts 5/6 of the weight on what was observed --
# enough to stop a two-game history speaking with full confidence, not so much that a real absence
# is flattened.
DEFAULT_SHRINK_K = 1.0


class AvailabilityError(ValueError):
    """Raised when availability cannot be computed as stated -- including, deliberately, when the
    input contains an appearance the as-of moment forbids."""


@dataclass(frozen=True, slots=True)
class Appearance:
    """One player's presence (or absence) in one completed game.

    Deliberately a local record rather than `store.PlayerGame`: importing `store` would drag
    SQLAlchemy into a module that must stay standard-library-only, and would give this module a
    database handle it has no business holding. T-028's Context does the conversion.

    `minutes` and `did_not_play` are separate fields and must stay that way -- see the module
    docstring on why a zero-minute row is not an absence.
    """

    game_id: str
    date: datetime
    player_id: str
    minutes: float | None
    did_not_play: bool

    def __post_init__(self) -> None:
        if self.date.tzinfo is None or self.date.tzinfo.utcoffset(self.date) is None:
            raise AvailabilityError(
                f"appearance {self.game_id!r}/{self.player_id!r} has a naive date -- every date in "
                "this pipeline is timezone-aware, and a naive one silently takes the machine's zone"
            )
        if self.minutes is not None and self.minutes < 0:
            raise AvailabilityError(
                f"appearance {self.game_id!r}/{self.player_id!r} has negative minutes "
                f"({self.minutes})"
            )


@dataclass(frozen=True, slots=True)
class AvailabilityConfig:
    """Every knob, defaulted to values measured against the corpus rather than chosen."""

    rotation_games: int = DEFAULT_ROTATION_GAMES
    rotation_size: int = DEFAULT_ROTATION_SIZE
    window_games: int = DEFAULT_WINDOW_GAMES
    prior: float = DEFAULT_PRIOR
    shrink_k: float = DEFAULT_SHRINK_K

    def __post_init__(self) -> None:
        for name in ("rotation_games", "rotation_size", "window_games"):
            if getattr(self, name) < 1:
                raise AvailabilityError(f"{name} must be at least 1, got {getattr(self, name)}")
        if not 0.0 <= self.prior <= 1.0:
            raise AvailabilityError(
                f"prior must be a share in [0, 1], got {self.prior} -- availability is a fraction "
                "of rotation weight present, so a prior outside that range is not a value the "
                "feature can take"
            )
        if self.shrink_k < 0:
            raise AvailabilityError(f"shrink_k must be non-negative, got {self.shrink_k}")

    @property
    def lookback_games(self) -> int:
        """How many of a team's most recent games `team_availability` can actually read.

        Exists so a caller slicing a long history down to what this module will use does not have to
        know *which* of the two windows is the wider one -- that is this module's business, and a
        caller that hard-coded 15 would silently truncate the rotation the day `rotation_games`
        moved. `features` (T-028) slices with this.
        """
        return max(self.rotation_games, self.window_games)


DEFAULT_CONFIG = AvailabilityConfig()


def _shrink(observed: float, n: int, prior: float, k: float) -> float:
    """Blend toward `prior` with weight `n/(n+k)`. Mirrors D-015's shape.

    Reimplemented rather than imported from `features` because `features` imports *this* module;
    it is three lines, and a circular import to save them would be a poor trade.
    """
    if n <= 0:
        return prior
    weight = n / (n + k)
    return weight * observed + (1 - weight) * prior


def _by_game(appearances: Sequence[Appearance]) -> list[tuple[datetime, str, list[Appearance]]]:
    """Group into games, ordered by `(date, game_id)`.

    Ordered by date rather than by the caller's sequence, for the reason T-007 established and
    `elo` repeats: dates are the fact, a caller's ordering is a claim. Ties are broken by `game_id`
    because simultaneous tip-offs are real.
    """
    grouped: dict[tuple[datetime, str], list[Appearance]] = defaultdict(list)
    for appearance in appearances:
        grouped[(appearance.date, appearance.game_id)].append(appearance)
    return [(date, game_id, rows) for (date, game_id), rows in sorted(grouped.items())]


def team_availability(
    appearances: Sequence[Appearance],
    *,
    as_of: datetime | None = None,
    config: AvailabilityConfig = DEFAULT_CONFIG,
) -> float:
    """The share of one team's rotation weight that has been present in its recent games.

    Args:
        appearances: **this team's** player rows, from games completed **before** the prediction
            moment. Filtering is the caller's job (`features`' as-of filter, where T-006's property
            test proves it); this module never queries and never filters silently.
        as_of: optional tripwire. When given, an appearance dated at or after it is a hard error
            rather than a dropped row -- see the module docstring on why dropping would be worse.
        config: the knobs, defaulted to measured values.

    Returns:
        A share in [0, 1]. With no usable history this is exactly `config.prior`, which is the
        corpus mean and not 1.0 -- a team with no record is average, not perfectly healthy.
    """
    if not isinstance(appearances, Sequence) or isinstance(appearances, (str, bytes)):
        raise AvailabilityError(
            f"appearances must be a Sequence, got {type(appearances).__name__}. A one-shot iterator "
            "is refused: it would be consumed here and yield the prior on every later call, which "
            "is indistinguishable from a team with no history (F-045's shape)."
        )

    if as_of is not None:
        if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
            raise AvailabilityError("as_of must be timezone-aware")
        leaked = [a for a in appearances if a.date >= as_of]
        if leaked:
            raise AvailabilityError(
                f"{len(leaked)} appearance(s) are dated at or after as_of "
                f"{as_of.isoformat()} (first: game {leaked[0].game_id!r} at "
                f"{leaked[0].date.isoformat()}). Availability must be computed strictly from games "
                "completed before the prediction moment (D-035). These rows are refused rather "
                "than dropped: dropping would let a mis-filtered caller work by accident, which is "
                "how the as-of control migrates out of `features` where it is actually proven."
            )

    games = _by_game(appearances)
    if not games:
        return config.prior

    # --- rotation: who plays, and how much, over the recent past -------------------------------
    rotation_window = games[-config.rotation_games :]
    totals: dict[str, float] = defaultdict(float)
    for _date, _game_id, rows in rotation_window:
        for row in rows:
            # A DNP contributes nothing to the rotation definition but the game still counts in the
            # denominator below, so an injured starter's weight decays rather than vanishing.
            if not row.did_not_play and row.minutes:
                totals[row.player_id] += row.minutes
    if not totals:
        return config.prior

    top = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[: config.rotation_size]
    weights = {player_id: total / len(rotation_window) for player_id, total in top}
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return config.prior

    # --- participation: how much of that weight has actually been present ----------------------
    shares: list[float] = []
    for _date, _game_id, rows in games[-config.window_games :]:
        present = {row.player_id for row in rows if not row.did_not_play}
        shares.append(
            sum(weight for player_id, weight in weights.items() if player_id in present)
            / total_weight
        )

    observed = sum(shares) / len(shares)
    return _shrink(observed, len(shares), config.prior, config.shrink_k)


def availability_difference(
    home_appearances: Sequence[Appearance],
    away_appearances: Sequence[Appearance],
    *,
    as_of: datetime | None = None,
    config: AvailabilityConfig = DEFAULT_CONFIG,
) -> float:
    """`avail_diff` -- the home team's availability minus the away team's.

    Signed toward the home team, matching every other difference feature in this model. Range
    [-1, 1]; on the dev seasons the standard deviation is about .108, and the feature correlates
    with `elo_diff` at only +.255 -- which is what makes it a second factor rather than another
    ruler for team strength.
    """
    return team_availability(home_appearances, as_of=as_of, config=config) - team_availability(
        away_appearances, as_of=as_of, config=config
    )


# --- as-of addressable participation (T-028) ------------------------------------------------------


class AppearanceIndex:
    """One team's availability at **any** moment, without rescanning its whole history per query.

    `team_availability` is the definition; this is the index that makes calling it 6,600 times
    affordable. A training pass asks for one number per team per game, and the naive form hands the
    function every appearance the team has ever made -- ~6,000 records for a late-season game, of
    which it reads the last fifteen games' worth. Over a training set that is ~100M records grouped
    and sorted to produce numbers that depend on 0.4% of them.

    ## The slice is bounded by the module's own answer, not by a number `features` picked

    `AvailabilityConfig.lookback_games` says how far back `team_availability` can see, and this
    index hands it one game more than that. The extra game is not slack: `team_availability` slices
    its own tail (`games[-rotation_games:]`), so being given a slightly longer history is exactly
    equivalent to being given the whole one, and the spare slot is what keeps that true when the
    excluded-game path below removes a row from the middle. A test pins the equivalence against the
    unsliced history rather than trusting the argument.

    ## It cannot answer without an as-of moment

    Same rule as `elo.Timeline`. There is no "current availability" method, and the `as_of` a caller
    passes is handed straight back to `team_availability`, whose tripwire then re-checks the slice.
    That check is redundant if this index is right -- which is the point of leaving it in: it is what
    would catch this index being wrong.
    """

    __slots__ = ("_config", "_game_dates", "_game_starts", "_rows")

    def __init__(
        self,
        by_team: Mapping[str, Sequence[Appearance]],
        *,
        config: AvailabilityConfig = DEFAULT_CONFIG,
    ) -> None:
        if not isinstance(by_team, Mapping):
            raise AvailabilityError(
                f"by_team must be a Mapping of team id -> that team's appearances, got "
                f"{type(by_team).__name__}"
            )
        self._config = config
        self._rows: dict[str, tuple[Appearance, ...]] = {}
        self._game_dates: dict[str, tuple[datetime, ...]] = {}
        self._game_starts: dict[str, tuple[int, ...]] = {}

        for team_id, appearances in by_team.items():
            if not isinstance(appearances, Sequence) or isinstance(appearances, (str, bytes)):
                raise AvailabilityError(
                    f"appearances for team {team_id!r} must be a Sequence, got "
                    f"{type(appearances).__name__} -- a one-shot iterator would be consumed here "
                    "and leave the team looking like it had never played (F-045)."
                )
            # Checked before the sort, not after: sorting on `.date` raises a bare AttributeError
            # from inside a lambda, which is the unhelpful failure F-048 made this codebase check
            # shapes rather than accept whatever happens to work.
            for row in appearances:
                if not isinstance(row, Appearance):
                    raise AvailabilityError(
                        f"appearances for team {team_id!r} must contain Appearance records, got "
                        f"{type(row).__name__}"
                    )
            rows = sorted(appearances, key=lambda a: (a.date, a.game_id))
            starts: list[int] = []
            dates: list[datetime] = []
            previous: tuple[datetime, str] | None = None
            for position, row in enumerate(rows):
                key = (row.date, row.game_id)
                if key != previous:
                    starts.append(position)
                    dates.append(row.date)
                    previous = key
            self._rows[team_id] = tuple(rows)
            self._game_dates[team_id] = tuple(dates)
            self._game_starts[team_id] = (*starts, len(rows))

    def teams(self) -> frozenset[str]:
        return frozenset(self._rows)

    def availability_before(
        self, team_id: str, as_of: datetime, *, exclude_game_id: str | None = None
    ) -> float:
        """That team's availability going into a game at `as_of`.

        A team this index has never seen returns `config.prior`, exactly as a team with no history
        does -- and that is the one case a caller must not reach by accident, which is why
        `features.Context` refuses to be built with participation it cannot vouch for rather than
        letting an empty index answer quietly.
        """
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise AvailabilityError("as_of must be a timezone-aware datetime")
        rows = self._rows.get(team_id)
        if not rows:
            return self._config.prior

        dates = self._game_dates[team_id]
        starts = self._game_starts[team_id]
        cut = bisect_left(dates, as_of)
        first = max(0, cut - (self._config.lookback_games + 1))
        window = list(rows[starts[first] : starts[cut]])
        if exclude_game_id is not None:
            window = [row for row in window if row.game_id != exclude_game_id]
        return team_availability(window, as_of=as_of, config=self._config)

    def difference_before(
        self,
        home_id: str,
        away_id: str,
        as_of: datetime,
        *,
        exclude_game_id: str | None = None,
    ) -> float:
        """`avail_diff` -- home availability minus away, both as of the same moment."""
        return self.availability_before(
            home_id, as_of, exclude_game_id=exclude_game_id
        ) - self.availability_before(away_id, as_of, exclude_game_id=exclude_game_id)
