"""Tests for the loader-frame -> `Game` adapter (T-006, `backend/model/dataset.py`).

**These skip in CI, by construction.** `dataset.py` needs pandas, and `gate.yml` installs
`requirements.txt` only (D-021) — so `importorskip` below skips the whole module there and runs it
locally, where `requirements-train.txt` is installed. That is a real coverage gap and is recorded as
one rather than papered over: it is the same category as PLAN-v1's deliberate decision not to unit
test `loader.py`, and the adapter is thin enough that its actual risk is the loader's frame changing
shape, which no committed fixture can detect anyway. What these tests do buy is that the field
mapping is pinned — a swapped `home_id`/`away_id` or `home_score`/`away_score` would be invisible
downstream (both produce plausible numbers) and is caught here.
"""

from datetime import UTC, datetime

import pytest

pd = pytest.importorskip("pandas", reason="training-only dependency; not installed in CI (D-021)")

# Below the importorskip on purpose: importing model.dataset pulls in pandas, so these have to run
# after the skip, not before it. E402 is the rule that would otherwise object.
from model.dataset import games_from_frame  # noqa: E402
from model.features import FeatureInputError  # noqa: E402


def _frame(**overrides) -> pd.DataFrame:
    row = {
        "game_id": "401360941",
        "date": pd.Timestamp("2025-01-15T19:00:00Z"),
        "season": 2025,
        "season_type": 2,
        "home_id": "19",
        "away_id": "29",
        "home_score": 118,
        "away_score": 111,
        "neutral_site": False,
        "home_win": True,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_every_field_maps_to_the_right_place():
    (game,) = games_from_frame(_frame())
    assert game.game_id == "401360941"
    assert game.date == datetime(2025, 1, 15, 19, 0, tzinfo=UTC)
    assert game.season == 2025
    assert game.home_id == "19"
    assert game.away_id == "29"
    assert game.home_score == 118
    assert game.away_score == 111
    assert game.neutral_site is False
    assert game.home_win is True  # derived from the scores, never read from the frame's column


def test_the_dates_it_produces_are_timezone_aware():
    """`Game` refuses naive datetimes, so this is really a check that the loader's `utc=True`
    parsing survives the `to_pydatetime` conversion."""
    (game,) = games_from_frame(_frame())
    assert game.date.tzinfo is not None


def test_neutral_site_survives_as_a_real_bool():
    (game,) = games_from_frame(_frame(neutral_site=True))
    assert game.neutral_site is True


def test_a_missing_column_is_refused_by_name():
    with pytest.raises(ValueError, match=r"missing column\(s\) \['home_score'\]"):
        games_from_frame(_frame().drop(columns=["home_score"]))


def test_game_level_validation_fires_during_conversion():
    """A frame the loader verified can still be unusable — conversion is where that surfaces."""
    with pytest.raises(FeatureInputError, match="tied"):
        games_from_frame(_frame(home_score=110, away_score=110))


def test_conversion_preserves_row_order_and_count():
    frame = pd.concat([_frame(game_id="a"), _frame(game_id="b"), _frame(game_id="c")])
    assert [g.game_id for g in games_from_frame(frame)] == ["a", "b", "c"]


def test_season_is_read_from_the_frame_not_assumed():
    """F-056 — the adapter hardcoding `season=2022` passed the whole suite, because `_frame()`
    happened to use 2022. Season drives both accumulating features, so a wrong one changed 5,283 of
    the 6,615 real vectors. Two rows, two seasons, both asserted."""
    frame = pd.concat([_frame(game_id="a", season=2023), _frame(game_id="b", season=2026)])
    assert [g.season for g in games_from_frame(frame)] == [2023, 2026]


def test_a_non_bool_neutral_site_is_refused_rather_than_coerced():
    """F-048 — `bool("False")` is True, which would invert the flag on every row and silently make
    `home_advantage` a constant 0.0."""
    with pytest.raises(ValueError, match="neutral_site` must be a bool"):
        games_from_frame(_frame(neutral_site="False"))


def test_a_non_integer_score_is_refused_rather_than_truncated():
    """F-048 — `int(118.9)` silently became 118."""
    with pytest.raises(ValueError, match="home_score` must be an integer"):
        games_from_frame(_frame(home_score=118.9))


def test_an_unparsed_date_column_fails_with_a_useful_error():
    """F-048 — this used to die with a bare AttributeError from inside a comprehension."""
    with pytest.raises(ValueError, match="must be a pandas Timestamp"):
        games_from_frame(_frame(date="2025-01-15T19:00:00Z"))


def test_duplicate_game_ids_across_the_frame_are_refused():
    """F-046 second layer — `load_games((2022, 2022))` returned 2,648 records with 1,324 unique ids
    and no error. GameHistory protects the features; this protects the count."""
    frame = pd.concat([_frame(game_id="dup"), _frame(game_id="dup")])
    with pytest.raises(ValueError, match="duplicate game_id"):
        games_from_frame(frame)
