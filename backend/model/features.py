"""Pre-game feature computation -- the deep module of the analytical core (T-006, rebuilt in T-028).

One public interface: `compute_features(context, target, as_of)`. Everything leakage-relevant lives
behind that signature. The module is **pure**: no I/O, no database, no network, and -- deliberately
-- no clock. `as_of` is always a parameter, never `datetime.now()`, because a function that can read
the clock is a function whose output cannot be reproduced, and every number this project reports has
to be reproducible from committed code.

## The v2 feature set (D-032 ... D-036), and what it replaced

  elo_diff      Home minus away pre-game MOV Elo rating, WITHOUT the home adjustment (D-032).
                Replaces `point_diff_diff`: MOV Elo *is* an opponent-adjusted point differential,
                the two correlate at .92, and keeping both is collinearity for ~+.002 AUC.
  home_b2b      1.0 when the home team played the previous day (zero days of rest), else 0.0.
  away_b2b      The same for the away team. Separate indicators on purpose (user story 7): whether
                a back-to-back costs the two sides equally is a thing to measure, not assume.
  rest_edge     Home minus away days of rest, each bucketed at `REST_EDGE_CAP`. Integer in
                [-3, 3]. Together with the two indicators this is D-034's re-encoding of the old
                capped-linear `rest_diff`, whose single slope had to serve both the 0-day cliff and
                the flat region past it.
  avail_diff    Home minus away lagged rotation availability (D-035). The only genuinely new
                factor -- it correlates with `elo_diff` at just +.255, against the .87-.92 band that
                made the Phase 1 features three measurements of one thing.
  travel_diff   Home minus away miles travelled since each team's previous game of this season
                (D-036). Signed home-minus-away like every other difference here, so its
                coefficient is expected to be *negative*: travel is a cost, and the sign convention
                is about orientation, not about which way is good.
  altitude      1.0 when this game is played at a high-elevation venue and is not at a neutral site.

`form_diff` and `home_advantage` are gone (D-033). Form was a worse ruler for what Elo now measures.
Home advantage was never identifiable (D-024) -- a column of 1.0 differing from the intercept by a
constant -- and it lives in the intercept, where it always did.

## The Context, and why the as-of filter had to move up a level

T-006's `history` was a sequence of games, and the as-of filter had one thing to filter. The v2 set
reads three time-varying sources -- games, player participation, and (through each team's previous
game) venues -- and the security note for this task is blunt about the consequence: the as-of filter
is now the integrity control for **every** one of them, not just games. One filter, inside this
module, applied uniformly.

`Context` is what makes "uniformly" mechanical rather than aspirational:

1. **It holds sources, never answers.** Every index it carries -- the game history, `elo.Timeline`,
   `availability.AppearanceIndex` -- is built over the *whole* corpus and has no method that
   answers without an `as_of`. There is no cached "current" anything to read by mistake.
2. **`compute_features` derives one `as_of` and passes that same instant to every source.** A
   source cannot be consulted at a different moment than its neighbours, because there is only one
   moment in scope.
3. **It is built once and reused.** Which is what makes (1) affordable: the Elo replay and the
   participation index cost one pass over the corpus each, not one pass per feature vector. The
   equality that shortcut rests on -- index answer == replay of the filtered prefix -- is asserted
   in `test_elo.py` and `test_availability.py` over whole corpora, not argued for here.

The property test is what proves the result: injecting future games **and** future player-box rows
must not move a single number, paired with the control showing that a row dated *before* `as_of`
does move them -- without which a module that ignored its inputs entirely would pass perfectly.

## The as-of filter as an integrity control, enforced four ways

1. **It lives inside this module.** `compute_features` filters at query time. A caller cannot pass
   pre-filtered data and skip it, and cannot opt out: no flag, no alternate entry point, and no code
   path here that reads anything dated at or after `as_of`. The filter is strict (`<`, never `<=`),
   so a game at exactly the as-of instant is excluded -- which is also what excludes the target from
   its own feature vector in the training case, where `as_of` IS the target's tip-off.

2. **The target's outcome is closed off on both routes in.** The target is a `Matchup` -- game id,
   date, season, the two team ids, neutral-site flag -- carrying no scores at all, and a completed
   `Game` cannot be passed as the target; `Game.matchup` is the deliberate one-way door. That shuts
   the *direct* route. The other route is the corpus, where the target's own row lives: the strict
   date filter closes it, and `exclude_game_id` closes it again for the F-044 case where the
   corpus's copy of the target is dated microseconds earlier than the matchup it was built from.
   Both `elo.Timeline` and `availability.AppearanceIndex` take that exclusion, so the belt-and-braces
   is uniform across sources rather than applied to games alone.

3. **Predicting into the past is refused.** `as_of > target.date` raises `FeatureLeakageError`. At
   training time `as_of` is the target's own tip-off (use `compute_training_features`, which removes
   the choice); at inference it is the current moment, always before tip-off (D-010/D-012).

4. **A narrowed Context must say what it narrowed by** (F-113). D-039 lets a query reduce rows by
   season or by team, and an under-narrowed history yields shrinkage priors -- byte-identical to
   what opening night legitimately produces. `Coverage` is the declaration and `_require_covers` is
   the check.

Train/serve skew is designed out the same way (D-011): training and inference call this same
function with the same semantics, differing only in the `as_of` they pass, so there is no second
implementation to drift from.

## Dependencies: standard library only, on purpose

No pandas, no numpy. Two reasons, both binding:
  - **CI** installs `backend/requirements.txt` for the served image; a feature module importing
    pandas fails there while passing locally -- the split-environment failure F-037 cost a review
    round over.
  - **D-016.** The FastAPI service imports this module for inference. Whatever it imports, the
    served image must carry. That constraint propagates: `elo`, `availability`, `venues` and
    `records` are all standard-library-only for the same reason.
`dataset.py` and `store.py` are the two seams where pandas and SQLAlchemy respectively are allowed
to meet this pipeline, and both of them build a `Context` rather than reaching past one.

## Precondition this module cannot check for itself (F-047)

The corpus must contain only **final** games. `Game.date` is tip-off, not completion, and a `Game`
carries no status field -- so "games completed strictly before `as_of`" is enforced here as "games
that tipped off strictly before `as_of`". In the pinned corpus the two coincide. They stop
coinciding wherever one `games` table serves both training and inference and the status enum is
`scheduled | live | final`: a caller that forgets `status == \'final\'` would feed a live partial
score into a "pre-game" vector, and this module would accept it. Filter before you get here.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from . import availability as availability_module
from . import elo as elo_module
from .records import (
    FeatureError,
    FeatureInputError,
    FeatureLeakageError,
    Game,
    Matchup,
    _require_aware,
)
from .venues import City, distance_miles

__all__ = [
    "FEATURE_NAMES",
    "REST_EDGE_CAP",
    "Context",
    "Coverage",
    "FeatureError",
    "FeatureInputError",
    "FeatureLeakageError",
    "Game",
    "GameHistory",
    "Matchup",
    "compute_features",
    "compute_training_features",
    "to_vector",
]

# --- tuning constants (D-034; grounded in the measured 2022-2026 schedule) ------------------------

# Days of rest are bucketed at three. Measured over 2022-2026: the median gap between a team's
# consecutive games is 2.0 days, p95 is 3.9, and only 2.3% of gaps reach 5. Past three days the
# marginal day stops being rest and starts being schedule structure, and the sample supporting a
# distinction is thin. The cap also removes any need for a special case at a season's first game --
# "fully rested" is the honest reading of a months-long gap, and the cap produces it.
REST_EDGE_CAP: int = 3

# A back-to-back is zero days of rest: two games on consecutive days.
_B2B_REST_DAYS: int = 0

_SECONDS_PER_DAY: float = 86400.0

# The feature contract with the estimator. Order is significant: `to_vector` emits in this order, so
# a fitted artifact\'s coefficients line up with these names positionally.
FEATURE_NAMES: tuple[str, ...] = (
    "elo_diff",
    "home_b2b",
    "away_b2b",
    "rest_edge",
    "avail_diff",
    "travel_diff",
    "altitude",
)


@dataclass(frozen=True, slots=True)
class _TeamGame:
    """One completed game from one team's point of view. Internal to the index below."""

    game_id: str
    date: datetime
    season: int
    won: bool
    margin: int
    opponent_id: str


@dataclass(frozen=True, slots=True)
class Coverage:
    """What a history CLAIMS to contain -- the other half of the as-of guarantee (F-113).

    The as-of filter guarantees *"no game dated at or after `as_of` entered this vector."* It cannot
    guarantee the complementary half -- *"every game before `as_of` that should have entered, did"* --
    because a history that is missing games is indistinguishable from a history whose teams simply had
    not played yet. Both halves are required for a feature vector to be honest; until now only the
    first was a control and the second was an instruction in a docstring.

    That was harmless while exactly one caller existed, passing the entire in-memory corpus. It stops
    being harmless under D-038/D-039: history arrives from SQL, and D-039 permits narrowing *"by
    season or by team"* without defining what a **sufficient** narrowing is. An under-narrowed history
    produces shrinkage priors, which is **byte-identical** to what opening night legitimately produces
    -- D-015 deliberately removed the drop-early-games symptom that would otherwise expose it -- so the
    failure is silent by construction. F-045 closed the *type-mismatch* route to that same silent
    all-priors failure; this closes the *incomplete-history* route to it.

    So a narrowing caller **declares** what it narrowed by, and `compute_features` refuses a target the
    declaration does not cover. Every field defaults to `None` meaning "unrestricted", so a caller
    passing the full corpus declares nothing and behaves exactly as it did before.

    Fields:
        teams: the team ids this history contains every game for. `None` = every team.
        complete_from: the moment this history's record *begins*. `None` = "this is the beginning of
            the record", which is a claim only a caller that loaded the full corpus may make.
        complete_to: the moment this history's record *ends*. `None` = "through the end of the
            record". A window reaching past this is refused.
    """

    teams: frozenset[str] | None = None
    complete_from: datetime | None = None
    complete_to: datetime | None = None

    def __post_init__(self) -> None:
        for label in ("complete_from", "complete_to"):
            value = getattr(self, label)
            if value is not None:
                _require_aware(value, f"Coverage.{label}")
        if (
            self.complete_from is not None
            and self.complete_to is not None
            and self.complete_from >= self.complete_to
        ):
            raise FeatureInputError(
                f"Coverage.complete_from ({self.complete_from.isoformat()}) must be strictly before "
                f"complete_to ({self.complete_to.isoformat()}) -- an empty or inverted range cannot "
                "describe a history that contains anything"
            )
        if self.teams is not None:
            if not isinstance(self.teams, frozenset):
                raise FeatureInputError(
                    f"Coverage.teams must be a frozenset of team ids or None, got "
                    f"{type(self.teams).__name__} -- a mutable set would let a caller widen a "
                    "declaration after the check read it"
                )
            if not self.teams:
                raise FeatureInputError(
                    "Coverage.teams is empty -- a history covering no teams cannot produce any "
                    "feature. Pass None to declare 'every team'."
                )


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

    __slots__ = ("_coverage", "_dates", "_games", "_records")

    def __init__(self, games: Sequence[Game], *, coverage: Coverage | None = None) -> None:
        # F-045: a one-shot iterator is the declared-looking input that fails silently. Building an
        # index consumes it, so a *second* call with the same generator sees an empty history, falls
        # back to every prior, and raises nothing -- indistinguishable from a legitimate cold start.
        # Refusing non-sequences is the cheap structural fix; `list(games)` would not help, because
        # by the second call there is nothing left to copy.
        if not isinstance(games, Sequence) or isinstance(games, (str, bytes)):
            raise FeatureInputError(
                f"history must be a Sequence of Game records (list/tuple), got "
                f"{type(games).__name__}. A generator or other one-shot iterator is refused: it "
                "would be consumed by the first call and silently yield priors on every later one."
            )

        # F-115: `type(...) is`, deliberately not `isinstance` -- the same rule F-050 established for
        # `GameHistory.of`, applied to the second integrity control installed in this class. Under
        # `isinstance` a frozen-dataclass subclass overriding `__post_init__` without `super()` skips
        # every validation in `Coverage`, which was demonstrated taking a **mutable** set and having
        # the declaration widened after the index was built -- exactly the hazard `Coverage`'s own
        # error message names. A declaration that can be edited after it is checked is not a
        # declaration.
        if coverage is not None and type(coverage) is not Coverage:
            raise FeatureInputError(
                f"coverage must be exactly a Coverage or None, got {type(coverage).__name__}. A "
                "subclass is refused: overriding __post_init__ skips the validation that makes a "
                "declaration trustworthy, and a coverage that can be widened after this index was "
                "built would silently re-open the incomplete-history failure (F-113). Compose with a "
                "Coverage instead of inheriting from it."
            )
        self._coverage: Coverage = coverage if coverage is not None else Coverage()

        by_team: dict[str, list[_TeamGame]] = {}
        seen_ids: set[str] = set()
        for game in games:
            if not isinstance(game, Game):
                raise FeatureInputError(
                    f"history must contain Game records, got {type(game).__name__}"
                )
            # F-046: F-027's "right count, wrong rows", one layer downstream. T-005 asserts game_id
            # uniqueness *per season*, and `verify_completed_counts` keys its input by season, so a
            # repeated season collapses to one key and passes -- `load_games((2022, 2022))` returned
            # 2,648 games with 1,324 unique ids and no error, shifting real features. This is the
            # only place that covers every path in, since every caller ends up building an index.
            if game.game_id in seen_ids:
                raise FeatureInputError(
                    f"history contains duplicate game_id {game.game_id!r} -- a duplicated game is "
                    "counted twice in every feature derived from it. Check for an overlapping "
                    "season list or a concatenation that ran twice."
                )
            seen_ids.add(game.game_id)
            margin = game.home_margin
            won = game.home_win
            by_team.setdefault(game.home_id, []).append(
                _TeamGame(game.game_id, game.date, game.season, won, margin, game.away_id)
            )
            by_team.setdefault(game.away_id, []).append(
                _TeamGame(game.game_id, game.date, game.season, not won, -margin, game.home_id)
            )

        self._games: tuple[Game, ...] = tuple(sorted(games, key=lambda g: (g.date, g.game_id)))
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

    @property
    def games(self) -> tuple[Game, ...]:
        """Every game in the index, ordered by `(date, game_id)`.

        A view for `Context` to hand to `elo.Timeline`, not a second copy of the history: the tuple
        is built once at construction and the records are frozen, so there is nothing here a caller
        can mutate into disagreeing with the index it came from.
        """
        return self._games

    @property
    def teams(self) -> frozenset[str]:
        """Every team appearing in the index. `Context` checks its participation map against this."""
        return frozenset(self._records)

    @classmethod
    def of(cls, history: GameHistory | Sequence[Game]) -> GameHistory:
        """Normalize either accepted history form to an index. Idempotent.

        F-050: `type(...) is cls`, deliberately not `isinstance`. Under `isinstance` a *subclass*
        was returned unwrapped, and `compute_features` then called that subclass's
        `_records_before` -- a six-line override was enough to hand back unfiltered history and leak
        a future game. That is a call site opting out of the as-of filter, which the security note
        forbids; an exact type check means the only `_records_before` that can ever run is this one.

        A subclass is refused outright rather than quietly rebuilt. Rebuilding would be the friendlier
        behavior and the wrong one: it would silently discard whatever the subclass was written to do,
        and the caller would never learn that their override does not run. If you need extra state
        alongside the index, hold the index rather than inheriting from it.
        """
        if type(history) is cls:
            return history
        if isinstance(history, GameHistory):
            raise FeatureInputError(
                f"{type(history).__name__} subclasses GameHistory, which is refused: overriding "
                "the as-of filter is exactly what the exact-type check exists to prevent. Compose "
                "with a GameHistory instead of inheriting from it."
            )
        return cls(history)

    def _records_before(
        self, team: str, as_of: datetime, exclude_game_id: str, opponent_id: str
    ) -> tuple[_TeamGame, ...]:
        """That team's completed games dated **strictly before** `as_of`, oldest first, excluding
        `exclude_game_id`.

        This is the as-of filter -- the single place it is applied, and the reason it cannot be
        bypassed. `bisect_left` returns the first index whose date is >= `as_of`, so the slice below
        excludes a game at exactly the as-of instant. Strictness is not incidental: in training
        `as_of` is the target's own tip-off, so this is what keeps a game out of its own features.

        `exclude_game_id` (F-044) is what makes that last sentence true regardless of the caller.
        The date filter alone only excluded the target because `as_of == target.date`, which is the
        caller's data property, not this module's guarantee -- with `as_of` even microseconds later
        (and `to_pydatetime()` truncating nanoseconds is enough to produce exactly that), the target
        fell inside its own window and its own result entered its own features.
        """
        dates = self._dates.get(team)
        if not dates:
            return ()
        window = self._records[team][: bisect_left(dates, as_of)]
        kept = []
        for record in window:
            if record.game_id != exclude_game_id:
                kept.append(record)
                continue
            # F-068: the exclusion must not be a blunt id match. Dropping *any* record sharing the
            # target's id made this control fail OPEN -- a target id colliding with a real historical
            # game silently deleted that game from both teams' windows (form_diff 0.125 -> 0.0 with
            # no error). Matching the opponent too identifies the target precisely, and a same-id
            # game against a *different* opponent is not the target: it is corrupt input, and on a
            # module whose whole thesis is failing closed it must say so rather than quietly drop a
            # real game.
            if record.opponent_id != opponent_id:
                raise FeatureInputError(
                    f"history contains game_id {record.game_id!r} against opponent "
                    f"{record.opponent_id!r}, but the target with that id is against "
                    f"{opponent_id!r} -- game ids must identify one game. Refusing to guess which "
                    "of the two to drop from the as-of window."
                )
        return tuple(kept)

    def _require_covers(self, target: Matchup, as_of: datetime) -> None:
        """Refuse a target this history does not claim to cover (F-113).

        Runs before any feature is computed, because the failure it guards against has no symptom
        after the fact: an incomplete history returns shrinkage priors, and a vector of priors is
        exactly what a legitimate opening-night game produces.

        The three checks correspond to the three ways D-039's permitted narrowings can go wrong.
        """
        coverage = self._coverage

        if coverage.teams is not None:
            absent = [t for t in (target.home_id, target.away_id) if t not in coverage.teams]
            if absent:
                raise FeatureInputError(
                    f"history declares coverage of {len(coverage.teams)} team(s) and does not "
                    f"include {absent} -- game {target.game_id!r} is {target.away_id!r} at "
                    f"{target.home_id!r}. A team-narrowed query fetched the wrong teams; without this "
                    "check that team's features would be computed from an empty window and returned "
                    "as priors, which is indistinguishable from a team that has not played yet."
                )

        if coverage.complete_from is not None:
            # Blunt on purpose. Every look-back feature in this module is unbounded in time:
            # `_rest_days` is explicitly NOT season-scoped (a season's first game reads back into the
            # previous season), and D-032's `elo_diff` is running state across *all* prior seasons
            # including D-037's 2016-2019 warm-up. So no lower bound is ever sufficient, and a
            # season-narrowed history is wrong for both -- today only silently, because MAX_REST_DAYS
            # happens to sit below the 120-133 day offseason (T-007's measured figure). That is
            # arithmetic, not design, and D-032 removes even that coincidence.
            raise FeatureInputError(
                f"history declares its record begins at {coverage.complete_from.isoformat()}, so it "
                "cannot support features that look back past that moment -- `rest_diff` is not "
                "season-scoped, and `elo_diff` (D-032) is running state over every prior season. "
                "Narrow by team, never by season or date range. If you loaded the full corpus and it "
                "simply starts where it starts, declare complete_from=None: that is the claim 'this "
                "is the beginning of the record', and it is the loader's to make, not a query's."
            )

        if coverage.complete_to is not None and as_of > coverage.complete_to:
            raise FeatureInputError(
                f"as_of {as_of.isoformat()} is after this history's declared record end "
                f"{coverage.complete_to.isoformat()} -- the as-of window would extend past what the "
                "history claims to contain, and the games in the gap would be silently missing rather "
                "than absent."
            )


def _rest_days(records: tuple[_TeamGame, ...], tip_off: datetime) -> int:
    """Whole days of rest before `tip_off`, bucketed into [0, REST_EDGE_CAP].

    **Elapsed hours rounded to days, not a difference of calendar dates.** Every date in this
    pipeline is UTC, and a 10:30pm Eastern tip-off is already the next day in UTC -- so differencing
    calendar dates would call a back-to-back pair "two days apart" for exactly the late games where
    rest matters most. Elapsed time carries no timezone at all, which is why it is the thing measured
    here. Rounding is safe with a wide margin: the boundary between "back-to-back" and "one day off"
    sits at 36 hours, and real consecutive-day pairs span roughly 18 to 32 hours (a 12:00pm holiday
    start after a 10:30pm game is the extreme), so nothing in the corpus lands near it.

    Measured to **tip-off**, not to `as_of`: rest is a property of the game being predicted. A
    prediction made six days out therefore carries a rest value that is wrong rather than merely
    stale -- the team will play again before then -- which is precisely why D-012 re-predicts daily
    and keys predictions by `as_of` instead of updating a row in place. Computing it from `as_of`
    would hide that by making the number self-consistently meaningless instead of visibly wrong.

    Not season-scoped, and needs no empty-history case: the previous season\'s last game, or no game
    at all, both land on the cap, which reads as fully rested.
    """
    if not records:
        return REST_EDGE_CAP
    elapsed_days = (tip_off - records[-1].date).total_seconds() / _SECONDS_PER_DAY
    return min(max(round(elapsed_days) - 1, 0), REST_EDGE_CAP)


def _travel_miles(
    records: tuple[_TeamGame, ...], destination: City, season: int, cities: Mapping[str, City]
) -> float:
    """Miles from the team\'s previous game **of this season** to `destination`.

    Season-scoped, unlike rest, and for the opposite reason. A months-long gap reads honestly as
    "fully rested", so rest needs no season boundary; it does not read honestly as "flew 2,400
    miles", so travel does. A season\'s first game carries no accumulated travel and returns 0.0 --
    which is a real claim about the world, not a fallback for missing data. Missing data is refused
    at `Context` construction, where a game with no venue is an error rather than a silent zero.
    """
    if not records:
        return 0.0
    previous = records[-1]
    if previous.season != season:
        return 0.0
    origin = cities.get(previous.game_id)
    if origin is None:
        raise FeatureInputError(
            f"game {previous.game_id!r} is in the history but has no venue in this Context -- "
            "travel has no fallback, and a silent 0.0 would read as a home stand. Build the "
            "Context with a city for every game its history contains."
        )
    return distance_miles(origin, destination)


class Context:
    """Everything time-varying a feature vector reads, indexed once and filtered per query.

    Replaces T-006\'s bare `history` argument. It carries the three sources the v2 feature set needs
    -- completed games, player participation, and the venue each game was played at -- and it turns
    each into an index that **cannot be asked a question without an as-of moment**. That property is
    the whole point: the as-of filter is now the integrity control for three sources rather than one,
    and the cheapest way to apply it uniformly is to make an un-filtered read impossible to express.

    ## Why all three are required rather than optional

    An optional participation map would default `avail_diff` to 0.0 and an optional venue map would
    default `travel_diff` to 0.0, and both zeros are values the feature legitimately takes -- a
    perfectly healthy pair of teams, a home stand. So a caller who forgot to load the box scores
    would get a feature vector that is wrong in a way nothing downstream can detect, which is the
    silent-failure shape F-045 and F-113 both closed other routes to. There is no partial Context.

    Args:
        history: every completed game available, as a **sequence** of `Game` records (list or tuple
            -- not a generator, F-045) or a prebuilt `GameHistory`. Pass the full collection
            including D-037\'s warm-up seasons: Elo is running state over every prior season, so a
            rating difference computed from a subset is a different number. Do NOT pre-filter by
            date -- filtering is this module\'s job, and a caller that filters is a caller that can
            filter wrongly. **If you narrow it, say so** by giving the `GameHistory` a `Coverage`.
        appearances: `team_id` -> that team\'s `availability.Appearance` records, over the same
            games. Must cover every team the history does; a team missing here would silently read
            as league-average availability forever.
        game_cities: `game_id` -> the `venues.City` the game was played in, for every game in the
            history. Resolving cities here rather than per feature means an unknown venue fails once,
            loudly, at construction -- rather than 6,000 times, quietly, as zero travel.
    """

    __slots__ = ("_availability", "_cities", "_elo", "_history")

    def __init__(
        self,
        history: GameHistory | Sequence[Game],
        appearances: Mapping[str, Sequence[availability_module.Appearance]],
        game_cities: Mapping[str, City],
        *,
        elo_config: elo_module.EloConfig | None = None,
        availability_config: availability_module.AvailabilityConfig | None = None,
    ) -> None:
        index = GameHistory.of(history)
        games = index.games

        if not isinstance(game_cities, Mapping):
            raise FeatureInputError(
                f"game_cities must be a Mapping of game id -> City, got "
                f"{type(game_cities).__name__}"
            )
        for game_id, city in game_cities.items():
            if not isinstance(city, City):
                raise FeatureInputError(
                    f"game_cities[{game_id!r}] must be a venues.City, got {type(city).__name__}"
                )
        missing = [game.game_id for game in games if game.game_id not in game_cities]
        if missing:
            # Loud, once, at construction -- see the class docstring. `travel_diff` has no fallback.
            raise FeatureInputError(
                f"{len(missing)} game(s) in the history have no venue city (first few: "
                f"{sorted(missing)[:5]}). Travel is measured from the previous game\'s venue and a "
                "missing one would read as a home stand, so it is refused rather than defaulted."
            )

        history_teams = index.teams
        absent = sorted(history_teams - set(appearances))
        if absent:
            raise FeatureInputError(
                f"{len(absent)} team(s) in the history have no participation records (first few: "
                f"{absent[:5]}). A team missing here reads as league-average availability in every "
                "game it plays, which is a value the feature legitimately takes -- so it cannot be "
                "distinguished after the fact and is refused here instead."
            )

        self._history = index
        self._cities = dict(game_cities)
        self._elo = elo_module.Timeline(
            games, config=elo_config if elo_config is not None else elo_module.DEFAULT_CONFIG
        )
        self._availability = availability_module.AppearanceIndex(
            appearances,
            config=(
                availability_config
                if availability_config is not None
                else availability_module.DEFAULT_CONFIG
            ),
        )

    @property
    def history(self) -> GameHistory:
        return self._history

    @classmethod
    def of(cls, context: Context) -> Context:
        """Normalize the accepted context form. Idempotent, and refuses everything else.

        `GameHistory.of` accepted a raw sequence because a sequence *is* a complete history. A
        Context is not reconstructible from any one of its parts, so there is nothing to normalize
        from -- and accepting a bare history here would mean silently building a Context with no
        participation and no venues, which is the partial Context the class docstring refuses.
        """
        if type(context) is cls:
            return context
        if isinstance(context, Context):
            raise FeatureInputError(
                f"{type(context).__name__} subclasses Context, which is refused: overriding how a "
                "source is read is exactly what the as-of filter\'s single application point exists "
                "to prevent (F-050\'s rule, applied to the Context). Compose with a Context instead "
                "of inheriting from it."
            )
        raise FeatureInputError(
            f"compute_features takes a Context, got {type(context).__name__}. T-028 replaced the "
            "bare history argument: the v2 features read player participation and venues as well "
            "as games, and all three are filtered by one as-of moment. Build a Context (or use "
            "`store.load_context` / `dataset.context_from_frames`)."
        )


def compute_features(context: Context, target: Matchup, as_of: datetime) -> dict[str, float]:
    """The interface. Features for `target` using only information available strictly before `as_of`.

    Args:
        context: the sources, as a `Context`. Pass the whole corpus -- do NOT pre-filter it.
        target: the pre-game `Matchup`. Use `Game.matchup` for a completed game.
        as_of: the prediction moment, timezone-aware. Training: the target\'s own tip-off (prefer
            `compute_training_features`). Inference: the current moment, before tip-off.

    Returns:
        A dict keyed by exactly `FEATURE_NAMES`.

    Raises:
        FeatureInputError: naive `as_of`, wrong target type, a malformed or under-covering Context,
            or a venue this Context cannot resolve.
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

    context = Context.of(context)
    index = context._history
    # F-113: the completeness half of the guarantee, checked BEFORE anything is computed. An
    # incomplete history yields priors, and priors are what a legitimate opening night looks like.
    index._require_covers(target, as_of)

    destination = context._cities.get(target.game_id)
    if destination is None:
        raise FeatureInputError(
            f"game {target.game_id!r} has no venue city in this Context -- `travel_diff` and "
            "`altitude` are both read off the venue, and neither has a defensible default. Include "
            "the target\'s venue when building the Context."
        )

    home = index._records_before(target.home_id, as_of, target.game_id, target.away_id)
    away = index._records_before(target.away_id, as_of, target.game_id, target.home_id)

    home_rest = _rest_days(home, target.date)
    away_rest = _rest_days(away, target.date)

    return {
        "elo_diff": context._elo.difference_before(
            target.home_id,
            target.away_id,
            as_of,
            target.season,
            exclude_game_id=target.game_id,
        ),
        "home_b2b": 1.0 if home_rest == _B2B_REST_DAYS else 0.0,
        "away_b2b": 1.0 if away_rest == _B2B_REST_DAYS else 0.0,
        "rest_edge": float(home_rest - away_rest),
        "avail_diff": context._availability.difference_before(
            target.home_id, target.away_id, as_of, exclude_game_id=target.game_id
        ),
        "travel_diff": (
            _travel_miles(home, destination, target.season, context._cities)
            - _travel_miles(away, destination, target.season, context._cities)
        ),
        # An indicator on the venue, not a difference -- there is no "away altitude" to subtract.
        # Neutral sites are excluded because the feature\'s content is the *asymmetry*: at Denver the
        # visitor is the unacclimated side, which is a home-team advantage the model can price. At
        # Mexico City (7,350 ft, and in this corpus) both teams flew in, so the elevation affects
        # them equally and carries no information about who wins.
        "altitude": (
            1.0 if destination.is_high_altitude and not target.neutral_site else 0.0
        ),
    }


def compute_training_features(context: Context, game: Game) -> dict[str, float]:
    """Features for a completed game as of its own tip-off -- the training as-of rule, in code.

    Exists so no training loop ever picks an `as_of`. The rule ("at training time the as-of moment
    is the target game\'s own tip-off") is the other half of the anti-skew mechanism, and a rule
    stated in a plan is one a training loop can get wrong; a rule with no parameter is not.
    """
    return compute_features(context, game.matchup, game.date)


def to_vector(features: Mapping[str, float]) -> tuple[float, ...]:
    """Order a feature mapping into `FEATURE_NAMES` order for the estimator (T-009).

    Fails on a missing or unexpected key rather than filling a default: a silently-zero feature is
    the kind of defect that shows up as a mildly disappointing accuracy number and nothing else. For
    the same reason it refuses a non-finite value (F-049) -- a NaN reaching the estimator produces
    NaN coefficients, and the first place anyone would notice is a nonsense headline number.
    """
    missing = [name for name in FEATURE_NAMES if name not in features]
    unexpected = [name for name in features if name not in FEATURE_NAMES]
    if missing or unexpected:
        raise FeatureInputError(
            f"feature mapping does not match FEATURE_NAMES (missing={missing}, "
            f"unexpected={unexpected})"
        )
    vector = []
    for name in FEATURE_NAMES:
        value = features[name]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise FeatureInputError(
                f"feature {name!r} must be a real number, got {value!r} ({type(value).__name__})"
            )
        if not math.isfinite(value):
            raise FeatureInputError(f"feature {name!r} is {value!r}, which is not a finite number")
        vector.append(float(value))
    return tuple(vector)
