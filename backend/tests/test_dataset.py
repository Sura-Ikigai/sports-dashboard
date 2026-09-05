"""Tests for the loader-frame -> `Game` adapter (T-006, `backend/model/dataset.py`).

**These used to skip in CI and no longer do (T-022).** `dataset.py` needs pandas, and CI installed
`requirements.txt` only — so the `importorskip` below skipped this whole module there while it ran
locally, and the gap was recorded rather than papered over. `ci.yml` now installs
`requirements-train.txt` as well, so these actually run. D-016 is untouched: it is a decision about
the *served image*, and `backend/Dockerfile` still installs `requirements.txt` alone.

(The note this replaced pointed at `gate.yml`, which was deleted on 2026-08-24. `ci.yml` owns both
required status checks and always did.)

The `importorskip` stays as a local-convenience guard for a venv without the training deps. What
these tests buy is that the field mapping is pinned — a swapped `home_id`/`away_id` or
`home_score`/`away_score` would be invisible downstream, since both produce plausible numbers.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

pd = pytest.importorskip("pandas", reason="training-only dependency; not installed in CI (D-021)")

# Below the importorskip on purpose: importing model.dataset pulls in pandas, so these have to run
# after the skip, not before it. E402 is the rule that would otherwise object.
from model.corpus import CorpusIntegrityError  # noqa: E402
from model.dataset import (  # noqa: E402
    appearances_from_box_frame,
    context_from_frames,
    game_cities_from_frame,
    games_from_frame,
    load_games,
)
from model.features import (  # noqa: E402
    FEATURE_NAMES,
    FeatureInputError,
    compute_training_features,
)
from model.venues import VenueError  # noqa: E402


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


# --- the Context adapters (T-028) ------------------------------------------------------------------
#
# The pandas side of building a `features.Context`, mirroring `store.load_appearances` /
# `store.load_game_cities` on the Postgres side. Both exist because the v2 features read
# participation and venues as well as games, and both fail loudly on a venue they cannot place --
# a silent zero there reads as a home stand, which is a value travel legitimately takes.


def _box_frame(**overrides) -> pd.DataFrame:
    rows = [
        {
            "game_id": "401360941",
            "team_id": "19",
            "athlete_id": f"p{i}",
            "minutes": 30.0,
            "did_not_play": False,
        }
        for i in range(3)
    ]
    rows.append(
        {
            "game_id": "401360941",
            "team_id": "29",
            "athlete_id": "p9",
            "minutes": None,
            "did_not_play": True,
        }
    )
    frame = pd.DataFrame(rows)
    for column, value in overrides.items():
        frame[column] = value
    return frame


def _venue_frame(**overrides) -> pd.DataFrame:
    return _frame(**{"venue_city": "Boston", "venue_state": "MA", **overrides})


def test_appearances_are_keyed_by_team_and_dated_from_the_schedule():
    """The date comes from the *schedule*, never from the box frame's own column. `Game.date` is what
    every as-of comparison in the pipeline is made against, and a second source for one instant is a
    second thing that can drift."""
    date = datetime(2025, 1, 15, 19, 0, tzinfo=UTC)
    by_team = appearances_from_box_frame(_box_frame(), {"401360941": date})
    assert set(by_team) == {"19", "29"}
    assert [a.player_id for a in by_team["19"]] == ["p0", "p1", "p2"]
    assert all(a.date == date for rows in by_team.values() for a in rows)
    assert by_team["29"][0].did_not_play is True
    assert by_team["29"][0].minutes is None


def test_box_rows_for_games_outside_the_schedule_are_dropped():
    """The box files cover whole seasons while a caller may hold a curated subset (F-042's
    exhibitions). An appearance whose game is not in the history cannot be placed on the timeline."""
    assert appearances_from_box_frame(_box_frame(), {}) == {}


def test_box_rows_without_an_athlete_id_are_dropped():
    """33 real rows in the 2026 parquet have a null `athlete_id`. `ingest` pins that count per season
    at the boundary D-046 puts the guarantee on; this path deliberately does not re-pin it, because
    two copies of one pinned number is how they diverge."""
    frame = _box_frame()
    frame.loc[0, "athlete_id"] = None
    date = datetime(2025, 1, 15, 19, 0, tzinfo=UTC)
    assert len(appearances_from_box_frame(frame, {"401360941": date})["19"]) == 2


def test_a_box_frame_missing_a_column_is_refused():
    with pytest.raises(ValueError, match="missing column"):
        appearances_from_box_frame(_box_frame().drop(columns=["did_not_play"]), {})


def test_game_cities_resolve_through_the_static_venue_table():
    cities = game_cities_from_frame(_venue_frame())
    assert cities["401360941"].name == "Boston"
    assert cities["401360941"].is_high_altitude is False


def test_an_unknown_venue_city_is_refused_rather_than_defaulted():
    """`venues.city_for` has no fallback, and this is where that matters: an unrecognized venue fails
    at load, naming itself, instead of surfacing later as a game with mysteriously zero travel."""
    with pytest.raises(VenueError):
        game_cities_from_frame(_venue_frame(venue_city="Atlantis"))


def test_a_blank_venue_city_is_refused():
    with pytest.raises(ValueError, match="no venue city"):
        game_cities_from_frame(_venue_frame(venue_city=""))


def test_a_frame_without_the_venue_columns_is_refused():
    with pytest.raises(ValueError, match="missing column"):
        game_cities_from_frame(_frame())


def test_context_from_frames_assembles_all_three_sources():
    games = games_from_frame(_venue_frame())
    context = context_from_frames(games, _venue_frame(), _box_frame())
    assert context.history.teams == {"19", "29"}
    # The Context refuses a partial build, so getting one back is itself the assertion that all
    # three sources arrived.
    assert set(compute_training_features(context, games[0])) == set(FEATURE_NAMES)


def test_context_from_frames_refuses_a_game_the_schedule_does_not_cover():
    games = games_from_frame(_venue_frame())
    other = _venue_frame(game_id="999999999")
    with pytest.raises(ValueError, match="not in the schedule frame"):
        context_from_frames(games, other, _box_frame())
