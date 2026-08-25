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
from pathlib import Path
from unittest.mock import patch

import pytest

pd = pytest.importorskip("pandas", reason="training-only dependency; not installed in CI (D-021)")

# Below the importorskip on purpose: importing model.dataset pulls in pandas, so these have to run
# after the skip, not before it. E402 is the rule that would otherwise object.
from model.corpus import CorpusIntegrityError  # noqa: E402
from model.dataset import games_from_frame, load_games  # noqa: E402
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


def _synthetic_frame(seasons=(2024,), teams=30, rounds=2, all_star=1) -> pd.DataFrame:
    """A frame shaped like the loader's output: a full round-robin plus All-Star games."""
    rows, n = [], 0
    for season in seasons:
        ids = [f"t{i:02d}" for i in range(teams)]
        for r in range(rounds):
            for i in range(teams):
                for j in range(i + 1, teams):
                    home, away = (ids[i], ids[j]) if r % 2 == 0 else (ids[j], ids[i])
                    rows.append({
                        "game_id": f"s{season}g{n}", "date": pd.Timestamp("2024-01-01T19:00:00Z") + pd.Timedelta(hours=n),
                        "season": season, "season_type": 2, "home_id": home, "away_id": away,
                        "home_score": 110, "away_score": 100, "neutral_site": False, "home_win": True,
                    })
                    n += 1
        for k in range(all_star):
            rows.append({
                "game_id": f"s{season}as{k}", "date": pd.Timestamp("2024-03-01T19:00:00Z") + pd.Timedelta(hours=k),
                "season": season, "season_type": 2, "home_id": f"ph{season}a{k}", "away_id": f"ph{season}b{k}",
                "home_score": 150, "away_score": 148, "neutral_site": False, "home_win": True,
            })
    return pd.DataFrame(rows)


def test_load_games_excludes_exhibitions_by_default():
    """F-061 — `load_games` had NO tests, so D-025(3)'s exclude-by-default was unpinned: flipping the
    default to True, or inverting the flag, passed all 90 tests. Under either, `load_games()` returns
    the contaminated corpus, D-026's baseline silently shifts, and coin-flip rows reach T-009. This is
    F-059's pattern — an unexercised default — landing on the one safety property the module exists
    to provide."""
    frame = _synthetic_frame(all_star=1)  # matches the 2024 pin, so curation verifies cleanly
    with patch("model.dataset.load_completed_games", return_value=frame):
        default = load_games()
        explicit_raw = load_games(include_exhibitions=True)

    assert len(explicit_raw) == len(frame)                       # raw keeps everything
    assert len(default) == len(frame) - 1                        # default drops the exhibition
    assert default is not explicit_raw
    # the curated set contains exactly the 30 franchises, no phantom ids
    ids = {g.home_id for g in default} | {g.away_id for g in default}
    assert len(ids) == 30 and not any(t.startswith("ph") for t in ids)
    # ...and the raw one does carry them, so the assertion above is not vacuous
    raw_ids = {g.home_id for g in explicit_raw} | {g.away_id for g in explicit_raw}
    assert any(t.startswith("ph") for t in raw_ids)


def test_load_games_verifies_curation_rather_than_trusting_it():
    """A season whose exhibition count does not match the pin must fail, not pass quietly."""
    frame = _synthetic_frame(seasons=(2024,), all_star=3)  # pin for 2024 is 1
    with patch("model.dataset.load_completed_games", return_value=frame):
        with pytest.raises(CorpusIntegrityError, match="do not match the pinned expected"):
            load_games()


def test_load_games_passes_its_arguments_through_to_the_loader():
    """F-090 (HIGH) — `load_games` ignoring BOTH its arguments survived the whole suite, because the
    fixture patched `load_completed_games` with `return_value=`, which answers any argument list, and
    nothing inspected `call_args`.

    The consequence is the one this project exists to prevent: T-007 builds expanding-window folds
    *by season*, so a `load_games` that ignores `seasons` would have every fold train on its own test
    season — T-007's own fold tests would still pass, and the only symptom would be a better-looking
    accuracy number.
    """
    frame = _synthetic_frame(seasons=(2023, 2024), all_star=1)  # matches both pins (2023:1, 2024:1)
    data_dir = Path("/tmp/some-data-dir")
    with patch("model.dataset.load_completed_games", return_value=frame) as loader:
        load_games((2023, 2024), data_dir)
    # F-094: exact binding, not membership. The previous version built a set of the call's values and
    # checked each appeared SOMEWHERE, so swapped arguments passed -- and it used a single-element
    # tuple, which made a truncating mutation (`seasons[:1]`) undetectable by construction. A
    # multi-season tuple is the point: T-007 builds folds by season.
    loader.assert_called_once_with((2023, 2024), data_dir)
