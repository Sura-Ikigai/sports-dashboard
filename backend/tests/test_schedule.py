"""T-031 / D-048 -- the live schedule feed, and what replaced the content pin.

D-046's guarantee is a SHA-256 over source bytes. It works because a finished season's file never
changes again, and it is unavailable here for a file that changes nightly. So the tests that matter
are the ones covering what took its place: **structure** (is this a plausible NBA schedule) and
**continuity** (is it consistent with what we already accepted).

The refusals are the point, and each has a control proving it does not fire on ordinary input. The
distinction the module has to get right is *refuse* versus *exclude and count*: a shrinking game
count cannot be true of a real schedule, while a handful of NBA Cup placeholders is every December.

Data is synthetic and the network is never touched -- `loader.load_live_schedule` is covered by its
own tests, and what is under test here is the checking, not the fetching.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

pd = pytest.importorskip("pandas", reason="training-only dependency (D-016)")

import sqlalchemy as sa  # noqa: E402

from conftest import alembic_config, make_scratch_database  # noqa: E402
from model import corpus, schedule  # noqa: E402
from model.schedule import ScheduleError  # noqa: E402

SCRATCH_DB = "t031_schedule_test"
CORPUS_SEASON = 2024
LIVE_SEASON = 2027
TEAMS = [str(300 + i) for i in range(30)]
START = datetime(2026, 10, 20, 23, 0, tzinfo=UTC)


def _digest(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


_COLUMNS = (
    "game_id", "date", "season", "season_type", "home_id", "away_id",
    "neutral_site", "status", "venue_id", "venue_city", "venue_state",
)


def _frame(rows: list[dict]) -> pd.DataFrame:
    """Rows -> the normalized live-schedule frame. Empty is a shape the module must handle, so the
    columns are declared rather than inferred from the rows."""
    frame = pd.DataFrame(rows, columns=list(_COLUMNS))
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    return frame


def _row(n: int, *, home: str | None = None, away: str | None = None, day: int | None = None,
         status: str = "STATUS_SCHEDULED", season: int = LIVE_SEASON) -> dict:
    return {
        "game_id": f"live-{n}",
        "date": START + timedelta(days=n if day is None else day),
        "season": season,
        "season_type": 2,
        "home_id": home or TEAMS[n % 30],
        "away_id": away or TEAMS[(n + 1) % 30],
        "neutral_site": False,
        "status": status,
        "venue_id": f"v{n % 5}",
        "venue_city": "Boston",
        "venue_state": "MA",
    }


def _schedule(count: int = 40, **kwargs) -> pd.DataFrame:
    return _frame([_row(n, **kwargs) for n in range(count)])


@pytest.fixture(scope="module")
def live_db(pg_admin_url: str) -> Iterator[str]:
    """A migrated database with a corpus rich enough to define 30 franchises."""
    from alembic import command

    for url in make_scratch_database(pg_admin_url, SCRATCH_DB):
        command.upgrade(alembic_config(url), "head")
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            games = []
            n = 0
            for i, home in enumerate(TEAMS):
                for away in TEAMS[i + 1:]:
                    games.append({
                        "game_id": f"c{n}", "season": CORPUS_SEASON, "season_type": 2,
                        "game_date": datetime(2023, 10, 20, tzinfo=UTC) + timedelta(hours=n),
                        "home_id": home, "away_id": away, "home_score": 110, "away_score": 104,
                        "neutral_site": False, "venue_id": None,
                    })
                    n += 1
            conn.execute(
                sa.text(
                    "INSERT INTO corpus_games (game_id, season, season_type, game_date, home_id,"
                    " away_id, home_score, away_score, neutral_site, venue_id)"
                    " VALUES (:game_id, :season, :season_type, :game_date, :home_id, :away_id,"
                    " :home_score, :away_score, :neutral_site, :venue_id)"
                ),
                games,
            )
        engine.dispose()
        yield url


@pytest.fixture
def conn(live_db: str) -> Iterator[sa.Connection]:
    """A transaction rolled back after each test, so tests do not see each other's snapshots."""
    engine = sa.create_engine(live_db)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


# ── the franchise rule ────────────────────────────────────────────────────────


def test_franchises_come_from_games_played_not_from_a_list(conn: sa.Connection) -> None:
    """F-042's reasoning, applied here: next season's All-Star game carries *fresh* phantom ids, and
    a hardcoded list would let them through with no signal at all."""
    assert schedule._known_franchises(conn) == frozenset(TEAMS)


def test_a_team_below_the_games_threshold_is_not_a_franchise(conn: sa.Connection) -> None:
    """The non-vacuity control for the rule above: the check must discriminate, not just return
    every id it sees."""
    conn.execute(
        sa.text(
            "INSERT INTO corpus_games (game_id, season, season_type, game_date, home_id, away_id,"
            " home_score, away_score, neutral_site) VALUES ('phantom', :s, 2, :d, '999998',"
            " '999999', 150, 140, true)"
        ),
        {"s": CORPUS_SEASON, "d": datetime(2024, 2, 18, tzinfo=UTC)},
    )
    franchises = schedule._known_franchises(conn)
    assert "999998" not in franchises
    assert franchises == frozenset(TEAMS)
    assert corpus.MIN_SEASON_GAMES_FOR_A_REAL_TEAM > 1


# ── a clean snapshot ──────────────────────────────────────────────────────────


def test_a_clean_snapshot_is_accepted_and_stored(conn: sa.Connection) -> None:
    report = schedule.ingest_snapshot(conn, _schedule(40), _digest("a"), LIVE_SEASON)
    assert (report.row_count, report.scheduled_count, report.added_game_ids) == (40, 40, 40)
    assert report.unchanged is False

    stored = conn.execute(sa.text("SELECT count(*) FROM scheduled_games")).scalar()
    snapshots = conn.execute(sa.text("SELECT count(*) FROM schedule_snapshots")).scalar()
    assert (stored, snapshots) == (40, 1)


def test_re_ingesting_identical_bytes_is_a_no_op(conn: sa.Connection) -> None:
    """The provenance trail must mean "the schedule changed", not "somebody ran the job"."""
    frame, digest = _schedule(40), _digest("a")
    schedule.ingest_snapshot(conn, frame, digest, LIVE_SEASON)
    second = schedule.ingest_snapshot(conn, frame, digest, LIVE_SEASON)
    assert second.unchanged is True
    assert conn.execute(sa.text("SELECT count(*) FROM schedule_snapshots")).scalar() == 1


def test_placeholders_are_excluded_and_counted_not_refused(conn: sa.Connection) -> None:
    """Every December. The NBA Cup semi-finals carry `home_id = -1` / `away_id = -2` until the
    bracket resolves, and refusing them would stop the daily job over games nobody wants predicted."""
    rows = [_row(n) for n in range(40)]
    rows += [_row(90 + i, home="-1", away="-2") for i in range(5)]
    report = schedule.ingest_snapshot(conn, _frame(rows), _digest("a"), LIVE_SEASON)
    assert report.excluded_placeholders == 5
    assert report.row_count == 40
    assert conn.execute(sa.text("SELECT count(*) FROM scheduled_games")).scalar() == 40


def test_first_seen_is_preserved_while_last_seen_moves(conn: sa.Connection) -> None:
    """When a game first appeared is a fact about the schedule, not about this run. A churn threshold
    will eventually need it, and overwriting it would erase how long we have known about a game."""
    schedule.ingest_snapshot(conn, _schedule(10), _digest("a"), LIVE_SEASON)
    before = conn.execute(
        sa.text("SELECT first_seen, last_seen FROM scheduled_games WHERE game_id = 'live-0'")
    ).first()

    moved = _schedule(10)
    moved.loc[moved["game_id"] == "live-0", "date"] += pd.Timedelta(days=1)
    schedule.ingest_snapshot(conn, moved, _digest("b"), LIVE_SEASON)

    after = conn.execute(
        sa.text("SELECT first_seen, last_seen FROM scheduled_games WHERE game_id = 'live-0'")
    ).first()
    assert after.first_seen == before.first_seen
    assert after.last_seen >= before.last_seen


def test_a_moved_tip_off_is_counted_as_a_change(conn: sa.Connection) -> None:
    schedule.ingest_snapshot(conn, _schedule(10), _digest("a"), LIVE_SEASON)
    moved = _schedule(10)
    moved.loc[moved["game_id"].isin(["live-0", "live-1"]), "date"] += pd.Timedelta(hours=3)
    report = schedule.ingest_snapshot(conn, moved, _digest("b"), LIVE_SEASON)
    assert report.changed_game_ids == 2
    assert report.added_game_ids == 0


def test_an_unchanged_snapshot_with_new_bytes_counts_no_changes(conn: sa.Connection) -> None:
    """The control for the test above. Churn counting has to discriminate, or every snapshot would
    look like a mass reschedule and the eventual threshold would be meaningless."""
    schedule.ingest_snapshot(conn, _schedule(10), _digest("a"), LIVE_SEASON)
    report = schedule.ingest_snapshot(conn, _schedule(10), _digest("b"), LIVE_SEASON)
    assert (report.changed_game_ids, report.added_game_ids) == (0, 0)


# ── continuity: the refusals ──────────────────────────────────────────────────


def test_a_shrinking_schedule_is_refused(conn: sa.Connection) -> None:
    """A schedule does not shrink. Postponed games keep their row, so this is a truncated file or an
    upstream regression -- exactly the threat the pinned count used to catch."""
    schedule.ingest_snapshot(conn, _schedule(40), _digest("a"), LIVE_SEASON)
    with pytest.raises(ScheduleError, match="does not shrink"):
        schedule.ingest_snapshot(conn, _schedule(39), _digest("b"), LIVE_SEASON)


def test_a_growing_schedule_is_accepted(conn: sa.Connection) -> None:
    """The control: playoff rows are appended in April, and that must not look like an attack."""
    schedule.ingest_snapshot(conn, _schedule(40), _digest("a"), LIVE_SEASON)
    report = schedule.ingest_snapshot(conn, _schedule(45), _digest("b"), LIVE_SEASON)
    assert report.added_game_ids == 5


def test_a_game_we_already_predicted_may_not_vanish(conn: sa.Connection) -> None:
    """Stronger than a count pin, which a file that both loses and gains rows would satisfy. A
    prediction that can no longer be scored, for a game the schedule no longer admits existed, is a
    hole in the accuracy record with no way to explain it."""
    schedule.ingest_snapshot(conn, _schedule(40), _digest("a"), LIVE_SEASON)
    conn.execute(
        sa.text(
            "INSERT INTO predictions (game_id, model_version, as_of, home_win_prob, features,"
            " contributions) VALUES ('live-3', 'v1', :t, 0.6, '{}', '{}')"
        ),
        {"t": datetime(2026, 10, 21, tzinfo=UTC)},
    )
    dropped = _frame([_row(n) for n in range(41) if n != 3])  # same count, one predicted id gone
    with pytest.raises(ScheduleError, match="already predicted are absent"):
        schedule.ingest_snapshot(conn, dropped, _digest("b"), LIVE_SEASON)


def test_a_postponed_game_keeps_its_row_and_is_not_a_vanishing(conn: sa.Connection) -> None:
    """The control for the check above, and the behaviour verified against all four postponements of
    the completed 2026 season: the row stays forever, its status changes, and the replay is a new
    game with a new id."""
    schedule.ingest_snapshot(conn, _schedule(40), _digest("a"), LIVE_SEASON)
    conn.execute(
        sa.text(
            "INSERT INTO predictions (game_id, model_version, as_of, home_win_prob, features,"
            " contributions) VALUES ('live-3', 'v1', :t, 0.6, '{}', '{}')"
        ),
        {"t": datetime(2026, 10, 21, tzinfo=UTC)},
    )
    postponed = _schedule(40)
    postponed.loc[postponed["game_id"] == "live-3", "status"] = "STATUS_POSTPONED"
    report = schedule.ingest_snapshot(conn, postponed, _digest("b"), LIVE_SEASON)

    assert report.postponed_count == 1
    assert report.changed_game_ids == 1
    status = conn.execute(
        sa.text("SELECT status FROM scheduled_games WHERE game_id = 'live-3'")
    ).scalar()
    assert status == "STATUS_POSTPONED"


# ── structure: the refusals ───────────────────────────────────────────────────


def test_an_empty_schedule_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(ScheduleError, match="is empty"):
        schedule.ingest_snapshot(conn, _schedule(0), _digest("a"), LIVE_SEASON)


def test_a_wrong_season_label_is_refused(conn: sa.Connection) -> None:
    frame = _frame([_row(n, season=LIVE_SEASON if n else 1999) for n in range(10)])
    with pytest.raises(ScheduleError, match="carries season label"):
        schedule.ingest_snapshot(conn, frame, _digest("a"), LIVE_SEASON)


def test_a_repeated_game_id_is_refused(conn: sa.Connection) -> None:
    rows = [_row(n) for n in range(10)]
    rows.append({**_row(99), "game_id": "live-3"})
    with pytest.raises(ScheduleError, match="repeats game_id"):
        schedule.ingest_snapshot(conn, _frame(rows), _digest("a"), LIVE_SEASON)


def test_an_unrecognised_status_is_refused(conn: sa.Connection) -> None:
    """Refusing to guess whether an unknown status is predictable. Treating it as scheduled would
    predict a cancelled game; treating it as not would silently stop predicting a real one."""
    frame = _schedule(10)
    frame.loc[0, "status"] = "STATUS_SOMETHING_NEW"
    with pytest.raises(ScheduleError, match="unrecognised status"):
        schedule.ingest_snapshot(conn, frame, _digest("a"), LIVE_SEASON)


def test_naive_dates_are_refused(conn: sa.Connection) -> None:
    frame = _schedule(10)
    frame["date"] = frame["date"].dt.tz_localize(None)
    with pytest.raises(ScheduleError, match="timezone-naive"):
        schedule.ingest_snapshot(conn, frame, _digest("a"), LIVE_SEASON)


def test_a_missing_column_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(ScheduleError, match="missing column"):
        schedule.ingest_snapshot(conn, _schedule(10).drop(columns=["status"]), _digest("a"),
                                 LIVE_SEASON)


def test_a_wholesale_id_scheme_change_is_refused(conn: sa.Connection) -> None:
    """This check is only for the wholesale case. A *partial* degradation shrinks the accepted row
    count and is caught by the continuity check instead -- two nets at different scales, which is why
    this threshold sits at half the file rather than near the 0.5% a real season actually carries."""
    frame = _frame([_row(n, home=f"x{n}", away=f"y{n}") for n in range(20)])
    with pytest.raises(ScheduleError, match="changed its id scheme"):
        schedule.ingest_snapshot(conn, frame, _digest("a"), LIVE_SEASON)


def test_a_few_unknown_ids_are_excluded_rather_than_refused(conn: sa.Connection) -> None:
    """The non-vacuity control for the test above: the All-Star game arrives mid-season with fresh
    phantom ids, and the job must not stop for it."""
    rows = [_row(n) for n in range(40)]
    rows.append(_row(99, home="allstar-a", away="allstar-b"))
    report = schedule.ingest_snapshot(conn, _frame(rows), _digest("a"), LIVE_SEASON)
    assert report.excluded_placeholders == 1
    assert report.row_count == 40


def test_an_empty_corpus_is_refused_rather_than_validating_against_nothing(
    pg_admin_url: str,
) -> None:
    """Without this, a fresh database would accept any team id at all -- the franchise check would
    compare against an empty set and exclude every row, which is the silent-failure shape."""
    from alembic import command

    for url in make_scratch_database(pg_admin_url, "t031_empty_corpus"):
        command.upgrade(alembic_config(url), "head")
        engine = sa.create_engine(url)
        with engine.begin() as conn, pytest.raises(ScheduleError, match="no games"):
            schedule.ingest_snapshot(conn, _schedule(10), _digest("a"), LIVE_SEASON)
        engine.dispose()
