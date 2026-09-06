"""T-032 -- the track record: what was predicted, what happened, and how often the model was right.

Four claims carry this file and the rest is plumbing:

  1. **The last prediction before tip-off is the one scored.** D-012 keeps seven statements per game
     and exactly one of them is the model's final word. Getting this wrong changes the reported
     accuracy with no symptom, so it is a pure function with its own test *and* a control proving the
     same prediction is kept when it is dated before tip-off rather than after.
  2. **A band with nothing in it reports no hit rate, not a hit rate of zero.** `0.0` and "nothing to
     report" render identically as `0%`, and one of them is a lie.
  3. **Bands are taken on the favoured side.** A .75 home probability and a .25 home probability are
     the same call at the same confidence. Filing them apart would make user story 21 unanswerable.
  4. **No date comparison reaches the database.** D-039's rule in the narrower form this module can
     satisfy -- see `test_no_date_comparison_reaches_the_database`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from conftest import alembic_config, executable_strings, make_scratch_database
from model import track_record as tr
from model.track_record import (
    BANDS,
    PredictionRecord,
    ScheduledGame,
    TrackRecordError,
    band_for,
    confidence,
    last_before_tip_off,
    score,
    wilson_interval,
)

SCRATCH_DB = "t032_track_record_test"
MODULE_PATH = Path(tr.__file__)

V1 = "aaaaaaaaaaaa"
V2 = "bbbbbbbbbbbb"
TIP = datetime(2026, 10, 20, 23, 0, tzinfo=UTC)


def _prediction(game_id: str, probability: float, *, as_of: datetime, version: str = V1):
    return PredictionRecord(
        game_id=game_id, model_version=version, as_of=as_of, home_win_probability=probability
    )


def _game(game_id: str, *, home: int | None = None, away: int | None = None,
          status: str = "STATUS_FINAL", date: datetime = TIP) -> ScheduledGame:
    return ScheduledGame(
        game_id=game_id, season=2027, date=date, home_id="1", away_id="2",
        neutral_site=False, status=status, home_score=home, away_score=away,
    )


# ── bands ─────────────────────────────────────────────────────────────────────


def test_the_bands_tile_the_confidence_range_with_no_gap_and_no_overlap():
    """`band_for` raises rather than defaulting when a probability falls through, so a hole in the
    tiling is a loud failure -- but only if there is no hole. This is what proves there is not."""
    for i in range(500, 1001):
        value = i / 1000
        matching = [b for b in BANDS if b.contains(value)]
        assert len(matching) == 1, f"confidence {value} matched {[b.slug for b in matching]}"


def test_a_hole_in_the_tiling_is_refused_rather_than_defaulted(monkeypatch):
    """The non-vacuity control for the check above: with a band removed, the probabilities it used
    to hold must raise. A `band_for` that fell back to a default would pass the tiling test and
    quietly file games into the wrong bucket forever."""
    monkeypatch.setattr(tr, "BANDS", tuple(b for b in BANDS if b.slug != "clear"))
    with pytest.raises(TrackRecordError, match="tile"):
        band_for(0.70)


@pytest.mark.parametrize("probability", [0.5, 0.54, 0.55, 0.649, 0.65, 0.74, 0.75, 0.99, 1.0])
def test_a_band_is_the_same_whichever_side_the_model_favours(probability):
    """A .75 home probability and a .25 home probability are one call at one confidence, pointing
    opposite ways. Banding on the raw home probability would file them apart and make "how often is
    the model right at this confidence" unanswerable (user story 21)."""
    assert band_for(probability) is band_for(1.0 - probability)


@pytest.mark.parametrize(
    ("probability", "slug"),
    [(0.50, "toss-up"), (0.5499, "toss-up"), (0.55, "lean"), (0.6499, "lean"),
     (0.65, "clear"), (0.7499, "clear"), (0.75, "strong"), (1.0, "strong"),
     (0.45, "lean"), (0.30, "clear"), (0.20, "strong")],
)
def test_band_boundaries(probability, slug):
    assert band_for(probability).slug == slug


@pytest.mark.parametrize("probability", [-0.001, 1.001, 2.0])
def test_a_probability_outside_the_unit_interval_is_refused(probability):
    with pytest.raises(TrackRecordError, match="outside"):
        confidence(probability)


# ── the scoring rule ──────────────────────────────────────────────────────────


def test_the_last_prediction_before_tip_off_is_the_one_scored():
    held = [
        _prediction("g", 0.55, as_of=TIP - timedelta(days=3)),
        _prediction("g", 0.80, as_of=TIP - timedelta(hours=2)),
        _prediction("g", 0.60, as_of=TIP - timedelta(days=1)),
    ]
    chosen = last_before_tip_off(held, TIP)
    assert chosen is not None
    assert chosen.home_win_probability == 0.80


def test_a_prediction_dated_after_tip_off_is_never_the_one_scored():
    """There should be none -- `load_upcoming` returns games strictly after `as_of` and
    `compute_features` refuses an as-of past tip-off (D-010). Filtering for them here anyway is this
    function's own definition of what it computes, not dead defensive code (F-060)."""
    held = [
        _prediction("g", 0.80, as_of=TIP - timedelta(hours=2)),
        _prediction("g", 0.05, as_of=TIP + timedelta(hours=1)),
    ]
    chosen = last_before_tip_off(held, TIP)
    assert chosen is not None
    assert chosen.home_win_probability == 0.80


def test_the_same_prediction_is_kept_when_it_is_dated_before_tip_off():
    """The control for the test above. Without it, a filter that dropped *everything* would pass."""
    held = [
        _prediction("g", 0.80, as_of=TIP - timedelta(hours=2)),
        _prediction("g", 0.05, as_of=TIP - timedelta(minutes=1)),
    ]
    chosen = last_before_tip_off(held, TIP)
    assert chosen is not None
    assert chosen.home_win_probability == 0.05


def test_a_prediction_made_at_the_instant_of_tip_off_counts():
    """`compute_features` refuses only `as_of > target.date`, so a prediction made at exactly tip-off
    is one the scoring path produced. Excluding it here would disagree with the code that made it."""
    held = [_prediction("g", 0.70, as_of=TIP)]
    assert last_before_tip_off(held, TIP) is not None


def test_nothing_to_score_returns_none_rather_than_raising():
    assert last_before_tip_off([], TIP) is None
    assert last_before_tip_off([_prediction("g", 0.7, as_of=TIP + timedelta(days=1))], TIP) is None


# ── the record ────────────────────────────────────────────────────────────────


def test_a_correct_call_on_each_side_is_counted_correct():
    record = score(
        [
            _prediction("home-win", 0.80, as_of=TIP - timedelta(hours=1)),
            _prediction("away-win", 0.20, as_of=TIP - timedelta(hours=1)),
        ],
        [_game("home-win", home=110, away=100), _game("away-win", home=95, away=105)],
        model_version=V1,
    )
    assert record.games == 2
    assert record.correct == 2
    assert record.accuracy == 1.0


def test_a_wrong_call_is_counted_wrong():
    """The control. Without it a `correct` that always returned True would pass the test above."""
    record = score(
        [_prediction("g", 0.80, as_of=TIP - timedelta(hours=1))],
        [_game("g", home=95, away=105)],
        model_version=V1,
    )
    assert (record.games, record.correct) == (1, 0)
    assert record.accuracy == 0.0


def test_a_game_with_no_result_is_pending_rather_than_scored():
    record = score(
        [_prediction("g", 0.80, as_of=TIP - timedelta(days=1))],
        [_game("g", status="STATUS_SCHEDULED")],
        model_version=V1,
    )
    assert (record.games, record.pending) == (0, 1)
    assert record.accuracy is None


@pytest.mark.parametrize("status", ["STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_IN_PROGRESS"])
def test_a_game_without_a_final_result_is_never_scored(status):
    """A postponed game's prediction is *retained* -- it is a true record of what the model said --
    and excluded by joining to the status, never by deleting a row (which `sports_job`'s grant could
    not do anyway)."""
    record = score(
        [_prediction("g", 0.80, as_of=TIP - timedelta(days=1))],
        [_game("g", status=status, home=110, away=100)],
        model_version=V1,
    )
    assert (record.games, record.pending) == (0, 1)


def test_predictions_from_another_model_version_are_not_mixed_in():
    """D-017 makes 2026-27 the trial of *one* version. Averaging a frozen model's record with
    whatever superseded it and calling the result a track record would be a lie of arithmetic."""
    record = score(
        [
            _prediction("g", 0.80, as_of=TIP - timedelta(hours=2), version=V1),
            _prediction("g", 0.10, as_of=TIP - timedelta(hours=1), version=V2),
        ],
        [_game("g", home=110, away=100)],
        model_version=V1,
    )
    assert record.games == 1
    assert record.correct == 1, "V2's later, wrong call must not be the one scored"


def test_an_empty_band_reports_no_hit_rate_rather_than_zero():
    """`0.0` and "nothing to report" render identically as `0%`, and one of them is a lie."""
    record = score(
        [_prediction("g", 0.80, as_of=TIP - timedelta(hours=1))],
        [_game("g", home=110, away=100)],
        model_version=V1,
    )
    by_slug = {b.band.slug: b for b in record.bands}
    assert by_slug["strong"].hit_rate == 1.0
    for slug in ("toss-up", "lean", "clear"):
        assert by_slug[slug].games == 0
        assert by_slug[slug].hit_rate is None
        assert by_slug[slug].mean_probability is None


def test_an_empty_record_reports_nothing_rather_than_perfection():
    record = score([], [], model_version=V1)
    assert record.games == 0
    assert record.accuracy is None
    assert record.log_loss is None
    assert record.brier is None
    assert all(b.hit_rate is None for b in record.bands)


def test_log_loss_and_brier_match_the_arithmetic():
    """Pinned against hand-computed values so a refactor cannot quietly change what "log loss" means
    here. Both are taken on the *home* probability, not the favoured one: they score the
    distribution, not the pick, and folding both sides onto the favourite would stop a confident
    wrong call being punished more than a hesitant one."""
    record = score(
        [
            _prediction("a", 0.80, as_of=TIP - timedelta(hours=1)),  # home won
            _prediction("b", 0.30, as_of=TIP - timedelta(hours=1)),  # away won
        ],
        [_game("a", home=110, away=100), _game("b", home=95, away=105)],
        model_version=V1,
    )
    expected_ll = (-math.log(0.80) + -math.log(0.70)) / 2
    expected_brier = ((0.80 - 1.0) ** 2 + (0.30 - 0.0) ** 2) / 2
    assert record.log_loss == pytest.approx(expected_ll)
    assert record.brier == pytest.approx(expected_brier)


def test_a_confident_wrong_call_is_punished_more_than_a_hesitant_one():
    """The property the choice of home-side log loss exists to preserve."""
    def loss(probability: float) -> float:
        return score(
            [_prediction("g", probability, as_of=TIP - timedelta(hours=1))],
            [_game("g", home=95, away=105)],
            model_version=V1,
        ).log_loss

    assert loss(0.95) > loss(0.60)


def test_a_tie_is_refused_rather_than_scored():
    """NBA games do not end tied (F-049), so a tie means the feed is wrong. Guessing a side would
    put fiction into the accuracy number; refusing is loud and fixable."""
    with pytest.raises(TrackRecordError, match="tie"):
        _ = _game("g", home=100, away=100).winner


def test_a_final_game_missing_a_score_is_not_scoreable():
    """The migration's paired-scores check makes a half-populated row impossible in the database.
    This is the same refusal one layer up, for a record built in memory."""
    assert not _game("g", home=110, away=None).is_scoreable
    with pytest.raises(TrackRecordError, match="no result"):
        _ = _game("g", home=110, away=None).winner


# ── the Wilson interval ───────────────────────────────────────────────────────


def test_a_tiny_sample_gets_an_interval_wide_enough_to_say_so():
    """Three-from-three is a point estimate of 1.0 and an interval starting near .44. A surface
    shown only the point estimate has no way to tell a visitor that it means nothing yet."""
    low, high = wilson_interval(3, 3)
    assert high == 1.0
    assert low < 0.5


def test_a_large_sample_narrows():
    low, high = wilson_interval(600, 1000)
    assert 0.56 < low < 0.60 < high < 0.64


def test_the_interval_never_leaves_the_unit_interval():
    """The reason it is Wilson and not the normal approximation, which runs off both ends."""
    for correct, games in [(0, 1), (1, 1), (0, 5), (5, 5), (1, 100), (99, 100)]:
        low, high = wilson_interval(correct, games)
        assert 0.0 <= low <= high <= 1.0


def test_an_interval_over_no_games_is_refused():
    with pytest.raises(TrackRecordError, match="zero games"):
        wilson_interval(0, 0)


# ── no date comparison reaches the database ───────────────────────────────────

# D-039's rule, in the narrower form this module can satisfy. `store.py` may not contain the string
# `as_of` at all, because a corpus read has no business naming it. `predictions` has an `as_of`
# *column*, so that rule is not merely inconvenient here -- it is unsatisfiable. What is refused
# instead is a date **comparison**: naming the column is allowed, comparing it in SQL is not.
#
# The rule still has teeth. "The last prediction before tip-off is the one scored" is a filter over
# time, and this is what keeps it in `last_before_tip_off`, where the tests above can see it.
_DATE_COMPARISON = re.compile(
    r"\b\w*(?:as_of|date|time|_at|_ts)\w*\s*(?:<=|>=|<|>)", re.IGNORECASE
)
_BETWEEN = re.compile(r"\bbetween\b", re.IGNORECASE)


def _violations(strings: list[str]) -> list[str]:
    return [s for s in strings for p in (_DATE_COMPARISON, _BETWEEN) if p.search(s)]


def test_no_date_comparison_reaches_the_database() -> None:
    violations = _violations(executable_strings(MODULE_PATH.read_text()))
    assert violations == [], (
        "track_record.py builds SQL containing a date comparison. The window belongs in Python "
        f"where a test can read it -- see the module docstring: {violations}"
    )


@pytest.mark.parametrize(
    "offending",
    [
        "SELECT * FROM predictions WHERE as_of <= :tip_off",
        "AND game_date > :start",
        "WHERE created_at >= :since",
        "WHERE game_date BETWEEN :a AND :b",
    ],
)
def test_the_check_above_matches_what_it_claims_to(offending):
    """Non-vacuity. A regex that matched nothing would pass forever."""
    assert _violations([offending]), f"the matcher must catch {offending!r}"


@pytest.mark.parametrize(
    "allowed",
    [
        "SELECT game_id, model_version, as_of, home_win_prob FROM predictions",
        "GROUP BY model_version ORDER BY newest DESC",
        "ORDER BY game_date, game_id",
        "WHERE game_id = ANY(:game_ids)",
    ],
)
def test_the_check_above_permits_selecting_and_ordering_by_the_column(allowed):
    """The other half of non-vacuity: a matcher that refused everything would be equally useless,
    and would make this module unwritable."""
    assert not _violations([allowed])


# ── against a database ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def record_db(pg_admin_url: str) -> Iterator[str]:
    """A scratch database holding a small season: two finals, one scheduled, one postponed.

    No corpus rows. Nothing in this module computes a feature, so a corpus would be scenery -- and
    scenery in a fixture is a thing future readers assume is load-bearing.
    """
    from alembic import command

    for url in make_scratch_database(pg_admin_url, SCRATCH_DB):
        command.upgrade(alembic_config(url), "head")
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO schedule_snapshots (sha256, season, row_count, scheduled_count,"
                    " completed_count, postponed_count) VALUES ('snap', 2027, 4, 1, 2, 1)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO scheduled_games (game_id, season, season_type, game_date, home_id,"
                    " away_id, neutral_site, status, home_score, away_score, snapshot_sha256)"
                    " VALUES (:game_id, 2027, 2, :game_date, :home_id, :away_id, false, :status,"
                    " :home_score, :away_score, 'snap')"
                ),
                [
                    {"game_id": "won-home", "game_date": TIP, "home_id": "1", "away_id": "2",
                     "status": "STATUS_FINAL", "home_score": 112, "away_score": 100},
                    {"game_id": "won-away", "game_date": TIP + timedelta(days=1),
                     "home_id": "3", "away_id": "4",
                     "status": "STATUS_FINAL", "home_score": 95, "away_score": 105},
                    {"game_id": "ahead", "game_date": TIP + timedelta(days=4),
                     "home_id": "5", "away_id": "6",
                     "status": "STATUS_SCHEDULED", "home_score": None, "away_score": None},
                    {"game_id": "unscored", "game_date": TIP + timedelta(days=5),
                     "home_id": "7", "away_id": "8",
                     "status": "STATUS_SCHEDULED", "home_score": None, "away_score": None},
                    {"game_id": "called-off", "game_date": TIP + timedelta(days=2),
                     "home_id": "9", "away_id": "10",
                     "status": "STATUS_POSTPONED", "home_score": None, "away_score": None},
                ],
            )
            conn.execute(
                sa.text(
                    "INSERT INTO predictions (game_id, model_version, as_of, home_win_prob,"
                    " features, contributions, schedule_snapshot)"
                    " VALUES (:game_id, :version, :as_of, :p, :features, :contributions, 'snap')"
                ),
                [
                    # `won-home` has three: an early one, the one that stands, and -- inserted
                    # directly, because the job could not produce it -- one dated after tip-off.
                    {"game_id": "won-home", "version": V1, "as_of": TIP - timedelta(days=2),
                     "p": 0.58, "features": '{"elo_diff": 0.1}',
                     "contributions": '{"elo_diff": 0.05}'},
                    {"game_id": "won-home", "version": V1, "as_of": TIP - timedelta(hours=3),
                     "p": 0.80, "features": '{"elo_diff": 0.4, "home_b2b": 0.0}',
                     "contributions": '{"elo_diff": 1.3, "home_b2b": -0.1}'},
                    {"game_id": "won-home", "version": V1, "as_of": TIP + timedelta(hours=2),
                     "p": 0.02, "features": '{"elo_diff": -2.0}',
                     "contributions": '{"elo_diff": -3.9}'},
                    # A second version's later, wrong call on the same game.
                    {"game_id": "won-home", "version": V2, "as_of": TIP - timedelta(hours=1),
                     "p": 0.10, "features": '{"elo_diff": -1.0}',
                     "contributions": '{"elo_diff": -2.2}'},
                    {"game_id": "won-away", "version": V1, "as_of": TIP, "p": 0.30,
                     "features": '{"elo_diff": -0.3}', "contributions": '{"elo_diff": -0.85}'},
                    {"game_id": "ahead", "version": V1, "as_of": TIP - timedelta(days=1),
                     "p": 0.66, "features": '{"elo_diff": 0.2}',
                     "contributions": '{"elo_diff": 0.66}'},
                    {"game_id": "called-off", "version": V1, "as_of": TIP - timedelta(days=1),
                     "p": 0.72, "features": '{"elo_diff": 0.3}',
                     "contributions": '{"elo_diff": 0.95}'},
                ],
            )
        engine.dispose()
        yield url


@pytest.fixture
def conn(record_db: str) -> Iterator[sa.Connection]:
    engine = sa.create_engine(record_db)
    with engine.connect() as connection:
        yield connection
    engine.dispose()


def test_predictions_narrow_by_game_and_by_version(conn):
    assert len(tr.load_predictions(conn, game_ids=["won-home"])) == 4
    assert len(tr.load_predictions(conn, game_ids=["won-home"], model_version=V1)) == 3
    assert len(tr.load_predictions(conn, model_version=V2)) == 1


def test_an_empty_narrowing_is_refused_rather_than_widened(conn):
    """An empty collection reads as "no narrowing" under a naive `if values:` and silently returns
    everything -- the opposite of what was asked."""
    with pytest.raises(TrackRecordError, match="empty narrowing"):
        tr.load_predictions(conn, game_ids=[])


def test_the_decomposition_is_read_only_when_asked_for(conn):
    """Carrying two JSONB blobs per row through a season-wide record query costs far more than it
    explains. The game-detail read asks for them; the record does not."""
    lean = tr.load_predictions(conn, game_ids=["won-home"], model_version=V1)
    assert all(p.features is None and p.contributions is None for p in lean)

    full = tr.load_predictions(
        conn, game_ids=["won-home"], model_version=V1, with_decomposition=True
    )
    assert all(isinstance(p.features, dict) for p in full)
    assert full[0].contributions == {"elo_diff": 0.05}


def test_model_versions_come_back_most_recently_used_first(conn):
    """V2's only prediction is dated an hour before tip-off; V1's latest is two hours after it."""
    assert tr.model_versions(conn) == [V1, V2]


def test_the_record_scores_the_last_prediction_before_tip_off(conn):
    """The end-to-end form of the pure test above. `won-home` carries a .02 prediction dated after
    tip-off; if the rule leaked, the record would show a wrong call in the `strong` band."""
    record = tr.compute_record(conn, model_version=V1)
    assert record.games == 2, "won-home and won-away; the scheduled and postponed games are pending"
    assert record.correct == 2
    assert record.pending == 2, "ahead and called-off"

    by_slug = {b.band.slug: b for b in record.bands}
    assert by_slug["strong"].games == 1, "the .80 prediction, not the .02 one"
    assert by_slug["clear"].games == 1, "the .30 prediction, favouring away at .70 confidence"
    assert by_slug["toss-up"].games == 0


def test_the_record_defaults_to_the_most_recently_used_version(conn):
    assert tr.compute_record(conn).model_version == V1


def test_a_database_with_no_predictions_says_so_rather_than_inventing_a_version(record_db):
    """`compute_record` has no version to default to, and returning an empty record under a made-up
    one would report "0 games, no errors" for a job that has never run."""
    engine = sa.create_engine(record_db)
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TEMP TABLE saved AS SELECT * FROM predictions"))
        connection.execute(sa.text("DELETE FROM predictions"))
        with pytest.raises(TrackRecordError, match="no predictions"):
            tr.compute_record(connection)
        connection.execute(sa.text("INSERT INTO predictions SELECT * FROM saved"))
    engine.dispose()


def test_upcoming_returns_the_window_and_lists_games_the_job_has_not_reached(conn):
    """A game with no prediction is listed carrying `None`. Omitting it would make "the job has not
    run" and "there are no games" render identically, and the first of those is an outage."""
    rows = tr.upcoming(conn, as_of=TIP + timedelta(days=1), horizon_days=7)
    by_id = {r.game.game_id: r for r in rows}
    assert set(by_id) == {"ahead", "unscored"}, "postponed and already-final games are not upcoming"
    assert by_id["ahead"].prediction is not None
    assert by_id["unscored"].prediction is None


def test_upcoming_excludes_what_falls_outside_the_horizon(conn):
    """The control for the window: a filter returning everything would pass the test above."""
    rows = tr.upcoming(conn, as_of=TIP + timedelta(days=1), horizon_days=3)
    assert {r.game.game_id for r in rows} == {"ahead"}


def test_an_unbounded_horizon_is_refused(conn):
    """D-047 leaves these endpoints unauthenticated, so an unbounded horizon is an unbounded read
    for anyone who asks for one."""
    with pytest.raises(TrackRecordError, match="horizon_days"):
        tr.upcoming(conn, as_of=TIP, horizon_days=tr.MAX_HORIZON_DAYS + 1)
    with pytest.raises(TrackRecordError, match="horizon_days"):
        tr.upcoming(conn, as_of=TIP, horizon_days=0)


def test_game_detail_carries_the_standing_prediction_its_history_and_its_bands_record(conn):
    detail = tr.game_detail(conn, "won-home", model_version=V1)
    assert detail is not None
    assert detail.latest.home_win_probability == 0.80
    assert detail.latest.features == {"elo_diff": 0.4, "home_b2b": 0.0}
    assert [p.home_win_probability for p in detail.history] == [0.58, 0.80, 0.02], (
        "history is every prediction made, oldest first -- including the one that cannot be scored"
    )
    assert detail.band_record.band.slug == "strong"
    assert detail.band_record.games == 1


def test_game_detail_is_none_for_a_game_nobody_has_predicted(conn):
    assert tr.game_detail(conn, "unscored", model_version=V1) is None
    assert tr.game_detail(conn, "no-such-game", model_version=V1) is None
