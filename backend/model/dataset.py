"""Loader frame -> `Game` records (T-006). The one place pandas meets the feature pipeline.

`features.py` is standard-library only, on purpose and for two binding reasons (see its module
docstring): CI installs `requirements.txt` without pandas, and D-016 has Phase 2 importing the
feature function inside the FastAPI service. `loader.py` is unavoidably pandas. This module is the
seam between them, and it exists so that the pressure to "just import pandas in features.py" -- which
would break both -- never arises.

Training-only, like the loader: never imported by the served image.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .availability import Appearance
from .corpus import apply_default_curation
from .features import Context, Game
from .loader import DEFAULT_DATA_DIR, SEASONS, load_completed_games
from .venues import City, city_for

# Exactly the columns `loader._read_completed_games` emits that a pre-game feature needs. `home_win`
# is deliberately absent -- `Game` derives it from the scores, so there is one definition of who won.
REQUIRED_FRAME_COLUMNS: tuple[str, ...] = (
    "game_id",
    "date",
    "season",
    "home_id",
    "away_id",
    "home_score",
    "away_score",
    "neutral_site",
)


def games_from_frame(frame: pd.DataFrame) -> list[Game]:
    """Convert the loader's normalized completed-game frame into `Game` records.

    A field-by-field conversion with no reinterpretation: every validation that matters (tied score,
    naive date, a team playing itself) belongs to `Game.__post_init__` and fires here, so a frame the
    loader verified but that is nonetheless unusable fails at conversion rather than silently
    producing features. The frame's `date` column is tz-aware (`loader` parses with `utc=True`), and
    `Game` refuses it if it ever stops being.
    """
    missing = [c for c in REQUIRED_FRAME_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(
            f"frame is missing column(s) {missing} -- expected the normalized completed-game frame "
            f"from loader.load_season / loader.load_completed_games"
        )
    games = [_game_from_row(row) for row in frame.itertuples(index=False)]

    # F-046, second layer. `GameHistory.__init__` also refuses duplicates, and that is the check that
    # protects the features. This one protects the *count*: without it `load_games((2022, 2022))`
    # returns 2,648 records with 1,324 unique ids and no complaint, and anything that reports "N
    # games evaluated" — T-009's headline, for one — would quote a doubled number that never reaches
    # a GameHistory. Same reasoning as T-005 verifying at both the download and the cached path.
    seen: set[str] = set()
    duplicates = sorted({g.game_id for g in games if g.game_id in seen or seen.add(g.game_id)})
    if duplicates:
        raise ValueError(
            f"frame contains {len(duplicates)} duplicate game_id(s) (first few: {duplicates[:5]}) -- "
            "a repeated season in the requested list, or a frame concatenated twice. T-005 verifies "
            "game_id uniqueness per season; nothing verified it across the combined collection."
        )
    return games


def _game_from_row(row) -> Game:
    """One frame row -> one `Game`, checking types rather than coercing them (F-048).

    The original version used bare `bool()` / `int()` / `str()`, which is not "no reinterpretation" --
    it is silent reinterpretation. `bool("False")` is `True`, so a `neutral_site` column of strings
    would have inverted the flag on every row, making `home_advantage` a constant 0.0 and quietly
    deleting the model's only guaranteed feature; `int(118.9)` truncates a float score to 118; and a
    plain-`str` date column died with a bare `AttributeError` from inside a comprehension.

    Nothing was wrong at the time this was written -- the loader emits real bools via `== "true"` and
    real ints via `.astype(int)`. But this module is the trust boundary between third-party-derived
    data and a pipeline whose failures are silent by construction, so it should assert the shape it
    claims to depend on rather than accept whatever coerces.
    """
    if not hasattr(row.date, "to_pydatetime"):
        raise ValueError(
            f"game {row.game_id!r}: `date` must be a pandas Timestamp, got {type(row.date).__name__}"
            " -- the loader parses this column with utc=True; an unparsed column means the frame did"
            " not come from it"
        )
    if not isinstance(row.neutral_site, (bool, np.bool_)):
        # The one that matters most: truthiness would silently invert it (`bool("False") is True`).
        raise ValueError(
            f"game {row.game_id!r}: `neutral_site` must be a bool, got "
            f"{row.neutral_site!r} ({type(row.neutral_site).__name__})"
        )
    for field in ("home_score", "away_score", "season"):
        value = getattr(row, field)
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
            raise ValueError(
                f"game {row.game_id!r}: `{field}` must be an integer, got "
                f"{value!r} ({type(value).__name__})"
            )
    return Game(
        game_id=str(row.game_id),
        # to_pydatetime() truncates sub-microsecond precision; the pinned corpus is minute-precision
        # so nothing is lost today, and F-044's game_id exclusion now covers the case where it is.
        date=row.date.to_pydatetime(),
        season=int(row.season),
        home_id=str(row.home_id),
        away_id=str(row.away_id),
        home_score=int(row.home_score),
        away_score=int(row.away_score),
        neutral_site=bool(row.neutral_site),
    )


def load_games(
    seasons: tuple[int, ...] = SEASONS,
    data_dir: Path = DEFAULT_DATA_DIR,
    *,
    include_exhibitions: bool = False,
) -> list[Game]:
    """Load the pinned seasons through the loader (with all its verification) as `Game` records.

    **Excludes All-Star exhibition games by default** (F-042): the source mixes 10 of them into the
    2022-2026 schedules as `season_type = 2`, so the verified corpus is 6,615 games and the modeling
    corpus is **6,605**. Curation lives in `corpus.exclude_exhibitions`, which verifies the result
    rather than trusting it.

    The default is exclusion on purpose. The two populations are indistinguishable downstream -- an
    All-Star row has ordinary-looking features and a coin-flip label -- so a caller who forgets the
    flag gets a quietly contaminated evaluation and no symptom. The safe set is the one you get
    without asking; `include_exhibitions=True` returns the raw verified corpus, and is for auditing
    the loader's pinned counts (which *do* include them, deliberately -- see `corpus`), not for
    modeling.
    """
    games = games_from_frame(load_completed_games(seasons, data_dir))
    # The policy itself lives in `corpus.apply_default_curation` (stdlib), so the gate can test the
    # behaviour rather than this signature's default — see F-092/F-093. This function holds no
    # curation policy of its own.
    return apply_default_curation(games, include_exhibitions=include_exhibitions)


# Exactly the player-box columns `availability.Appearance` is made of, plus the join keys. Kept
# separate from `loader.PLAYER_BOX_REQUIRED_COLUMNS` on purpose: that constant says what the *source
# file* must contain and is a verification pin; this one says what this conversion reads. They
# overlap today and are allowed to diverge, which is the point of not sharing one.
REQUIRED_BOX_COLUMNS: tuple[str, ...] = (
    "game_id",
    "team_id",
    "athlete_id",
    "minutes",
    "did_not_play",
)

REQUIRED_VENUE_COLUMNS: tuple[str, ...] = ("game_id", "venue_city", "venue_state")


def appearances_from_box_frame(
    frame: pd.DataFrame, dates: Mapping[str, datetime]
) -> dict[str, list[Appearance]]:
    """Loader box frame + game dates -> the participation map a `Context` takes.

    `dates` comes from the *schedule*, never from the box frame's own `game_date` column, even
    though that column exists and agrees today. `Game.date` is what every other as-of comparison in
    the pipeline is made against, and a second source for the same instant is a second thing that can
    drift -- the rule `store.load_player_box` already applies to `season`.

    Box rows for games outside `dates` are dropped rather than dated from elsewhere: the box files
    cover whole seasons while a caller may hold a curated subset (F-042's exhibitions, for one), and
    an appearance whose game is not in the history cannot be positioned on the timeline at all.

    Rows with no `athlete_id` are dropped. The corpus has 33 of them, all in 2026, and `ingest` pins
    that count per season so a change is caught at the boundary D-046 puts the guarantee on. This
    path deliberately does not re-pin it: two copies of one pinned number is how they diverge.
    """
    missing = [c for c in REQUIRED_BOX_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(
            f"box frame is missing column(s) {missing} -- expected the frame from loader.load_box"
        )

    by_team: dict[str, list[Appearance]] = {}
    for row in frame.itertuples(index=False):
        date = dates.get(str(row.game_id))
        if date is None:
            continue
        athlete_id = row.athlete_id
        if athlete_id is None or (isinstance(athlete_id, float) and math.isnan(athlete_id)):
            continue
        minutes = row.minutes
        if minutes is not None and isinstance(minutes, float) and math.isnan(minutes):
            minutes = None
        by_team.setdefault(str(row.team_id), []).append(
            Appearance(
                game_id=str(row.game_id),
                date=date,
                player_id=str(athlete_id),
                minutes=None if minutes is None else float(minutes),
                did_not_play=bool(row.did_not_play),
            )
        )
    return by_team


def game_cities_from_frame(frame: pd.DataFrame) -> dict[str, City]:
    """Loader schedule frame -> `game_id` -> `venues.City`, the D-036 join on the pandas side.

    The mirror of `store.load_game_cities`, and it fails the same way: `city_for` has no fallback,
    so an unrecognized venue city raises here, naming itself, rather than surfacing as a game with
    inexplicably zero travel. A blank city is refused for the same reason.
    """
    missing = [c for c in REQUIRED_VENUE_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(
            f"frame is missing column(s) {missing} -- T-023 added the venue columns to "
            "loader._read_completed_games; a frame without them predates it"
        )
    cities: dict[str, City] = {}
    unlocated: list[str] = []
    for row in frame.itertuples(index=False):
        city = row.venue_city
        if city is None or (isinstance(city, float) and math.isnan(city)) or not str(city).strip():
            unlocated.append(str(row.game_id))
            continue
        state = row.venue_state
        if state is not None and isinstance(state, float) and math.isnan(state):
            state = None
        cities[str(row.game_id)] = city_for(str(city), None if state is None else str(state))
    if unlocated:
        raise ValueError(
            f"{len(unlocated)} game(s) have no venue city (first few: {unlocated[:5]}) -- travel "
            "and altitude are read off the venue and neither has a defensible default"
        )
    return cities


def context_from_frames(
    games: Sequence[Game], schedule: pd.DataFrame, box: pd.DataFrame
) -> Context:
    """Assemble a `Context` from the loader's frames -- the pandas counterpart of
    `store.load_context`.

    `games` is passed separately rather than re-derived from `schedule` because curation is a
    decision (F-042/F-092), and the two callers of this function want different answers: an
    evaluation wants the curated corpus, an audit wants the raw one.

    `schedule` is still required, and still only for its dates -- the game ids in `games` must all
    appear in it, which is the check that a caller has not paired a curated corpus with the wrong
    frame. T-030 removed the venue join; `game_cities_from_frame` remains for callers that want to
    *display* travel or elevation, but no feature reads one.
    """
    dates = {game.game_id: game.date for game in games}
    known = set(schedule["game_id"].astype(str))
    absent = [game_id for game_id in dates if game_id not in known]
    if absent:
        raise ValueError(
            f"{len(absent)} game(s) are not in the schedule frame (first few: {absent[:5]})"
        )
    return Context(list(games), appearances_from_box_frame(box, dates))
