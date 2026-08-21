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

2. **The target's outcome is closed off on both routes into this module.** The target is a `Matchup`
   -- game id, date, season, the two team ids, neutral-site flag -- carrying no scores at all, and a
   completed `Game` cannot be passed as the target; `Game.matchup` is the deliberate one-way door.
   That shuts the *direct* route. It does **not** by itself shut the other one: the scores also live
   in `history`, and the target's own `Game` is kept out of its own features only because the strict
   as-of filter excludes a game dated at `as_of`. That held only while `as_of == target.date` -- a
   property of the caller's data, not of this module (F-044: with `as_of` one hour after tip-off and
   the target in history, its own 200-80 result moved `point_diff_diff` from 6.0 to 32.0). So the
   window is now *also* filtered by `game_id != target.game_id`, which is the check that makes this
   claim true independently of what the caller passes.

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

## Precondition this module cannot check for itself (F-047)

`history` must contain only **final** games. `Game.date` is **tip-off**, not completion, and a `Game`
carries no status field -- so "games completed strictly before `as_of`" is enforced here as "games
that tipped off strictly before `as_of`". In Phase 1 the two coincide: the loader admits only
`status_type_completed` rows, and the closest pair of consecutive games for any real team in the
corpus is 23 hours apart (the sub-12h gaps all belong to F-042's All-Star phantom ids). It stops
coinciding in Phase 2, where D-011 puts one `games` table behind both training and inference and the
app's status enum is `scheduled | live | final`: a caller that forgets `status == 'final'` would feed
a live partial score into a "pre-game" vector, and this module would accept it. Filter before you get
here.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
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


def _require_key_types(game_id: str, season: object, home_id: object, away_id: object, label: str) -> None:
    """Reject key fields whose *type* would silently miss the index rather than raise (F-045).

    A season of `"2024"` against a history of `2024`, or team ids typed `int` on one side and `str`
    on the other, look like perfectly ordinary inputs and produce a perfectly ordinary answer: every
    lookup misses, every feature falls back to its prior, and the result is byte-identical to what
    opening night legitimately produces. D-015 removed the only symptom that would otherwise have
    exposed it (dropped early-season games), so nothing downstream can tell "this team has no prior
    games" from "the key never matched". T-009 would read the resulting all-priors matrix as *"four
    pre-game features carry no signal -- no-ship"* rather than *"the pipeline is broken"* -- the
    exact silent failure PLAN-v1 exists to design out. Cheapest place to close it is at construction,
    where the type is still visible.
    """
    # bool is a subclass of int; a `True` season is a bug, not a season.
    if not isinstance(season, int) or isinstance(season, bool):
        raise FeatureInputError(
            f"{label} season must be an int, got {season!r} ({type(season).__name__}). A season that "
            "does not match the history's type misses every lookup and silently returns priors."
        )
    for role, team_id in (("home_id", home_id), ("away_id", away_id)):
        if not isinstance(team_id, str):
            raise FeatureInputError(
                f"{label} {role} must be a str, got {team_id!r} ({type(team_id).__name__}). Team ids "
                "are ESPN string ids throughout; a mistyped one misses every lookup silently."
            )
    if not isinstance(game_id, str):
        raise FeatureInputError(
            f"{label} game_id must be a str, got {game_id!r} ({type(game_id).__name__})"
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
        _require_key_types(self.game_id, self.season, self.home_id, self.away_id, "matchup")
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
        _require_key_types(self.game_id, self.season, self.home_id, self.away_id, "game")
        if self.home_id == self.away_id:
            raise FeatureInputError(
                f"game {self.game_id!r} has the same team ({self.home_id!r}) on both sides"
            )
        for role, score in (("home_score", self.home_score), ("away_score", self.away_score)):
            # F-049: the tie check below compares scores, and `nan != nan`, so a NaN score sails
            # through it and then propagates a NaN straight into the feature vector. Checked before
            # the tie test for exactly that reason. bool excluded: `True` is not a score.
            if not isinstance(score, int) or isinstance(score, bool):
                raise FeatureInputError(
                    f"game {self.game_id!r} {role} must be an int, got {score!r} "
                    f"({type(score).__name__}) -- a non-integral score is corrupt input, and a NaN "
                    "one would pass the tie check below and poison the feature vector"
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
    opponent_id: str


@dataclass(frozen=True)
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

    __slots__ = ("_coverage", "_dates", "_records")

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

        if coverage is not None and not isinstance(coverage, Coverage):
            raise FeatureInputError(
                f"coverage must be a Coverage or None, got {type(coverage).__name__}"
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

    Only an upper clamp: `records` is filtered to dates strictly before `as_of`, and `as_of` is
    refused past `tip_off`, so the gap is always positive. A lower `max(..., 0.0)` used to sit here
    and was unreachable (F-060) -- dead defensive code reads as a guarded path and hides that the
    invariant, not the clamp, is what holds.
    """
    if not records:
        return MAX_REST_DAYS
    gap_days = (tip_off - records[-1].date).total_seconds() / _SECONDS_PER_DAY
    return min(gap_days, MAX_REST_DAYS)


def compute_features(
    history: GameHistory | Sequence[Game], target: Matchup, as_of: datetime
) -> dict[str, float]:
    """The interface. Features for `target` using only games completed strictly before `as_of`.

    Args:
        history: every completed game available, as a **sequence** of `Game` records (list or
            tuple -- not a generator, see F-045) or a prebuilt `GameHistory`. Pass the full
            collection -- do NOT pre-filter it. Filtering is this module's job, and a caller
            that filters is a caller that can filter wrongly. Must contain only final games
            (see the module docstring's precondition) and no duplicate `game_id`.
            **If you must narrow it, say so** (F-113): build a `GameHistory` with an explicit
            `Coverage` and this function will refuse a target the declaration does not cover.
            A raw sequence declares nothing, so it is taken at its word -- which is why the
            instruction above is still an instruction and the declaration is the check.
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
    # F-113: the completeness half of the guarantee, checked BEFORE anything is computed. An
    # incomplete history yields priors, and priors are what a legitimate opening night looks like.
    index._require_covers(target, as_of)
    home = index._records_before(target.home_id, as_of, target.game_id, target.away_id)
    away = index._records_before(target.away_id, as_of, target.game_id, target.home_id)

    return {
        "home_advantage": 0.0 if target.neutral_site else 1.0,
        "form_diff": _form(home, target.season) - _form(away, target.season),
        "rest_diff": _rest_days(home, target.date) - _rest_days(away, target.date),
        "point_diff_diff": (
            _season_point_diff(home, target.season) - _season_point_diff(away, target.season)
        ),
    }


def compute_training_features(
    history: GameHistory | Sequence[Game], game: Game
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
