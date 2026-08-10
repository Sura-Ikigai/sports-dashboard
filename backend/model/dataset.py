"""Loader frame -> `Game` records (T-006). The one place pandas meets the feature pipeline.

`features.py` is standard-library only, on purpose and for two binding reasons (see its module
docstring): CI installs `requirements.txt` without pandas, and D-016 has Phase 2 importing the
feature function inside the FastAPI service. `loader.py` is unavoidably pandas. This module is the
seam between them, and it exists so that the pressure to "just import pandas in features.py" -- which
would break both -- never arises.

Training-only, like the loader: never imported by the served image.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from .features import Game
from .loader import DEFAULT_DATA_DIR, SEASONS, load_completed_games

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
    seasons: tuple[int, ...] = SEASONS, data_dir: Path = DEFAULT_DATA_DIR
) -> list[Game]:
    """Load the pinned seasons through the loader (with all its verification) as `Game` records."""
    return games_from_frame(load_completed_games(seasons, data_dir))
