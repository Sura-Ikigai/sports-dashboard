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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .features import Game

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
