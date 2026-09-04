"""T-023 -- the ingest boundary, where verification moves from files to a database (D-046).

Everything downstream of `ingest` trusts these tables without re-deriving them from source bytes, so
the contract is narrow: **refuse rather than repair.** These tests are mostly about the refusals.

## Why the data here is synthetic

The real corpus is 11,870 games and 173k player rows behind a network. Ingest's job is not to
re-verify those bytes -- `loader` does that, and `test_loader.py` covers it -- but to write what the
loader hands it, correctly and exactly once. Small synthetic frames exercise every branch of that in
milliseconds, and `loader.load_season` / `load_box` are patched so nothing here touches the network.

The real run is not skipped, merely not run *here*: T-023's outcome in
`docs/plans/modeling-second-cycle.md` records the live ingest of all nine seasons and its
verification in SQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

pd = pytest.importorskip("pandas", reason="training-only dependency (D-016)")

import sqlalchemy as sa  # noqa: E402

from conftest import alembic_config, make_scratch_database  # noqa: E402
from model import ingest, loader  # noqa: E402
from model.ingest import IngestError  # noqa: E402

SEASON = 2024
SCRATCH_DB = "t023_ingest_test"

# 30 teams, because `_assert_franchise_invariant` demands exactly that many per season -- the same
# check D-046 names. Team ids are strings because everything is ESPN-keyed and the corpus stores
# text; see `_str_or_none`.
TEAMS = [str(100 + i) for i in range(30)]


# ── synthetic frames shaped like the loader's output ──────────────────────────


def _schedule_frame(season: int = SEASON, *, rounds: int = 2) -> pd.DataFrame:
    """A mini-season: every team plays every other team `rounds` times.

    With 30 teams and 2 rounds each team plays 58 games, comfortably over
    `corpus.MIN_SEASON_GAMES_FOR_A_REAL_TEAM` (20), so all 30 read as franchises.
    """
    rows = []
    start = datetime(season - 1, 10, 20, tzinfo=UTC)
    n = 0
    for r in range(rounds):
        for i, home in enumerate(TEAMS):
            for away in TEAMS[i + 1 :]:
                h, a = (home, away) if r == 0 else (away, home)
                rows.append(
                    {
                        "game_id": f"{season}-{n}",
                        "date": start + timedelta(hours=n),
                        "season": season,
                        "season_type": 2,
                        "home_id": h,
                        "away_id": a,
                        "home_score": 110,
                        "away_score": 104,
                        "neutral_site": False,
                        "venue_id": f"v{int(h) % 5}",
                        "venue_name": f"Arena {int(h) % 5}",
                        "venue_city": "Denver",
                        "venue_state": "CO",
                        "venue_indoor": True,
                        "home_win": True,
                    }
                )
                n += 1
    return pd.DataFrame(rows)


def _player_box_frame(schedule: pd.DataFrame, *, per_team: int = 3) -> pd.DataFrame:
    rows = []
    for game in schedule.itertuples(index=False):
        for team in (game.home_id, game.away_id):
            for p in range(per_team):
                rows.append(
                    {
                        "game_id": game.game_id,
                        "season": game.season,
                        "season_type": game.season_type,
                        "game_date": game.date,
                        "athlete_id": f"{team}-{p}",
                        "team_id": team,
                        # One DNP per team per game, with NULL minutes -- and one active player on
                        # exactly zero minutes. Those two must stay distinguishable (user story 12).
                        "minutes": None if p == 0 else (0.0 if p == 1 else 30.0),
                        "starter": p == 2,
                        "did_not_play": p == 0,
                        "active": p != 0,
                        "points": None if p == 0 else 10.0,
                    }
                )
    return pd.DataFrame(rows)


def _team_box_frame(schedule: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for game in schedule.itertuples(index=False):
        for team, side, score in (
            (game.home_id, "home", game.home_score),
            (game.away_id, "away", game.away_score),
        ):
            rows.append(
                {
                    "game_id": game.game_id,
                    "season": game.season,
                    "season_type": game.season_type,
                    "game_date": game.date,
                    "team_id": team,
                    "team_home_away": side,
                    "team_score": float(score),
                }
            )
    return pd.DataFrame(rows)


@pytest.fixture
def schedule() -> pd.DataFrame:
    return _schedule_frame()


@pytest.fixture
def patched_loader(schedule: pd.DataFrame):
    """Serve the synthetic frames in place of the network, and pin the drop count for the season."""
    players = _player_box_frame(schedule)
    teams = _team_box_frame(schedule)

    def _load_box(kind, season, *_args, **_kwargs):
        return players.copy() if kind == "player_box" else teams.copy()

    with (
        patch.object(loader, "load_season", lambda season, *a, **k: schedule.copy()),
        patch.object(loader, "load_box", _load_box),
        patch.dict(ingest.UNIDENTIFIED_PLAYER_ROWS, {SEASON: 0}),
    ):
        yield


# ── pure conversions ──────────────────────────────────────────────────────────


def test_nan_becomes_null_rather_than_a_stored_nan() -> None:
    """A NaN reaching a `double precision` column stores the float NaN, which is not NULL and
    compares unequal to itself -- so `minutes IS NULL`, the query T-027's availability rests on,
    would silently return nothing."""
    assert ingest._none_if_nan(float("nan")) is None
    assert ingest._none_if_nan(None) is None
    assert ingest._none_if_nan(0.0) == 0.0
    assert ingest._none_if_nan(30.5) == 30.5


def test_ids_are_normalized_to_text_across_both_sources() -> None:
    """Box-score ids arrive as int32 and schedule ids as str. Everything is ESPN-keyed and the
    corpus keys them as text, so these have to become the same key or the box scores join to
    nothing."""
    assert ingest._str_or_none(401584689) == "401584689"
    assert ingest._str_or_none("401584689") == "401584689"
    assert ingest._str_or_none(float("nan")) is None


def test_minutes_and_did_not_play_survive_conversion_separately(schedule: pd.DataFrame) -> None:
    """User story 12 at the ingest boundary: a zero-minute row is not an absence."""
    rows = ingest._player_box_rows(_player_box_frame(schedule.head(1)))
    dnp = [r for r in rows if r["did_not_play"]]
    zero = [r for r in rows if r["minutes"] == 0.0]
    assert dnp and zero
    assert all(r["minutes"] is None for r in dnp)
    assert all(not r["did_not_play"] for r in zero)


def test_venues_are_deduplicated_keeping_the_latest_name(schedule: pd.DataFrame) -> None:
    """Arenas change sponsors constantly, so a venue renamed mid-corpus must land with its most
    recent name rather than whichever row happened to sort first."""
    frame = schedule.copy()
    frame.loc[frame.index[-1], "venue_id"] = "v0"
    frame.loc[frame.index[-1], "venue_name"] = "Renamed Arena"
    rows = {r["venue_id"]: r for r in ingest._venue_rows(frame)}
    assert rows["v0"]["full_name"] == "Renamed Arena"


def test_a_blank_state_becomes_null_not_an_empty_string(schedule: pd.DataFrame) -> None:
    """International games carry no state. "The source did not say" must be one value in the
    database, not two that compare unequal."""
    frame = schedule.copy()
    frame["venue_state"] = None
    assert all(r["state"] is None for r in ingest._venue_rows(frame))


# ── the pinned repair (null athlete_id) ───────────────────────────────────────


def test_unidentifiable_player_rows_are_dropped_when_the_count_matches(
    schedule: pd.DataFrame,
) -> None:
    frame = _player_box_frame(schedule.head(2))
    frame.loc[frame.index[0], "athlete_id"] = None
    with patch.dict(ingest.UNIDENTIFIED_PLAYER_ROWS, {SEASON: 1}):
        kept = ingest._identifiable_players(frame, SEASON)
    assert len(kept) == len(frame) - 1


def test_one_more_unidentifiable_row_than_pinned_is_refused(schedule: pd.DataFrame) -> None:
    """The whole justification for dropping anything. Without this assertion `_identifiable_players`
    is a silent filter that would absorb an upstream regression of any size, and the corpus would
    quietly hold fewer players than the source published with nothing to notice."""
    frame = _player_box_frame(schedule.head(2))
    frame.loc[frame.index[0], "athlete_id"] = None
    frame.loc[frame.index[1], "athlete_id"] = None
    with patch.dict(ingest.UNIDENTIFIED_PLAYER_ROWS, {SEASON: 1}):
        with pytest.raises(IngestError, match="2 rows have a null athlete_id"):
            ingest._identifiable_players(frame, SEASON)


def test_fewer_unidentifiable_rows_than_pinned_is_also_refused(schedule: pd.DataFrame) -> None:
    """Drift in either direction means the source changed. A pin that only caught growth would let
    a silent upstream reshuffle through."""
    frame = _player_box_frame(schedule.head(2))
    with patch.dict(ingest.UNIDENTIFIED_PLAYER_ROWS, {SEASON: 1}):
        with pytest.raises(IngestError, match="0 rows have a null athlete_id"):
            ingest._identifiable_players(frame, SEASON)


def test_an_unexamined_season_is_refused_rather_than_assumed_clean(schedule: pd.DataFrame) -> None:
    frame = _player_box_frame(schedule.head(1))
    with patch.dict(ingest.UNIDENTIFIED_PLAYER_ROWS, {}, clear=True):
        with pytest.raises(IngestError, match="no pinned unrepresentable-row count"):
            ingest._identifiable_players(frame, SEASON)


def test_the_real_2026_drop_count_is_pinned_and_every_other_season_is_zero() -> None:
    """Recorded from the live files on 2026-09-04. If this ever needs changing, the source changed,
    and that is a reviewed decision rather than a test edit."""
    assert ingest.UNIDENTIFIED_PLAYER_ROWS == {2022: 0, 2023: 0, 2024: 0, 2025: 0, 2026: 33}
    assert set(ingest.UNIDENTIFIED_PLAYER_ROWS) == set(loader.BOX_SEASONS)


# ── the franchise invariant (D-046) ───────────────────────────────────────────


def test_a_full_thirty_team_season_passes(schedule: pd.DataFrame) -> None:
    """Non-vacuity control for the refusals below."""
    assert ingest._assert_franchise_invariant(schedule) == 0


def test_a_season_missing_a_franchise_is_refused(schedule: pd.DataFrame) -> None:
    frame = schedule[(schedule.home_id != TEAMS[0]) & (schedule.away_id != TEAMS[0])]
    with pytest.raises(IngestError, match="franchise invariant violated"):
        ingest._assert_franchise_invariant(frame)


def test_exhibition_games_are_identified_and_counted_not_removed(schedule: pd.DataFrame) -> None:
    """Exhibitions are written and excluded on read. The pinned counts count them, so writing the
    curated set would make the database's own row count disagree with the number that verifies it.
    """
    extra = schedule.iloc[0].copy()
    extra["game_id"] = "all-star"
    extra["home_id"] = "999"  # a phantom id playing exactly one game
    frame = pd.concat([schedule, pd.DataFrame([extra])], ignore_index=True)
    assert ingest._assert_franchise_invariant(frame) == 1


# ── against a real database ───────────────────────────────────────────────────


@pytest.fixture(scope="session")
def ingest_db(pg_admin_url: str) -> Iterator[str]:
    from alembic import command

    for url in make_scratch_database(pg_admin_url, SCRATCH_DB):
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture
def clean_db(ingest_db: str) -> Iterator[str]:
    engine = sa.create_engine(ingest_db)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "TRUNCATE corpus_player_box, corpus_team_box, corpus_games, corpus_venues CASCADE"
            )
        )
    engine.dispose()
    yield ingest_db


def _counts(url: str) -> dict[str, int]:
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        counts = {
            t: conn.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in ("corpus_venues", "corpus_games", "corpus_player_box", "corpus_team_box")
        }
    engine.dispose()
    return counts


def test_ingest_writes_every_table(clean_db: str, patched_loader, schedule) -> None:
    report = ingest.ingest(clean_db, [SEASON], box_seasons=[SEASON])
    counts = _counts(clean_db)
    assert counts["corpus_games"] == len(schedule)
    assert counts["corpus_team_box"] == 2 * len(schedule)
    assert counts["corpus_player_box"] == 6 * len(schedule)
    assert counts["corpus_venues"] == 5
    assert report.games == len(schedule)


def test_running_twice_yields_one_row_per_game(clean_db: str, patched_loader) -> None:
    """D-045's acceptance criterion, stated exactly as the plan words it."""
    ingest.ingest(clean_db, [SEASON], box_seasons=[SEASON])
    first = _counts(clean_db)
    ingest.ingest(clean_db, [SEASON], box_seasons=[SEASON])
    assert _counts(clean_db) == first


def test_a_re_ingest_repairs_a_changed_row_rather_than_duplicating_it(
    clean_db: str, schedule: pd.DataFrame
) -> None:
    """The one sense in which this module repairs anything, and it is deliberate: the alternative is
    an operator hand-deleting rows before every re-ingest, which is how partial states are made."""
    corrected = schedule.copy()
    corrected.loc[corrected.index[0], "home_score"] = 999

    players = _player_box_frame(schedule)
    teams = _team_box_frame(schedule)

    def _load_box(kind, season, *_a, **_k):
        return players.copy() if kind == "player_box" else teams.copy()

    with (
        patch.object(loader, "load_box", _load_box),
        patch.dict(ingest.UNIDENTIFIED_PLAYER_ROWS, {SEASON: 0}),
    ):
        with patch.object(loader, "load_season", lambda *a, **k: schedule.copy()):
            ingest.ingest(clean_db, [SEASON], box_seasons=[SEASON])
        before = _counts(clean_db)
        with patch.object(loader, "load_season", lambda *a, **k: corrected.copy()):
            ingest.ingest(clean_db, [SEASON], box_seasons=[SEASON])

    assert _counts(clean_db) == before
    engine = sa.create_engine(clean_db)
    with engine.connect() as conn:
        score = conn.execute(
            sa.text("SELECT home_score FROM corpus_games WHERE game_id = :g"),
            {"g": schedule.iloc[0].game_id},
        ).scalar_one()
    engine.dispose()
    assert score == 999


def test_minutes_and_did_not_play_land_distinctly_in_the_database(
    clean_db: str, patched_loader
) -> None:
    ingest.ingest(clean_db, [SEASON], box_seasons=[SEASON])
    engine = sa.create_engine(clean_db)
    with engine.connect() as conn:
        null_minutes, zero_minutes, dnp_with_minutes = conn.execute(
            sa.text(
                "SELECT count(*) FILTER (WHERE minutes IS NULL),"
                "       count(*) FILTER (WHERE minutes = 0),"
                "       count(*) FILTER (WHERE did_not_play AND minutes IS NOT NULL)"
                "  FROM corpus_player_box"
            )
        ).one()
    engine.dispose()
    assert null_minutes > 0
    assert zero_minutes > 0
    assert dnp_with_minutes == 0


def test_an_unpinned_season_is_refused_before_anything_is_written(clean_db: str) -> None:
    with pytest.raises(IngestError, match="not pinned in loader.SCHEDULE_SEASONS"):
        ingest.ingest(clean_db, [1999])
    assert _counts(clean_db)["corpus_games"] == 0


def test_a_warmup_season_has_no_box_scores_to_ask_for(clean_db: str) -> None:
    with pytest.raises(IngestError, match="no pinned box-score assets"):
        ingest.ingest(clean_db, [2016], box_seasons=[2016])


def test_an_empty_season_list_is_refused(clean_db: str) -> None:
    """A no-op that reported success would be indistinguishable from a completed ingest."""
    with pytest.raises(IngestError, match="no seasons requested"):
        ingest.ingest(clean_db, [])


def test_a_failure_partway_through_leaves_no_partial_corpus(
    clean_db: str, schedule: pd.DataFrame
) -> None:
    """The failure mode verification cannot cover.

    Every byte-level and count-level check raises while the file is still a file, so a hash mismatch
    aborts before the first INSERT. What is left is a database error on the fourth table -- which
    would otherwise leave the first three populated, a partial corpus that every count check would
    happily pass because each table is internally consistent.
    """
    players = _player_box_frame(schedule)
    # A team_box row for a game that does not exist: the FK fires after venues, games and
    # player_box have already been written inside the transaction.
    teams = _team_box_frame(schedule)
    teams.loc[teams.index[0], "game_id"] = "no-such-game"

    def _load_box(kind, season, *_a, **_k):
        return players.copy() if kind == "player_box" else teams.copy()

    with (
        patch.object(loader, "load_season", lambda *a, **k: schedule.copy()),
        patch.object(loader, "load_box", _load_box),
        patch.dict(ingest.UNIDENTIFIED_PLAYER_ROWS, {SEASON: 0}),
    ):
        with pytest.raises(sa.exc.IntegrityError):
            ingest.ingest(clean_db, [SEASON], box_seasons=[SEASON])

    assert _counts(clean_db) == {
        "corpus_venues": 0,
        "corpus_games": 0,
        "corpus_player_box": 0,
        "corpus_team_box": 0,
    }


def test_ingest_refuses_a_database_with_no_corpus_tables(pg_admin_url: str) -> None:
    """Migrations own schema; this command owns data (D-045). An unmigrated database is a refusal
    with an instruction, not a stack trace from a missing relation."""
    for url in make_scratch_database(pg_admin_url, "t023_unmigrated_test"):
        with pytest.raises(IngestError, match="run `alembic upgrade head` first"):
            ingest.ingest(url, [SEASON])
