"""Pre-game feature computation (T-006) -- the deep module of the Phase 1 analytical core.

One public interface: `compute_features(history, target, as_of)`. Everything leakage-relevant lives
behind that signature (docs/plans/PLAN-current.md, "Modules"). The module is **pure**: no I/O, no
database, no network, and -- deliberately -- no clock. `as_of` is always a parameter, never
`datetime.now()`, because a function that can read the clock is a function whose output cannot be
reproduced, and every number this project reports has to be reproducible from committed code.

Features (all four from T-006's acceptance criteria), expressed as home-minus-away differences
except the home indicator, which is not a difference:

  home_advantage   1.0 normally, 0.0 at a neutral site.
  form_diff        home minus away shrunk win rate over each team's last FORM_WINDOW games
                   *this season*, before `as_of`.
  rest_diff        home minus away days since each team's previous game, capped at MAX_REST_DAYS.
  point_diff_diff  home minus away shrunk season-to-date average point differential, before `as_of`.

## The as-of filter is an integrity control (T-006's security note), enforced three ways

1. **It lives inside this module.** `compute_features` filters the history itself, at query time,
   through `GameHistory._records_before`. A caller cannot pass pre-filtered history and skip it, and
   cannot opt out of it: there is no flag, no alternate entry point, and no code path in this module
   that reads a game dated at or after `as_of`. The filter is strict (`<`, never `<=`), so a game at
   exactly the as-of instant is excluded -- which is also what excludes the target game from its own
   feature computation in the training case, where `as_of` IS the target's tip-off.

2. **The target's outcome is not in scope, structurally.** The target is a `Matchup` -- game id,
   date, season, the two team ids, neutral-site flag -- and carries no scores at all. A completed
   `Game` cannot be passed as the target. So the target's result cannot leak into its own features
   by any bug, because it is not reachable from here; `Game.matchup` is the deliberate one-way door.
   This is stronger than filtering it out, which is a thing code can forget to do.

3. **Predicting into the past is refused.** `as_of > target.date` raises `FeatureLeakageError`.
   At training time `as_of` is the target's own tip-off (use `compute_training_features`, which
   removes the choice); at inference it is the current moment, always before tip-off (D-010/D-012).
   An `as_of` after tip-off would let games played *after* the prediction moment into the window --
   a "pre-game" feature vector that could only be built after the fact. D-010 records why a model
   that re-predicts a finished game is worthless; this is that decision made mechanical.

Train/serve skew is designed out the same way (D-011, PLAN-v1 "The anti-skew mechanism"): training
and inference call this same function with the same semantics, differing only in the `as_of` they
pass, so there is no second implementation to drift from.

## Why the shrinkage priors are constants, not estimates

Cold start is handled by shrinking toward the league mean with weight `n/(n+k)`, k=5 (D-015).
Critically, both priors are **mathematical identities over any complete set of games**, not values
estimated from the corpus: every game produces exactly one winner and one loser, so the league-wide
win rate is exactly 0.5; and one team's margin is the other's negated, so the league-wide average
point differential is exactly 0.0. That matters for integrity, not just tidiness -- a prior fitted on
the dataset would be information from outside the as-of window entering every feature vector,
including opening night's. These constants carry no information about any particular season, so they
cannot leak.

## Dependencies: standard library only, on purpose

No pandas, no numpy (contrast `loader.py`, which needs both). Two reasons, both binding:
  - **CI.** `.github/workflows/gate.yml` installs `backend/requirements.txt` only, never
    `requirements-train.txt`. A feature module importing pandas would make `be-unit` fail in CI while
    passing locally -- the class of split-environment failure F-037 already cost this project a
    review round.
  - **D-016.** Phase 2 loads this module inside the FastAPI service for inference. Whatever it
    imports, the served image must carry. Keeping it stdlib means the anti-skew guarantee (one
    feature function, both sides) costs the deployed image nothing, so there is never a reason to
    write a lighter second copy -- which is exactly the skew D-011 removed.
The loader's frame converts to `Game` records with `games_from_frame` in `backend/model/dataset.py`,
which is where pandas is allowed to touch this pipeline.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

# --- tuning constants (D-015; window/cap grounded in the real 2022-2026 data, see docstrings) ----

# Shrinkage strength: weight on a team's own record is n/(n+k). k=5 per D-015.
SHRINKAGE_K: float = 5.0

# Rolling-form window, in games, scoped to the target's own season (D-020).
FORM_WINDOW: int = 10

# Rest is capped here. Measured over 2022-2026: median gap between a team's consecutive games is
# 2.0 days, p95 is 3.9, max is 10.0 (the All-Star break), and only 2.3% of gaps reach 5 days. Past
# roughly this point extra days stop being "rest" and start being schedule structure, so the cap
# keeps a 10-day break from reading as twice the advantage of a 5-day one. It also means a team's
# first game of a season -- whose previous game is months earlier, or does not exist -- needs no
# special case: fully rested is the honest reading, and the cap produces it.
MAX_REST_DAYS: float = 5.0

# Shrinkage targets. Identities over any complete set of games, not estimates -- see module
# docstring, "Why the shrinkage priors are constants".
PRIOR_WIN_RATE: float = 0.5
PRIOR_POINT_DIFF: float = 0.0

_SECONDS_PER_DAY: float = 86400.0

# The feature contract with the estimator (T-009). Order is significant: `to_vector` emits in this
# order, so a fitted artifact's coefficients line up with these names positionally.
FEATURE_NAMES: tuple[str, ...] = (
    "home_advantage",
    "form_diff",
    "rest_diff",
    "point_diff_diff",
)


class FeatureError(ValueError):
    """Base class for every refusal this module makes. Subclass of ValueError so a caller that
    catches broadly still catches these, but distinct enough to be caught on purpose."""


class FeatureInputError(FeatureError):
    """Raised when an input is malformed or ambiguous -- a naive datetime, a team playing itself, a
    tied final score, a non-`Game` in the history."""


class FeatureLeakageError(FeatureError):
    """Raised when the requested computation would use information that did not exist at the
    prediction moment. Today that is exactly one case: `as_of` after the target's tip-off."""


def _require_aware(value: datetime, label: str) -> None:
    """Reject naive datetimes.

    The as-of filter compares instants, and a naive datetime is not an instant -- it is a wall-clock
    reading whose meaning depends on a timezone nobody recorded. Python would happily compare two
    naive datetimes and produce a confident, wrong answer about which came first; it raises only when
    naive and aware are mixed. Since the whole integrity guarantee rests on that comparison, ambiguous
    input is refused at the boundary rather than trusted. `loader.py` parses with `utc=True`, so real
    data arrives aware.
    """
    if not isinstance(value, datetime):
        raise FeatureInputError(f"{label} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise FeatureInputError(
            f"{label} is a naive datetime ({value!r}) -- the as-of filter compares instants, and a "
            "naive datetime has no unambiguous instant to compare. Pass a timezone-aware datetime."
        )


@dataclass(frozen=True, slots=True)
class Matchup:
    """The pre-game facts about a game: who is playing, when, and where.

    This is the *only* type `compute_features` accepts as its target, and it deliberately carries no
    scores -- see the module docstring, point 2. It is equally constructible for a game played in
    2022 and one tipping off next Tuesday, which is what lets training and inference call one
    function (D-011).
    """

    game_id: str
    date: datetime
    season: int
    home_id: str
    away_id: str
    neutral_site: bool = False

    def __post_init__(self) -> None:
        _require_aware(self.date, f"matchup {self.game_id!r} date")
        if self.home_id == self.away_id:
            raise FeatureInputError(
                f"matchup {self.game_id!r} has the same team ({self.home_id!r}) on both sides"
            )


@dataclass(frozen=True, slots=True)
class Game:
    """A completed game: a `Matchup` plus its final score. The unit of the history collection.

    Mirrors the loader's normalized frame one-for-one (`backend/model/loader.py`,
    `_read_completed_games`), so `dataset.games_from_frame` is a field-by-field conversion with no
    reinterpretation.
    """

    game_id: str
    date: datetime
    season: int
    home_id: str
    away_id: str
    home_score: int
    away_score: int
    neutral_site: bool = False

    def __post_init__(self) -> None:
        _require_aware(self.date, f"game {self.game_id!r} date")
        if self.home_id == self.away_id:
            raise FeatureInputError(
                f"game {self.game_id!r} has the same team ({self.home_id!r}) on both sides"
            )
        if self.home_score == self.away_score:
            # NBA games cannot end tied -- they go to overtime -- and none of the 6,615 completed
            # games in the pinned 2022-2026 corpus do (verified). So a tie here is not an edge case
            # to model, it is corrupt input, and counting it as a loss for the home team (which is
            # what a bare `home_score > away_score` does) would quietly bias every form feature the
            # team appears in. Fail hard rather than self-heal quietly, consistent with D-019(3).
            raise FeatureInputError(
                f"game {self.game_id!r} is tied at {self.home_score} -- an NBA game cannot end "
                "tied, so this is corrupt input, not a drawable result; refusing to score it"
            )

    @property
    def home_margin(self) -> int:
        return self.home_score - self.away_score

    @property
    def home_win(self) -> bool:
        return self.home_score > self.away_score

    @property
    def matchup(self) -> Matchup:
        """The pre-game view of this game -- everything that was knowable before tip-off.

        The one-way door of the module docstring's point 2: features are computed from this, so a
        completed game's own result is structurally out of reach of its own feature vector.
        """
        return Matchup(
            game_id=self.game_id,
            date=self.date,
            season=self.season,
            home_id=self.home_id,
            away_id=self.away_id,
            neutral_site=self.neutral_site,
        )


@dataclass(frozen=True, slots=True)
class _TeamGame:
    """One completed game from one team's point of view. Internal to the index below."""

    game_id: str
    date: datetime
    season: int
    won: bool
    margin: int


class GameHistory:
    """An immutable, team-indexed view of a completed-game collection.

    Purely an efficiency structure, and deliberately a dumb one: it indexes the **whole** history and
    applies no as-of filtering at construction. Filtering happens per query, in `_records_before`,
    against whatever `as_of` that call was given. That ordering is the reason this class cannot
    weaken the integrity guarantee -- there is no "already filtered" state for a caller to build once
    and reuse against a later `as_of`, and passing a prebuilt index is therefore exactly equivalent
    to passing the raw sequence (asserted in the tests, both directions).

    Building it is worth the code: T-009 computes features for every one of 6,615 games against a
    6,615-game history, which is ~44M comparisons scanned naively and a bisect per team here.
    """

    __slots__ = ("_dates", "_records")

    def __init__(self, games: Iterable[Game]) -> None:
        by_team: dict[str, list[_TeamGame]] = {}
        for game in games:
            if not isinstance(game, Game):
                raise FeatureInputError(
                    f"history must contain Game records, got {type(game).__name__}"
                )
            margin = game.home_margin
            won = game.home_win
            by_team.setdefault(game.home_id, []).append(
                _TeamGame(game.game_id, game.date, game.season, won, margin)
            )
            by_team.setdefault(game.away_id, []).append(
                _TeamGame(game.game_id, game.date, game.season, not won, -margin)
            )

        self._records: dict[str, tuple[_TeamGame, ...]] = {}
        self._dates: dict[str, tuple[datetime, ...]] = {}
        for team, records in by_team.items():
            # Sort by (date, game_id), not date alone: a team cannot really play twice at the same
            # instant, but the tiebreaker makes the index a pure function of the *set* of input
            # games rather than of their iteration order, so a shuffled history provably produces
            # identical features (asserted in the tests).
            records.sort(key=lambda r: (r.date, r.game_id))
            self._records[team] = tuple(records)
            self._dates[team] = tuple(r.date for r in records)

    @classmethod
    def of(cls, history: GameHistory | Iterable[Game]) -> GameHistory:
        """Normalize either accepted history form to an index. Idempotent."""
        return history if isinstance(history, cls) else cls(history)

    def _records_before(self, team: str, as_of: datetime) -> tuple[_TeamGame, ...]:
        """That team's completed games dated **strictly before** `as_of`, oldest first.

        This is the as-of filter -- the single place it is applied, and the reason it cannot be
        bypassed. `bisect_left` returns the first index whose date is >= `as_of`, so the slice below
        excludes a game at exactly the as-of instant. Strictness is not incidental: in training
        `as_of` is the target's own tip-off, so this is what keeps a game out of its own features.
        """
        dates = self._dates.get(team)
        if not dates:
            return ()
        return self._records[team][: bisect.bisect_left(dates, as_of)]


def _shrink(observed: float, n: int, prior: float) -> float:
    """Blend a team's own `n`-game observation toward `prior` with weight `n/(n+k)` (D-015).

    n=0 -> the prior exactly (and `observed` is never read, so callers need no sentinel);
    n=k -> exactly halfway; n->inf -> the observation. Monotone in n by construction.
    """
    if n <= 0:
        return prior
    weight = n / (n + SHRINKAGE_K)
    return weight * observed + (1.0 - weight) * prior


def _form(records: tuple[_TeamGame, ...], season: int) -> float:
    """Shrunk win rate over the team's last `FORM_WINDOW` games of `season`.

    Season-scoped (D-020): last season's results describe a different roster, and scoping here is
    what makes the cold start D-015 is about recur every autumn rather than only in 2022.
    `records` is already as-of filtered and date-ordered, so the tail is the most recent window.
    """
    window = [r for r in records if r.season == season][-FORM_WINDOW:]
    n = len(window)
    if n == 0:
        return PRIOR_WIN_RATE
    return _shrink(sum(1 for r in window if r.won) / n, n, PRIOR_WIN_RATE)


def _season_point_diff(records: tuple[_TeamGame, ...], season: int) -> float:
    """Shrunk season-to-date average point differential. Uses every game of `season` so far, not a
    window -- "season-to-date" is the plan's wording, and it is the slower-moving counterpart to the
    10-game form feature."""
    margins = [r.margin for r in records if r.season == season]
    n = len(margins)
    if n == 0:
        return PRIOR_POINT_DIFF
    return _shrink(sum(margins) / n, n, PRIOR_POINT_DIFF)


def _rest_days(records: tuple[_TeamGame, ...], tip_off: datetime) -> float:
    """Days from the team's most recent completed game to `tip_off`, clamped to [0, MAX_REST_DAYS].

    Measured to **tip-off**, not to `as_of`: rest is a property of the game being predicted. A
    prediction made six days out therefore carries a rest value that is wrong rather than merely
    stale -- the team will play again before then -- which is precisely why D-012 re-predicts daily
    and keys predictions by `as_of` instead of updating a row in place. Computing it from `as_of`
    would hide that, by making the number self-consistently meaningless instead of visibly wrong.

    Not season-scoped and needs no empty-history case: the previous season's last game, or no game
    at all, both land at the cap, which reads as "fully rested" -- the honest value for an opener.
    """
    if not records:
        return MAX_REST_DAYS
    gap_days = (tip_off - records[-1].date).total_seconds() / _SECONDS_PER_DAY
    return min(max(gap_days, 0.0), MAX_REST_DAYS)


def compute_features(
    history: GameHistory | Iterable[Game], target: Matchup, as_of: datetime
) -> dict[str, float]:
    """The interface. Features for `target` using only games completed strictly before `as_of`.

    Args:
        history: every completed game available, as `Game` records or a prebuilt `GameHistory`.
            Pass the full collection -- do NOT pre-filter it. Filtering is this module's job, and a
            caller that filters is a caller that can filter wrongly.
        target: the pre-game `Matchup`. Use `Game.matchup` for a completed game.
        as_of: the prediction moment, timezone-aware. Training: the target's own tip-off (prefer
            `compute_training_features`). Inference: the current moment, before tip-off.

    Returns:
        A dict keyed by exactly `FEATURE_NAMES`.

    Raises:
        FeatureInputError: naive `as_of`, wrong target type, or malformed history.
        FeatureLeakageError: `as_of` is after `target.date`.
    """
    if not isinstance(target, Matchup):
        raise FeatureInputError(
            f"target must be a Matchup (a completed Game exposes one as `.matchup`), got "
            f"{type(target).__name__}"
        )
    _require_aware(as_of, "as_of")
    if as_of > target.date:
        raise FeatureLeakageError(
            f"as_of {as_of.isoformat()} is after tip-off {target.date.isoformat()} for game "
            f"{target.game_id!r} -- these are pre-game features, so an as-of moment after the game "
            "started would admit information that did not exist when the prediction was made. "
            "Pass the tip-off itself for training (see compute_training_features), or the real "
            "prediction moment for inference."
        )

    index = GameHistory.of(history)
    home = index._records_before(target.home_id, as_of)
    away = index._records_before(target.away_id, as_of)

    return {
        "home_advantage": 0.0 if target.neutral_site else 1.0,
        "form_diff": _form(home, target.season) - _form(away, target.season),
        "rest_diff": _rest_days(home, target.date) - _rest_days(away, target.date),
        "point_diff_diff": (
            _season_point_diff(home, target.season) - _season_point_diff(away, target.season)
        ),
    }


def compute_training_features(
    history: GameHistory | Iterable[Game], game: Game
) -> dict[str, float]:
    """Features for a completed game as of its own tip-off -- the training as-of rule, in code.

    Exists so T-009 never picks an `as_of` for training. The rule ("at training time the as-of moment
    is the target game's own tip-off", PLAN-v1) is the other half of the anti-skew mechanism, and a
    rule stated in a plan is one a training loop can get wrong; a rule with no parameter is not.
    """
    return compute_features(history, game.matchup, game.date)


def to_vector(features: Mapping[str, float]) -> tuple[float, ...]:
    """Order a feature mapping into `FEATURE_NAMES` order for the estimator (T-009).

    Fails on a missing or unexpected key rather than filling a default: a silently-zero feature is
    the kind of defect that shows up as a mildly disappointing accuracy number and nothing else.
    """
    missing = [name for name in FEATURE_NAMES if name not in features]
    unexpected = [name for name in features if name not in FEATURE_NAMES]
    if missing or unexpected:
        raise FeatureInputError(
            f"feature mapping does not match FEATURE_NAMES (missing={missing}, "
            f"unexpected={unexpected})"
        )
    return tuple(float(features[name]) for name in FEATURE_NAMES)
