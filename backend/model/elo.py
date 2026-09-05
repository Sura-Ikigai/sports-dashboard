"""Margin-of-victory Elo (T-025, D-032).

Replaces season-to-date point differential as the team-strength feature. MOV Elo *is* an
opponent-adjusted point differential -- the same margin information with strength of schedule folded
in -- which is why D-032 replaces rather than adds: the two correlate at .9187, and keeping both
buys roughly +.002 AUC at the cost of the coefficient interpretability user story 30 depends on.

Measured on the 2024+2025 dev seasons (2,640 games, 2026 deliberately untouched): **AUC .7149**
against `point_diff_diff`'s .7073. Plain win/loss Elo scores .7093 and K=40 scores .7086 -- margin
is what earns the gain, and a high K over-reacts.

## Ratings are replayed, never persisted

Replaying ~12,000 games is milliseconds. Persisting ratings would create state that can drift from
the games it claims to summarize, and an incremental-update path that can be got wrong; neither
exists here. Every call recomputes from the game sequence, so the ratings are a pure function of the
corpus and nothing else.

## What this module emits, and the one thing it deliberately does not

`pregame_rating_differences` returns **`home_rating - away_rating`, without the home adjustment.**

The home adjustment is update mechanics: it belongs in the expected-score calculation, where it
stops home wins from inflating ratings. It must not reach the feature. Adding a constant 100 to
every game's `elo_diff` produces a column that differs from the unadjusted one by a constant, which
is **perfectly collinear with the intercept** -- exactly the non-identifiability D-024 documented
for `home_advantage` and D-033 removed the feature over. Home-court advantage is real and it lives
in the intercept.

## Standard library only

D-021/D-016. No numpy, no pandas. This module is consumed by `features` (T-028), which is itself
standard-library-only so that importing the model package never drags in training dependencies.

## The 2019 -> 2022 gap

The corpus has no 2020 or 2021 season (`loader.SEASONS` never pinned them), so replaying warm-up
plus modeling seasons crosses a three-year hole. By default carryover is applied **once per observed
season transition** -- the textbook behaviour, and the one D-032's 0.75 was measured under. Set
`regress_per_elapsed_year=True` to instead regress once per calendar year of the gap, on the view
that three unobserved seasons decay a rating more than one does.

This is a real modelling choice with no measurement behind it either way, and it is left explicit
rather than buried: by 2022 -- the first training season -- the warm-up has done its job under
either setting, and fold 1 trains on 2022 onward.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from .records import Game

# The measured defaults (D-032). Changing any of these changes what the model was calibrated on.
DEFAULT_K = 20.0
DEFAULT_HOME_ADVANTAGE = 100.0
DEFAULT_CARRYOVER = 0.75
DEFAULT_INITIAL_RATING = 1500.0


class EloError(ValueError):
    """Raised when a game sequence cannot be replayed as stated."""


@dataclass(frozen=True, slots=True)
class EloConfig:
    """Every knob, defaulted to D-032's measured values.

    Configurable because T-030 may re-measure them, and frozen because a rating replay whose
    parameters could change midway is not a replay.
    """

    k: float = DEFAULT_K
    home_advantage: float = DEFAULT_HOME_ADVANTAGE
    carryover: float = DEFAULT_CARRYOVER
    initial_rating: float = DEFAULT_INITIAL_RATING

    # The 538 margin-of-victory multiplier: ((MOV + offset) ** exponent) / (base + slope * diff),
    # where `diff` is the winner's pre-game rating advantage including the home adjustment. The
    # denominator is the autocorrelation correction -- without it a strong favourite winning big
    # gains far too much, and ratings inflate away from the mean over a season.
    mov_offset: float = 3.0
    mov_exponent: float = 0.8
    mov_denominator_base: float = 7.5
    mov_denominator_slope: float = 0.006

    regress_per_elapsed_year: bool = False

    def __post_init__(self) -> None:
        if self.k <= 0:
            raise EloError(f"k must be positive, got {self.k} -- a non-positive K freezes or "
                           "inverts every update")
        if not 0.0 <= self.carryover <= 1.0:
            raise EloError(
                f"carryover must be in [0, 1], got {self.carryover}. 1.0 carries a rating forward "
                "intact; 0.0 resets every team to the mean each season. Outside that range a "
                "season boundary would push ratings away from the mean rather than toward it."
            )
        if self.mov_denominator_base <= 0:
            raise EloError(
                f"mov_denominator_base must be positive, got {self.mov_denominator_base}"
            )


DEFAULT_CONFIG = EloConfig()

# The MOV denominator is `base + slope * winner_advantage`, which reaches zero at an advantage of
# -1250 under the defaults -- a rating gap no NBA corpus contains, but a division by zero if one
# ever did, and a *sign flip* just past it, which would move the winner's rating down. Clamped
# rather than left to chance: a floor is a bounded approximation, an inverted update is a silent
# corruption of every rating downstream of it.
_MIN_MOV_DENOMINATOR = 1e-3


@dataclass(frozen=True, slots=True)
class EloReplay:
    """The full result of a replay.

    `pregame_difference` is what `features` (T-028) consumes; the rest exists so the invariants can
    be asserted against the thing itself rather than against a re-derivation of it.
    """

    pregame_difference: dict[str, float] = field(default_factory=dict)
    pregame_ratings: dict[str, tuple[float, float]] = field(default_factory=dict)
    final_ratings: dict[str, float] = field(default_factory=dict)
    seasons_replayed: tuple[int, ...] = ()

    def rating(self, team_id: str, *, default: float | None = None) -> float:
        if team_id in self.final_ratings:
            return self.final_ratings[team_id]
        if default is not None:
            return default
        raise EloError(f"team {team_id!r} does not appear in the replayed games")


def _expected_home_score(home: float, away: float, home_advantage: float) -> float:
    """Standard logistic expectation on a 400-point scale, with the home side adjusted upward."""
    return 1.0 / (1.0 + 10.0 ** ((away - (home + home_advantage)) / 400.0))


def _mov_multiplier(margin: int, winner_advantage: float, config: EloConfig) -> float:
    """538's margin-of-victory multiplier.

    `winner_advantage` is the winner's pre-game rating minus the loser's, *including* the home
    adjustment on whichever side was at home. That term is the autocorrelation correction: a
    favourite winning big is expected, so its gain is damped, while an underdog winning big is
    surprising and its gain is not.
    """
    numerator = (abs(margin) + config.mov_offset) ** config.mov_exponent
    denominator = config.mov_denominator_base + config.mov_denominator_slope * winner_advantage
    return numerator / max(denominator, _MIN_MOV_DENOMINATOR)


def _regress_to_mean(
    ratings: dict[str, float], *, carryover: float, mean: float, times: int = 1
) -> None:
    """Move every rating toward `mean`, in place, `times` times.

    Total rating is preserved when the ratings already average `mean`, which they do: every team
    starts there and every game update is zero-sum.
    """
    for _ in range(times):
        for team in ratings:
            ratings[team] = mean + carryover * (ratings[team] - mean)


def replay(games: Sequence[Game], *, config: EloConfig = DEFAULT_CONFIG) -> EloReplay:
    """Replay `games` in date order, returning each game's pre-game state and the final ratings.

    Games are sorted internally by `(date, game_id)` rather than trusted in the order given. That is
    the same reasoning T-007 used for its temporal assertion -- dates are the fact, and a caller's
    ordering is a claim. Sorting here also makes the replay a pure function of the *set* of games,
    which is what "replay is deterministic" has to mean to be worth asserting.

    Ties in tip-off time are real (the NBA runs simultaneous games), so `game_id` breaks them: an
    arbitrary but *stable* order, which keeps the result reproducible without pretending the two
    games have a true sequence.
    """
    if not isinstance(games, Sequence) or isinstance(games, (str, bytes)):
        # The F-045 shape: a generator would be consumed by the sort below and every later use of
        # the same object would silently replay nothing.
        raise EloError(
            f"games must be a Sequence of Game records (list/tuple), got {type(games).__name__}. "
            "A one-shot iterator is refused: it would be consumed here and yield an empty replay "
            "on any second use."
        )

    ordered = sorted(games, key=lambda g: (g.date, g.game_id))

    ratings: dict[str, float] = {}
    pregame_difference: dict[str, float] = {}
    pregame_ratings: dict[str, tuple[float, float]] = {}
    seasons: list[int] = []
    previous_season: int | None = None

    for game in ordered:
        if not isinstance(game, Game):
            raise EloError(f"games must contain Game records, got {type(game).__name__}")

        if previous_season is not None and game.season != previous_season:
            if game.season < previous_season:
                # Sorted by date, so this means a game's `season` label disagrees with its date --
                # the exact confusion T-007 refused to rely on labels for.
                raise EloError(
                    f"game {game.game_id!r} is labelled season {game.season} but sorts after a "
                    f"season {previous_season} game by date. Season labels must agree with dates, "
                    "or the carryover is applied at the wrong moments."
                )
            gap = game.season - previous_season if config.regress_per_elapsed_year else 1
            _regress_to_mean(
                ratings, carryover=config.carryover, mean=config.initial_rating, times=gap
            )
        if game.season != previous_season:
            seasons.append(game.season)
            previous_season = game.season

        home = ratings.setdefault(game.home_id, config.initial_rating)
        away = ratings.setdefault(game.away_id, config.initial_rating)

        # Emitted WITHOUT the home adjustment -- see the module docstring. Adding a constant to
        # every row makes the column collinear with the intercept (D-024).
        pregame_difference[game.game_id] = home - away
        pregame_ratings[game.game_id] = (home, away)

        expected_home = _expected_home_score(home, away, config.home_advantage)
        home_won = game.home_score > game.away_score
        actual_home = 1.0 if home_won else 0.0

        # The winner's advantage includes the home adjustment on whichever side was at home.
        if home_won:
            winner_advantage = (home + config.home_advantage) - away
        else:
            winner_advantage = away - (home + config.home_advantage)

        multiplier = _mov_multiplier(
            game.home_score - game.away_score, winner_advantage, config
        )
        delta = config.k * multiplier * (actual_home - expected_home)

        # Equal and opposite: this is what makes total rating conserved under every update.
        ratings[game.home_id] = home + delta
        ratings[game.away_id] = away - delta

    return EloReplay(
        pregame_difference=pregame_difference,
        pregame_ratings=pregame_ratings,
        final_ratings=dict(ratings),
        seasons_replayed=tuple(seasons),
    )


def pregame_rating_differences(
    games: Sequence[Game], *, config: EloConfig = DEFAULT_CONFIG
) -> dict[str, float]:
    """**The interface.** `game_id` -> the home team's pre-game rating advantage.

    Pass the whole corpus, warm-up seasons included (D-037): ratings are running state across every
    prior season, so a rating difference computed from a subset is not the same number. This is the
    same reason `store.load_history` narrows by team and never by season.

    The returned value excludes the home adjustment; see the module docstring for why that is a
    correctness requirement rather than a preference.
    """
    return replay(games, config=config).pregame_difference


def elo_diff_for(
    games: Sequence[Game], game_id: str, *, config: EloConfig = DEFAULT_CONFIG
) -> float:
    """One game's pre-game difference. A convenience over `pregame_rating_differences`, and
    deliberately not an incremental path -- it replays the whole sequence, like everything else."""
    differences = pregame_rating_differences(games, config=config)
    if game_id not in differences:
        raise EloError(f"game {game_id!r} is not in the replayed sequence")
    return differences[game_id]


def ratings_after(
    games: Sequence[Game], *, config: EloConfig = DEFAULT_CONFIG
) -> Mapping[str, float]:
    """Final ratings after replaying `games`. Diagnostics and tests; features use the differences."""
    return replay(games, config=config).final_ratings


# --- as-of addressable rating state (T-028) -------------------------------------------------------


def _regress_value(rating: float, *, carryover: float, mean: float, times: int) -> float:
    """`_regress_to_mean` for a single rating. Regression is per-team and independent, so applying it
    to the two teams a query asks about gives the same numbers as applying it to the whole dict."""
    for _ in range(times):
        rating = mean + carryover * (rating - mean)
    return rating


def _regression_counts(ordered: Sequence[Game], config: EloConfig) -> tuple[int, ...]:
    """`counts[i]` = carryover regressions `replay` has performed by the time it processes
    `ordered[i]`, that game's own season transition included."""
    counts: list[int] = []
    performed = 0
    previous_season: int | None = None
    for game in ordered:
        if previous_season is not None and game.season != previous_season:
            performed += (game.season - previous_season) if config.regress_per_elapsed_year else 1
        counts.append(performed)
        previous_season = game.season
    return tuple(counts)


class Timeline:
    """Pre-game rating state at **any** moment, not only at the moments games were played.

    `pregame_rating_differences` answers "what was the rating gap before game X" for games that are
    *in* the corpus. `features` (T-028) needs a different question: "what was the rating gap as of
    this prediction moment", where the moment is an `as_of` that may sit days before tip-off and the
    target may not be in the corpus at all. Reading the pre-game difference off the replay would
    answer the first question while being asked the second -- so a prediction made five days out
    would silently carry the ratings the teams *will* have at tip-off, which is leakage.

    ## Why this is a precomputed index and not a replay per query

    Replaying the corpus once per target is O(n) per call and O(n^2) over a training set: 6,600
    targets against ~12,000 games is ~80M rating updates, minutes of pure Python. So the replay runs
    **once**, at construction, and this class answers by binary search.

    That is a performance decision that must not become an integrity decision, and it is not one.
    `replay` processes games sorted by `(date, game_id)`, so "the games dated strictly before
    `as_of`" is exactly a **prefix** of that order, and the state after a prefix is what this index
    stores. Answering from the index is therefore *equal* to replaying the filtered prefix, not
    merely close to it -- `test_elo.py` asserts that equality over every game of a corpus-shaped
    replay, under both carryover settings, which is what makes the shortcut safe rather than
    plausible.

    ## What it deliberately cannot do

    There is no method that answers without an `as_of`. The as-of moment is not an option a caller
    can drop; every question this class answers is bounded by one.
    """

    __slots__ = (
        "_config",
        "_dates",
        "_index_by_id",
        "_ordered",
        "_regressions",
        "_seasons",
        "_team_after",
        "_team_dates",
        "_team_indices",
    )

    def __init__(self, games: Sequence[Game], *, config: EloConfig = DEFAULT_CONFIG) -> None:
        result = replay(games, config=config)  # validates the sequence and the season ordering
        ordered = tuple(sorted(games, key=lambda g: (g.date, g.game_id)))

        self._config = config
        self._ordered = ordered
        self._dates = tuple(game.date for game in ordered)
        self._seasons = tuple(game.season for game in ordered)
        self._index_by_id = {game.game_id: i for i, game in enumerate(ordered)}

        # How many carryover regressions had been performed by the time each game was processed.
        # Carryover is applied to the WHOLE ratings dict at a season transition, not to a team when
        # it next plays, so a team's stored rating is only as regressed as the moment it was stored;
        # catching it up to the query moment is a difference of these counts. Getting this wrong is
        # invisible on a season's opening night and wrong for every game after it, for every team
        # that has not played yet.
        self._regressions = _regression_counts(ordered, config)

        # Per team, the rating it held *after* each of its own games. A team's rating changes only in
        # its own games, so this is the whole of its trajectory.
        team_dates: dict[str, list[datetime]] = {}
        team_after: dict[str, list[float]] = {}
        team_indices: dict[str, list[int]] = {}
        for index, game in enumerate(ordered):
            # The equivalence this index rests on -- "the games dated strictly before `as_of`" is a
            # prefix of `(date, game_id)` order -- holds for the two teams of a query only if
            # neither of them plays twice at the same instant. Simultaneous tip-offs are ordinary
            # (the corpus is full of them) and harmless, because ratings are per team; the same
            # *team* twice at one instant is not, and it would make this index disagree with
            # `replay` by however much the earlier of the two moved the rating. Impossible in a real
            # schedule, so it is corrupt input, and refused rather than silently approximated.
            for team in (game.home_id, game.away_id):
                if team_dates.get(team) and team_dates[team][-1] == game.date:
                    raise EloError(
                        f"team {team!r} appears in two games at exactly {game.date.isoformat()} "
                        f"(the second is {game.game_id!r}) -- a team cannot play two games at one "
                        "instant, and an as-of query at that moment has no well-defined answer."
                    )
            home_before, away_before = result.pregame_ratings[game.game_id]
            delta = self._delta(game, home_before, away_before)
            for team, after in (
                (game.home_id, home_before + delta),
                (game.away_id, away_before - delta),
            ):
                team_dates.setdefault(team, []).append(game.date)
                team_after.setdefault(team, []).append(after)
                team_indices.setdefault(team, []).append(index)
        self._team_dates = {t: tuple(v) for t, v in team_dates.items()}
        self._team_after = {t: tuple(v) for t, v in team_after.items()}
        self._team_indices = {t: tuple(v) for t, v in team_indices.items()}

    def _delta(self, game: Game, home: float, away: float) -> float:
        """The rating change `replay` applied to this game. Recomputed rather than stored so there is
        exactly one definition of the update; `EloReplay` carries the pre-game state, which is what
        it exists to expose."""
        config = self._config
        expected_home = _expected_home_score(home, away, config.home_advantage)
        home_won = game.home_score > game.away_score
        if home_won:
            winner_advantage = (home + config.home_advantage) - away
        else:
            winner_advantage = away - (home + config.home_advantage)
        multiplier = _mov_multiplier(game.home_score - game.away_score, winner_advantage, config)
        return config.k * multiplier * ((1.0 if home_won else 0.0) - expected_home)

    def _rating_after_prefix(self, team_id: str, as_of: datetime) -> tuple[float, int]:
        """That team's rating after its last game before `as_of`, and how many regressions had been
        applied to the ratings by then.

        A team with no prior game sits at `initial_rating`, which is the carryover's fixed point
        (`mean == initial_rating`), so the count it reports is irrelevant -- catching it up any
        number of times leaves it exactly there. That is also what `replay` does: it `setdefault`s a
        new team *after* regressing, so a debutant never arrives pre-decayed.
        """
        dates = self._team_dates.get(team_id)
        if not dates:
            return self._config.initial_rating, 0
        cut = bisect_left(dates, as_of)
        if cut == 0:
            return self._config.initial_rating, 0
        return (
            self._team_after[team_id][cut - 1],
            self._regressions[self._team_indices[team_id][cut - 1]],
        )

    def difference_before(
        self,
        home_id: str,
        away_id: str,
        as_of: datetime,
        season: int,
        *,
        exclude_game_id: str | None = None,
    ) -> float:
        """`home - away` pre-game rating difference as of `as_of`, **without** the home adjustment.

        Args:
            home_id, away_id: the matchup.
            as_of: the prediction moment. Games dated at or after it are excluded -- strictly, so a
                game at exactly this instant (the training case, where `as_of` is the target's own
                tip-off) cannot enter its own rating.
            season: the season the *predicted* game belongs to. Carryover is applied when it differs
                from the season of the last game before `as_of`, which is exactly what `replay` does
                when it reaches a game in a new season.
            exclude_game_id: a game id that must not contribute even if the corpus dates it before
                `as_of`. Normally a no-op -- the strict date filter and `compute_features`' refusal
                of an `as_of` past tip-off already put the target out of reach -- but F-044's
                truncated-timestamp case puts a target's own copy microseconds *before* its matchup
                date, and the date filter alone would then feed a game its own result. When it does
                fire, this takes an exact replay of the filtered prefix rather than an approximation.
        """
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise EloError("as_of must be a timezone-aware datetime")
        if not isinstance(season, int) or isinstance(season, bool):
            raise EloError(f"season must be an int, got {season!r} ({type(season).__name__})")

        cut = bisect_left(self._dates, as_of)
        excluded = self._index_by_id.get(exclude_game_id) if exclude_game_id is not None else None
        dropped = excluded is not None and excluded < cut

        last_index = cut - 1
        if dropped and excluded == last_index:
            last_index -= 1
        last_season = self._seasons[last_index] if last_index >= 0 else None

        # The regression `replay` would perform on reaching a game of `season`. Its `previous_season`
        # is None for the very first game it ever processes, and it regresses nothing then.
        target_regressions = 0
        if last_season is not None and season != last_season:
            if season < last_season:
                raise EloError(
                    f"season {season} is before the season of the last game preceding "
                    f"{as_of.isoformat()} ({last_season}) -- season labels must agree with dates, "
                    "or the carryover is applied at the wrong moments"
                )
            target_regressions = (
                (season - last_season) if self._config.regress_per_elapsed_year else 1
            )

        if dropped:
            # The exact path. `replay` regresses the whole dict, so both ratings come back with every
            # in-prefix regression already applied; only the target's own transition is left.
            home, away = self._replayed_prefix(home_id, away_id, cut, excluded)
            home_owed = away_owed = target_regressions
        else:
            base = self._regressions[last_index] if last_index >= 0 else 0
            home, home_at = self._rating_after_prefix(home_id, as_of)
            away, away_at = self._rating_after_prefix(away_id, as_of)
            home_owed = base + target_regressions - home_at
            away_owed = base + target_regressions - away_at

        config = self._config
        return _regress_value(
            home, carryover=config.carryover, mean=config.initial_rating, times=home_owed
        ) - _regress_value(
            away, carryover=config.carryover, mean=config.initial_rating, times=away_owed
        )

    def _replayed_prefix(
        self, home_id: str, away_id: str, cut: int, excluded: int
    ) -> tuple[float, float]:
        """The slow, exact path for the excluded-game case: replay the prefix without that game.

        Deliberately not an incremental un-update. Elo's update is not exactly invertible once later
        games have moved both ratings, and an approximate reversal on the corrupt-input path is how
        a leak becomes a rounding error nobody looks at.
        """
        prefix = [g for i, g in enumerate(self._ordered[:cut]) if i != excluded]
        ratings = replay(prefix, config=self._config).final_ratings
        return (
            ratings.get(home_id, self._config.initial_rating),
            ratings.get(away_id, self._config.initial_rating),
        )


def timeline(games: Sequence[Game], *, config: EloConfig = DEFAULT_CONFIG) -> Timeline:
    """Build a `Timeline` over `games`. Pass the whole corpus, warm-up seasons included (D-037)."""
    return Timeline(games, config=config)
