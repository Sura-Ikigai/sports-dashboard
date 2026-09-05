"""The record types the whole modeling pipeline is expressed in.

Extracted from `features` in T-028, not invented here: `Matchup` and `Game` are T-006's types with
T-006's validation, moved so that the modules `features` now composes -- `elo`, `availability` --
can name them without importing `features` itself. Before this, `elo` imported `Game` from
`features`, which was backwards (the deep module depending on its consumer) and became a genuine
import cycle the moment `features` grew a `Context` that builds an `elo.Timeline`.

`features` re-exports everything here, so `from model.features import Game` continues to mean what
it always meant and no caller has to know the split happened.

Standard library only, like everything it is imported by (D-021/D-016).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
