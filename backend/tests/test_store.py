"""T-024 -- the Postgres read layer, and the mechanical enforcement of D-039.

The headline test here is `test_no_as_of_predicate_appears_anywhere_in_store`. D-039 says SQL
narrows by season or team and **no query anywhere carries an as-of predicate**, because the as-of
filter is the integrity control this project rests on and it lives inside `features.py`, where
T-006's property test proves it. A `WHERE game_date < :as_of` in the read layer moves that control
to a call site no property test covers, and its failure is silent: an over-filtered history returns
shrinkage priors, byte-identical to what a legitimate opening-night game produces.

A comment saying "don't do this" is not a control. This file parses `store.py` and fails the build.
That is the same class of enforcement T-006 used for the as-of filter itself and T-009's `ast` check
used for argument forwarding.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from conftest import alembic_config, make_scratch_database
from model import corpus, store
from model.features import (
    FEATURE_NAMES,
    Context,
    Coverage,
    FeatureInputError,
    GameHistory,
    compute_features,
    compute_training_features,
)
from model.store import StoreError

SCRATCH_DB = "t024_store_test"
STORE_PATH = Path(store.__file__)

# 30 franchises, matching `corpus.NBA_TEAMS_PER_SEASON`, plus two phantom ids for the All-Star game.
TEAMS = [str(200 + i) for i in range(30)]
PHANTOMS = ("901", "902")

# Seasons must be pinned in EXPECTED_EXHIBITION_COUNTS, and must shed exactly that many exhibitions,
# or `exclude_exhibitions` refuses them. Both of these are pinned at 1.
SEASON_A = 2024
SEASON_B = 2016


# ══════════════════════════════════════════════════════════════════════════════
# D-039 -- enforced by parsing the module, not by asking nicely
# ══════════════════════════════════════════════════════════════════════════════

# An as-of predicate is fundamentally a date-shaped column in an inequality, or a parameter named
# for the concept. Equality is not matched: `g.game_id = pb.game_id` is an ordinary join, and
# `ORDER BY game_date` is an ordering, not a filter -- neither narrows by as-of.
_AS_OF_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"as_of", re.IGNORECASE),
    re.compile(r"\b\w*(?:date|time|_at|_ts)\w*\s*(?:<=|>=|<|>)", re.IGNORECASE),
    re.compile(r"\bbetween\b", re.IGNORECASE),
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Ids of the Constant nodes that are docstrings, so prose may discuss what code may not do.

    This module's own docstring names `WHERE game_date < :as_of` as the thing to avoid. Explaining a
    hazard has to stay possible or the explanation gets deleted to appease the checker.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _executable_strings(source: str) -> list[str]:
    """Every string literal in the module except docstrings.

    Deliberately not just `sa.text(...)` arguments: this module builds its WHERE clauses by
    appending fragments to a list and joining them, so a predicate can enter the SQL without ever
    appearing inside a `text()` call. Scanning every non-docstring literal is what closes that.
    """
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
    ]


def _as_of_violations(strings: list[str]) -> list[tuple[str, str]]:
    return [
        (pattern.pattern, text)
        for text in strings
        for pattern in _AS_OF_PATTERNS
        if pattern.search(text)
    ]


def test_no_as_of_predicate_appears_anywhere_in_store() -> None:
    """D-039, enforced. This is T-024's security note in executable form.

    If this fails, do not add an exception for the new query. The as-of filter belongs in
    `features.py` behind T-006's property test; a read layer that can express "before this moment"
    is a read layer that can express it *wrongly*, and the wrongness has no symptom.
    """
    violations = _as_of_violations(_executable_strings(STORE_PATH.read_text()))
    assert violations == [], (
        "store.py contains an as-of-shaped predicate, which D-039 forbids anywhere in SQL: "
        f"{violations}"
    )


@pytest.mark.parametrize(
    "offending",
    [
        "SELECT * FROM corpus_games WHERE game_date < :as_of",
        "SELECT * FROM corpus_games WHERE game_date <= :cutoff",
        "AND g.game_date > :start",
        "WHERE game_date BETWEEN :a AND :b",
        "SELECT * FROM t WHERE created_at >= :since",
    ],
)
def test_the_as_of_check_actually_catches_an_as_of_predicate(offending: str) -> None:
    """Non-vacuity, and the most important test in this file.

    A checker that matched nothing would pass `test_no_as_of_predicate_appears_anywhere_in_store`
    forever while the thing it exists to prevent walked straight through. Every shape the real
    predicate could take is asserted to be caught.
    """
    assert _as_of_violations([offending]), f"the D-039 check missed: {offending!r}"


@pytest.mark.parametrize(
    "legitimate",
    [
        "season = ANY(:seasons)",
        "(home_id = ANY(:teams) OR away_id = ANY(:teams))",
        "SELECT game_id, venue_id FROM corpus_games",
        "  ORDER BY g.game_date, pb.game_id, pb.player_id",
        "  JOIN corpus_games g ON g.game_id = pb.game_id",
    ],
)
def test_the_as_of_check_does_not_flag_the_narrowings_d039_permits(legitimate: str) -> None:
    """The other half of non-vacuity. A check that flagged everything would be equally useless --
    it would force the narrowings D-039 explicitly allows to be written some evasive way."""
    assert not _as_of_violations([legitimate]), f"the D-039 check over-fired on: {legitimate!r}"


def test_load_history_cannot_express_a_season_narrowing() -> None:
    """`Coverage.complete_from` is refused for any value but None (F-113), because every look-back
    feature is unbounded in time -- `rest_diff` is not season-scoped and `elo_diff` is running state
    over every prior season. So a season-narrowed history is unusable for features however it is
    chosen, and the way to make that unmistakable is for the function to have no such parameter."""
    import inspect

    params = set(inspect.signature(store.load_history).parameters)
    assert "seasons" not in params
    assert "teams" in params


# ══════════════════════════════════════════════════════════════════════════════
# Against a real corpus
# ══════════════════════════════════════════════════════════════════════════════


def _season_rows(season: int) -> tuple[list[dict], list[dict]]:
    """A synthetic season: 30 franchises in a single round-robin, plus one All-Star game.

    29 games per team clears `MIN_SEASON_GAMES_FOR_A_REAL_TEAM` (20); the two phantom ids play once,
    which is the single exhibition `EXPECTED_EXHIBITION_COUNTS` pins for both seasons used here.
    """
    games: list[dict] = []
    start = datetime(season - 1, 10, 20, tzinfo=UTC)
    n = 0
    for i, home in enumerate(TEAMS):
        for away in TEAMS[i + 1 :]:
            games.append(
                {
                    "game_id": f"{season}-{n}",
                    "season": season,
                    "season_type": 2,
                    "game_date": start + timedelta(hours=n),
                    "home_id": home,
                    "away_id": away,
                    "home_score": 110,
                    "away_score": 104,
                    "neutral_site": False,
                    "venue_id": f"v{i % 3}",
                }
            )
            n += 1
    games.append(
        {
            "game_id": f"{season}-allstar",
            "season": season,
            "season_type": 2,
            "game_date": start + timedelta(hours=n),
            "home_id": PHANTOMS[0],
            "away_id": PHANTOMS[1],
            "home_score": 200,
            "away_score": 190,
            "neutral_site": True,
            "venue_id": "v0",
        }
    )
    venues = [
        {"venue_id": f"v{i}", "full_name": f"Arena {i}", "city": "Denver", "state": "CO",
         "indoor": True}
        for i in range(3)
    ]
    return games, venues


# A nine-man rotation with distinct minutes, plus the two rows user story 12 turns on: a DNP (absent)
# and an active player on zero minutes (dressed, played nothing).
#
# **The rotation varies from game to game on purpose.** With identical participation everywhere,
# `test_a_store_query_returning_future_rows_produces_identical_vectors` would pass no matter what
# the availability source did with an as-of moment -- the future rows would be interchangeable with
# the past ones and there would be nothing for a filter to change. Verified by breaking the filter
# and watching that test go red.
ROTATION_SIZE = 9


def _player_rows(game: dict) -> list[dict]:
    rows: list[dict] = []
    index = int(game["game_id"].rsplit("-", 1)[1]) if game["game_id"][-1].isdigit() else 0
    for team in (game["home_id"], game["away_id"]):
        for i in range(ROTATION_SIZE):
            # Two rotation players sit out in a third of the games, in a pattern that differs by
            # team (the ids are numeric, so this is deterministic across runs -- `hash()` on a str
            # is not), so availability actually moves over the season for both sides of a matchup.
            out = i < 2 and (index + int(team)) % 3 == 0
            rows.append(
                {
                    "game_id": game["game_id"],
                    "team_id": team,
                    "player_id": f"{team}-p{i}",
                    "minutes": None if out else 34.0 - 2.0 * i,
                    "started": i < 5,
                    "did_not_play": out,
                    "points": None if out else 20 - i,
                }
            )
        rows.append(
            {"game_id": game["game_id"], "team_id": team, "player_id": f"{team}-dnp",
             "minutes": None, "started": False, "did_not_play": True, "points": None}
        )
        rows.append(
            {"game_id": game["game_id"], "team_id": team, "player_id": f"{team}-zero",
             "minutes": 0.0, "started": False, "did_not_play": False, "points": 0}
        )
    return rows


@pytest.fixture(scope="session")
def store_db(pg_admin_url: str) -> Iterator[str]:
    from alembic import command

    for url in make_scratch_database(pg_admin_url, SCRATCH_DB):
        command.upgrade(alembic_config(url), "head")
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            all_venues: dict[str, dict] = {}
            for season in (SEASON_A, SEASON_B):
                games, venues = _season_rows(season)
                all_venues.update({v["venue_id"]: v for v in venues})
                conn.execute(
                    sa.text(
                        "INSERT INTO corpus_venues (venue_id, full_name, city, state, indoor)"
                        " VALUES (:venue_id, :full_name, :city, :state, :indoor)"
                        " ON CONFLICT (venue_id) DO NOTHING"
                    ),
                    list(all_venues.values()),
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO corpus_games (game_id, season, season_type, game_date,"
                        " home_id, away_id, home_score, away_score, neutral_site, venue_id)"
                        " VALUES (:game_id, :season, :season_type, :game_date, :home_id,"
                        " :away_id, :home_score, :away_score, :neutral_site, :venue_id)"
                    ),
                    games,
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO corpus_team_box (game_id, team_id, is_home, points)"
                        " VALUES (:game_id, :team_id, :is_home, :points)"
                    ),
                    [
                        row
                        for g in games
                        for row in (
                            {"game_id": g["game_id"], "team_id": g["home_id"], "is_home": True,
                             "points": g["home_score"]},
                            {"game_id": g["game_id"], "team_id": g["away_id"], "is_home": False,
                             "points": g["away_score"]},
                        )
                    ],
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO corpus_player_box (game_id, team_id, player_id, minutes,"
                        " started, did_not_play, points)"
                        " VALUES (:game_id, :team_id, :player_id, :minutes, :started,"
                        " :did_not_play, :points)"
                    ),
                    [row for g in games for row in _player_rows(g)],
                )
        engine.dispose()
        yield url


@pytest.fixture
def conn(store_db: str) -> Iterator[sa.Connection]:
    engine = sa.create_engine(store_db)
    with engine.connect() as connection:
        yield connection
    engine.dispose()


GAMES_PER_SEASON = len(TEAMS) * (len(TEAMS) - 1) // 2  # 435 franchise games


# ── curation on read (D-046) ──────────────────────────────────────────────────


def test_exhibitions_are_excluded_on_read(conn: sa.Connection) -> None:
    """Ingest writes them; the read layer removes them. Both halves are deliberate -- the pinned
    counts count exhibitions, so writing the curated set would make the database's own row count
    disagree with the number that verifies it."""
    games = store.load_games(conn)
    assert len(games) == 2 * GAMES_PER_SEASON
    assert not [g for g in games if g.home_id in PHANTOMS or g.away_id in PHANTOMS]


def test_the_uncurated_set_is_available_but_never_the_default(conn: sa.Connection) -> None:
    """Non-vacuity for the test above: the exhibitions really are in the table."""
    raw = store.load_games(conn, include_exhibitions=True)
    assert len(raw) == 2 * (GAMES_PER_SEASON + 1)


def test_curation_verifies_the_franchise_count_and_the_pinned_exhibition_count(
    conn: sa.Connection,
) -> None:
    """The unnarrowed and season-narrowed paths run the full `exclude_exhibitions` check, so a
    season that lost a franchise or shed the wrong number of exhibitions is refused rather than
    quietly returned."""
    games = store.load_games(conn, seasons=[SEASON_A])
    assert {g.season for g in games} == {SEASON_A}
    assert len(games) == GAMES_PER_SEASON
    corpus.assert_curated(games)


def test_a_team_narrowed_read_still_excludes_exhibitions(conn: sa.Connection) -> None:
    """The trap this module documents: `partition_exhibitions` identifies a phantom by how few games
    it plays, and in a team-narrowed slice every *opponent* appears two or three times. Run naively
    over the slice it would classify nearly the whole thing as exhibition. Identification therefore
    runs over the full corpus and only the exclusion is applied to the slice.
    """
    team = TEAMS[0]
    games = store.load_games(conn, teams=[team])
    assert games, "a team-narrowed read returned nothing -- identification ran over the slice"
    # 29 games per season in a single round-robin, across both seasons.
    assert len(games) == 2 * (len(TEAMS) - 1)
    assert all(team in (g.home_id, g.away_id) for g in games)


def test_a_phantom_team_narrowed_read_returns_nothing_rather_than_its_exhibition(
    conn: sa.Connection,
) -> None:
    assert store.load_games(conn, teams=[PHANTOMS[0]]) == []


# ── narrowing ─────────────────────────────────────────────────────────────────


def test_season_and_team_narrowings_compose(conn: sa.Connection) -> None:
    games = store.load_games(conn, seasons=[SEASON_A], teams=[TEAMS[0]])
    assert {g.season for g in games} == {SEASON_A}
    assert len(games) == len(TEAMS) - 1


def test_games_come_back_in_date_order(conn: sa.Connection) -> None:
    """Every consumer here is temporal. An unordered read would still pass a count assertion."""
    games = store.load_games(conn)
    assert [g.date for g in games] == sorted(g.date for g in games)


def test_an_empty_narrowing_is_refused_rather_than_silently_widened(conn: sa.Connection) -> None:
    """`if teams:` on an empty list reads as "no narrowing" and returns the entire corpus -- the
    opposite of what was asked, and silent."""
    for kwargs in ({"teams": []}, {"seasons": []}):
        with pytest.raises(StoreError, match="empty narrowing"):
            store.load_games(conn, **kwargs)


def test_dates_come_back_timezone_aware(conn: sa.Connection) -> None:
    """`Game.__post_init__` refuses a naive datetime, so this is really asserting that the column
    stayed TIMESTAMPTZ -- a migration that dropped the timezone would fail here rather than three
    modules downstream."""
    game = store.load_games(conn, seasons=[SEASON_A])[0]
    assert game.date.tzinfo is not None


# ── the coverage declaration (F-113) ──────────────────────────────────────────


def test_an_unnarrowed_history_declares_nothing_and_covers_everything(conn: sa.Connection) -> None:
    history = store.load_history(conn)
    assert isinstance(history, GameHistory)
    assert history._coverage == Coverage()


def test_a_team_narrowed_history_declares_its_teams(conn: sa.Connection) -> None:
    history = store.load_history(conn, teams=[TEAMS[0], TEAMS[1]])
    assert history._coverage.teams == frozenset({TEAMS[0], TEAMS[1]})
    assert history._coverage.complete_from is None


def test_compute_features_refuses_a_target_the_narrowed_history_does_not_cover(
    conn: sa.Connection,
) -> None:
    """The whole point of the declaration, end to end through the store.

    Without it, a team-narrowed history computes the uncovered team's features from an empty window
    and returns priors -- indistinguishable from a team that has not played yet.
    """
    context = store.load_context(conn, teams=[TEAMS[0], TEAMS[1]])
    outsider = [
        g
        for g in store.load_games(conn, seasons=[SEASON_A])
        if TEAMS[0] not in (g.home_id, g.away_id) and TEAMS[1] not in (g.home_id, g.away_id)
    ][0]
    with pytest.raises(FeatureInputError, match="does not include"):
        compute_features(context, outsider.matchup, outsider.date)


# ── the other record types ────────────────────────────────────────────────────


def test_player_rows_keep_minutes_and_did_not_play_distinct(conn: sa.Connection) -> None:
    """User story 12, all the way out to the record the availability module will consume."""
    rows = store.load_player_box(conn, seasons=[SEASON_A], teams=[TEAMS[0]])
    assert rows
    dnp = [r for r in rows if r.did_not_play]
    zero = [r for r in rows if r.minutes == 0.0]
    assert dnp and zero
    assert all(r.minutes is None for r in dnp)
    assert all(not r.did_not_play for r in zero)


def test_player_box_season_narrowing_joins_through_games(conn: sa.Connection) -> None:
    """"Season" means what `corpus_games` says. The box parquet carries its own season column and
    they agree today, but two sources for one fact is one source too many."""
    a = store.load_player_box(conn, seasons=[SEASON_A])
    both = store.load_player_box(conn)
    assert 0 < len(a) < len(both)


def test_team_box_carries_both_sides_of_every_game(conn: sa.Connection) -> None:
    rows = store.load_team_box(conn, seasons=[SEASON_A])
    by_game: dict[str, list] = {}
    for row in rows:
        by_game.setdefault(row.game_id, []).append(row)
    assert all(len(v) == 2 for v in by_game.values())
    assert all({r.is_home for r in v} == {True, False} for v in by_game.values())


def test_venues_come_back_keyed_by_id(conn: sa.Connection) -> None:
    venues = store.load_venues(conn)
    assert set(venues) == {"v0", "v1", "v2"}
    assert venues["v0"].city == "Denver"


def test_game_venue_ids_is_a_mapping_not_a_field_on_game(conn: sa.Connection) -> None:
    """Kept off `Game` deliberately: `Game` mirrors the loader's frame one-for-one, and adding a
    field would give the same record type two shapes depending on which side it came from."""
    mapping = store.game_venue_ids(conn, seasons=[SEASON_A])
    assert len(mapping) == GAMES_PER_SEASON + 1  # includes the exhibition; this is a raw mapping
    assert set(mapping.values()) <= {"v0", "v1", "v2"}


# ── refusing an empty or partial corpus ───────────────────────────────────────


def test_a_populated_corpus_passes_the_presence_check(conn: sa.Connection) -> None:
    store.assert_corpus_present(conn, expect_seasons=[SEASON_A, SEASON_B])


def test_a_missing_season_is_refused(conn: sa.Connection) -> None:
    """A partially ingested corpus produces shrinkage priors, not an error -- so the check has to
    happen here, before anything reads."""
    with pytest.raises(StoreError, match=r"missing season\(s\) \[2023\]"):
        store.assert_corpus_present(conn, expect_seasons=[SEASON_A, 2023])


def test_an_empty_corpus_is_refused(pg_admin_url: str) -> None:
    from alembic import command

    for url in make_scratch_database(pg_admin_url, "t024_empty_store_test"):
        command.upgrade(alembic_config(url), "head")
        engine = sa.create_engine(url)
        with engine.connect() as connection:
            with pytest.raises(StoreError, match="corpus_games is empty"):
                store.assert_corpus_present(connection)
        engine.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# T-028 -- the Context, and D-039 demonstrated rather than asserted
# ══════════════════════════════════════════════════════════════════════════════


def test_appearances_are_keyed_by_team_and_dated_from_the_schedule(conn: sa.Connection) -> None:
    """The date is the join's whole reason for existing: `PlayerGame` carries none, because
    participation is a fact about a game and the game owns when it happened."""
    by_team = store.load_appearances(conn, seasons=[SEASON_A])
    assert set(by_team) >= set(TEAMS)
    rows = by_team[TEAMS[0]]
    suffixes = {a.player_id.rsplit("-", 1)[1] for a in rows}
    assert {"dnp", "zero"} < suffixes
    assert sum(1 for s in suffixes if s.startswith("p")) == ROTATION_SIZE
    assert all(a.date.tzinfo is not None for a in rows)

    dates = {g.game_id: g.date for g in store.load_games(conn, seasons=[SEASON_A])}
    for appearance in rows:
        if appearance.game_id in dates:
            assert appearance.date == dates[appearance.game_id]


def test_appearances_keep_a_dnp_and_a_zero_minute_row_apart(conn: sa.Connection) -> None:
    """User story 12, carried through the read layer. Collapsing them is the
    garbage-time-reads-as-injury failure the whole T-021 schema was built around."""
    rows = store.load_appearances(conn, seasons=[SEASON_A])[TEAMS[0]]
    by_kind = {a.player_id.rsplit("-", 1)[1]: a for a in rows}
    assert (by_kind["dnp"].did_not_play, by_kind["dnp"].minutes) == (True, None)
    assert (by_kind["zero"].did_not_play, by_kind["zero"].minutes) == (False, 0.0)


def test_appearances_narrow_by_team(conn: sa.Connection) -> None:
    narrowed = store.load_appearances(conn, teams=[TEAMS[0]])
    assert set(narrowed) == {TEAMS[0]}


def test_game_cities_resolve_through_the_static_venue_table(conn: sa.Connection) -> None:
    cities = store.load_game_cities(conn, seasons=[SEASON_A])
    assert len(cities) == GAMES_PER_SEASON + 1  # the exhibition has a venue too
    assert {city.name for city in cities.values()} == {"Denver"}
    assert all(city.is_high_altitude for city in cities.values())


def test_a_game_with_no_venue_is_refused_rather_than_defaulted(conn: sa.Connection) -> None:
    """Travel and altitude are read off the venue and neither has a defensible default, so a gap is
    an error at load rather than a game with mysteriously zero travel."""
    transaction = conn.begin()
    try:
        conn.execute(
            sa.text("UPDATE corpus_games SET venue_id = NULL WHERE game_id = :g"),
            {"g": f"{SEASON_A}-0"},
        )
        with pytest.raises(StoreError, match="no venue in the corpus"):
            store.load_game_cities(conn)
    finally:
        transaction.rollback()


def test_load_context_builds_all_three_sources(conn: sa.Connection) -> None:
    """The Context refuses a partial build, so getting one back at all is the assertion that games,
    participation and venues all arrived."""
    context = store.load_context(conn)
    games = store.load_games(conn)
    assert context.history.teams == {t for g in games for t in (g.home_id, g.away_id)}
    target = max(games, key=lambda g: g.date)
    assert set(compute_training_features(context, target)) == set(FEATURE_NAMES)


def test_load_context_narrows_by_team_and_never_by_season(conn: sa.Connection) -> None:
    """D-039 plus the reason `load_history` has no season parameter, now applying to all three
    sources: every look-back here is unbounded in time, so a season-narrowed Context is not a
    smaller correct answer but a different and wrong one."""
    import inspect

    params = set(inspect.signature(store.load_context).parameters)
    assert "seasons" not in params
    assert "teams" in params


def test_a_store_query_returning_future_rows_produces_identical_vectors(
    conn: sa.Connection,
) -> None:
    """**T-028's acceptance criterion, and the practical proof of D-039.**

    The store applies no as-of predicate anywhere -- `test_no_as_of_predicate_appears_anywhere_in_store`
    enforces that by parsing the module -- so a Context loaded for a mid-season prediction contains
    every game, box row and venue the corpus holds, including ones dated well after the prediction
    moment. This asserts that carrying that future data changes nothing: the feature vector is
    **bit-identical** to one computed from a Context that was hand-truncated to the past.

    That is what makes it safe for the SQL to narrow by team and never by date. The other reading --
    filtering in the query -- would move the integrity control to a call site no property test
    covers, and its failure would be silent, because an over-filtered history returns shrinkage
    priors and priors are what a legitimate opening night produces.
    """
    games = store.load_games(conn)
    appearances = store.load_appearances(conn)

    # Three quarters of the way through the later season: both sides have real history behind them,
    # and a quarter of that season is still dated after the prediction moment.
    season = sorted((g for g in games if g.season == SEASON_A), key=lambda g: g.date)
    target = season[int(len(season) * 0.75)]
    assert any(g.date > target.date for g in games), "the fixture must contain future rows"
    for team in (target.home_id, target.away_id):
        assert any(
            g.date < target.date and team in (g.home_id, g.away_id) for g in games
        ), f"{team} must have history, or the vectors would match for the wrong reason"

    with_future = compute_training_features(
        store.load_context(conn), target
    )

    past_games = [g for g in games if g.date < target.date]
    past_appearances = {
        team: [a for a in rows if a.date < target.date] for team, rows in appearances.items()
    }
    without_future = compute_features(
        Context(past_games, past_appearances), target.matchup, target.date
    )

    assert with_future == without_future
