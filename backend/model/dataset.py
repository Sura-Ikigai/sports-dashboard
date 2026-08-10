"""Loader frame -> `Game` records (T-006). The one place pandas meets the feature pipeline.

`features.py` is standard-library only, on purpose and for two binding reasons (see its module
docstring): CI installs `requirements.txt` without pandas, and D-016 has Phase 2 importing the
feature function inside the FastAPI service. `loader.py` is unavoidably pandas. This module is the
seam between them, and it exists so that the pressure to "just import pandas in features.py" -- which
would break both -- never arises.

Training-only, like the loader: never imported by the served image.
"""

from pathlib import Path

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
    return [
        Game(
            game_id=str(row.game_id),
            date=row.date.to_pydatetime(),
            season=int(row.season),
            home_id=str(row.home_id),
            away_id=str(row.away_id),
            home_score=int(row.home_score),
            away_score=int(row.away_score),
            neutral_site=bool(row.neutral_site),
        )
        for row in frame.itertuples(index=False)
    ]


def load_games(
    seasons: tuple[int, ...] = SEASONS, data_dir: Path = DEFAULT_DATA_DIR
) -> list[Game]:
    """Load the pinned seasons through the loader (with all its verification) as `Game` records."""
    return games_from_frame(load_completed_games(seasons, data_dir))
