"""Postgres read layer for the corpus (T-024).

Turns `corpus_*` rows back into the record types the pipeline already uses, so nothing downstream of
here knows or cares that the corpus moved into a database (D-038).

## D-039: SQL narrows. It never filters by as-of.

**No query in this module carries an as-of predicate, and that is not a style preference.** The
as-of filter is the integrity control this whole project rests on, and it lives inside
`features.py`, where T-006's property test proves it holds. Writing `WHERE game_date < :as_of` here
would move that control into a call site -- one that no property test covers, that every future
caller could get subtly wrong, and whose failure has no symptom: an over-filtered history returns
shrinkage priors, byte-identical to what a legitimate opening-night game produces.

So this module narrows by **season** and by **team** and by nothing else. `test_store.py` enforces
that mechanically, by parsing this file's AST and refusing any date-shaped inequality in any string
it contains -- the same class of check T-006 used for the as-of filter and T-009's `ast` check used
for argument forwarding. A comment saying "don't do this" is not a control; a test that fails the
build is.

## The one narrowing you cannot use for features

`features.Coverage` (F-113) refuses **any** history that declares a `complete_from`, and it is right
to: every look-back feature here is unbounded in time. `rest_diff` is deliberately not
season-scoped, and D-032's `elo_diff` is running state across every prior season including the
2016-2019 warm-up. A season-narrowed history is therefore wrong for feature computation no matter
how the narrowing is chosen.

Hence the split below. `load_games` narrows by season *or* team, because plenty of callers -- fold
construction, reporting, evaluation -- legitimately want one season. `load_history`, which is what
`compute_features` consumes, narrows **by team only** and declares exactly that. It cannot express a
season narrowing, so no caller can ask it for one.

## Curation on read (D-046)

Verified ingest makes the database trustworthy; the *curation* invariant is still re-asserted here,
because exhibitions are written and excluded on read by design (see `ingest.py`).

There is a trap in doing that on a narrowed result. `corpus.partition_exhibitions` identifies a
phantom team by how few games it plays, and in a **team-narrowed** slice every *opponent* appears
only two or three times -- so run naively, it would classify nearly the entire slice as exhibition.
Identification therefore always runs over the **full** corpus, and only the exclusion is applied to
the narrowed rows. The 30-franchises-per-season assertion is skipped for team-narrowed reads (a
slice of one team cannot have thirty) and runs in full for every other read.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import sqlalchemy as sa

from . import corpus
from .availability import Appearance
from .features import Context, Coverage, Game, GameHistory
from .venues import City, city_for


class StoreError(RuntimeError):
    """Raised when the corpus tables cannot answer a query the way this module promises."""


@dataclass(frozen=True, slots=True)
class PlayerGame:
    """One player's participation in one game -- the input to `availability` (T-027, D-035).

    `minutes` and `did_not_play` are carried separately and must stay that way: a player who logged
    zero minutes in a blowout is not a player who was absent (user story 12). The corpus holds
    31,769 null-minute rows and 700 active rows at exactly zero minutes, and collapsing them is the
    garbage-time-reads-as-injury failure T-027 exists to avoid.
    """

    game_id: str
    team_id: str
    player_id: str
    minutes: float | None
    started: bool
    did_not_play: bool
    points: int | None


@dataclass(frozen=True, slots=True)
class TeamGame:
    """One team's box-score line in one game."""

    game_id: str
    team_id: str
    is_home: bool
    points: int | None


@dataclass(frozen=True, slots=True)
class Venue:
    """A venue as the corpus records it. Geography -- coordinates, elevation, timezone -- is
    `venues`' (T-026) static table, not this; here the useful field is `city`, which is the join."""

    venue_id: str
    full_name: str
    city: str
    state: str | None
    indoor: bool | None


def _as_tuple(values: Iterable[object] | None) -> tuple | None:
    if values is None:
        return None
    out = tuple(values)
    if not out:
        # An empty narrowing is almost always a bug in the caller -- an empty list of teams reads as
        # "no narrowing" under a naive `if teams:` and returns the entire corpus, which is the
        # opposite of what was asked. Refuse rather than guess which was meant.
        raise StoreError(
            "an empty narrowing was requested -- pass None to mean 'everything', or a non-empty "
            "collection to mean 'these'. An empty collection is ambiguous and would silently widen "
            "to the whole corpus."
        )
    return out


def _game_from_row(row: sa.Row) -> Game:
    """One `corpus_games` row -> one `Game`.

    A field-by-field conversion, deliberately: every validation that matters (tied score, naive
    date, a team playing itself) belongs to `Game.__post_init__` and fires here, exactly as it does
    for `dataset.games_from_frame`. A row the database accepted but that the pipeline cannot use
    fails at conversion rather than producing features.
    """
    return Game(
        game_id=row.game_id,
        date=row.game_date,
        season=row.season,
        home_id=row.home_id,
        away_id=row.away_id,
        home_score=row.home_score,
        away_score=row.away_score,
        neutral_site=row.neutral_site,
    )


# Column list shared by every game query. Named explicitly rather than `SELECT *` so an added column
# cannot change what this module reads.
_GAME_COLUMNS = (
    "game_id, season, season_type, game_date, home_id, away_id, "
    "home_score, away_score, neutral_site, venue_id"
)


def _all_games(conn: sa.Connection) -> list[Game]:
    """Every game in the corpus, uncurated. Used only to identify exhibitions.

    Loading the whole table to curate a slice looks wasteful and is not: the corpus is 11,870 rows,
    and identification is *only* correct over the full population -- see the module docstring.
    """
    rows = conn.execute(
        sa.text(f"SELECT {_GAME_COLUMNS} FROM corpus_games ORDER BY game_date, game_id")
    ).all()
    return [_game_from_row(r) for r in rows]


def _exhibition_team_seasons(conn: sa.Connection) -> frozenset[tuple[int, str]]:
    return corpus.exhibition_team_seasons(_all_games(conn))


def load_games(
    conn: sa.Connection,
    *,
    seasons: Iterable[int] | None = None,
    teams: Iterable[str] | None = None,
    include_exhibitions: bool = False,
) -> list[Game]:
    """Completed games, narrowed by season and/or team, curated on read.

    Args:
        seasons: seasons to include. `None` = every season, warm-up included.
        teams: team ids; a game is included when **either** side is one of them. `None` = every team.
        include_exhibitions: return the uncurated set. For inspecting what ingest wrote; never for
            modeling.

    Note there is no `as_of`, no `before`, and no date range, and that this is load-bearing rather
    than an omission -- see the module docstring and D-039.
    """
    seasons = _as_tuple(seasons)
    teams = _as_tuple(teams)

    clauses: list[str] = []
    params: dict[str, object] = {}
    if seasons is not None:
        clauses.append("season = ANY(:seasons)")
        params["seasons"] = list(seasons)
    if teams is not None:
        clauses.append("(home_id = ANY(:teams) OR away_id = ANY(:teams))")
        params["teams"] = list(teams)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        sa.text(f"SELECT {_GAME_COLUMNS} FROM corpus_games{where} ORDER BY game_date, game_id"),
        params,
    ).all()
    games = [_game_from_row(r) for r in rows]

    if include_exhibitions:
        return games

    if teams is None:
        # Every season present is whole, so the full check applies: 30 franchises per season AND the
        # pinned per-season exhibition count. This is the strong path and it is the default one.
        return corpus.exclude_exhibitions(games)

    # Team-narrowed. Identification must come from the full corpus (see the module docstring), and
    # the 30-franchises assertion cannot hold on a slice of one team, so it is skipped -- explicitly,
    # here, rather than by calling a checker that silently passes.
    exhibitions = _exhibition_team_seasons(conn)
    return [
        g
        for g in games
        if (g.season, g.home_id) not in exhibitions and (g.season, g.away_id) not in exhibitions
    ]


def load_history(conn: sa.Connection, *, teams: Iterable[str] | None = None) -> GameHistory:
    """The history `compute_features` consumes, with its coverage declared (F-113).

    **Narrows by team only, on purpose.** `Coverage.complete_from` is refused by
    `features._require_covers` for any value but `None`, because every look-back feature here is
    unbounded in time -- `rest_diff` is not season-scoped and D-032's `elo_diff` is running state
    over every prior season including the 2016-2019 warm-up. A season-narrowed history is therefore
    unusable for features no matter how it is chosen, and the way to make that unmistakable is for
    this function to have no parameter that could express it.

    The returned history declares `teams=` when narrowed, so `compute_features` refuses a target
    involving a team this history does not contain every game for -- the failure that is otherwise
    silent, because an under-covered team yields shrinkage priors indistinguishable from a team that
    simply has not played yet.
    """
    teams = _as_tuple(teams)
    games = load_games(conn, teams=teams)
    coverage = Coverage(teams=frozenset(teams)) if teams is not None else Coverage()
    return GameHistory(games, coverage=coverage)


def load_player_box(
    conn: sa.Connection,
    *,
    seasons: Iterable[int] | None = None,
    teams: Iterable[str] | None = None,
) -> list[PlayerGame]:
    """Player participation, narrowed by season and/or team.

    Season narrowing joins through `corpus_games` rather than reading a season column off the box
    table, so "season" means one thing in this module: what `corpus_games` says. The box parquet
    carries its own season column and they agree today, but two sources for one fact is one source
    too many.
    """
    seasons = _as_tuple(seasons)
    teams = _as_tuple(teams)

    clauses: list[str] = []
    params: dict[str, object] = {}
    if seasons is not None:
        clauses.append("g.season = ANY(:seasons)")
        params["seasons"] = list(seasons)
    if teams is not None:
        clauses.append("pb.team_id = ANY(:teams)")
        params["teams"] = list(teams)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        sa.text(
            "SELECT pb.game_id, pb.team_id, pb.player_id, pb.minutes, pb.started,"
            "       pb.did_not_play, pb.points"
            "  FROM corpus_player_box pb"
            "  JOIN corpus_games g ON g.game_id = pb.game_id"
            f"{where}"
            "  ORDER BY g.game_date, pb.game_id, pb.player_id"
        ),
        params,
    ).all()
    return [
        PlayerGame(
            game_id=r.game_id,
            team_id=r.team_id,
            player_id=r.player_id,
            minutes=r.minutes,
            started=r.started,
            did_not_play=r.did_not_play,
            points=r.points,
        )
        for r in rows
    ]


def load_team_box(
    conn: sa.Connection,
    *,
    seasons: Iterable[int] | None = None,
    teams: Iterable[str] | None = None,
) -> list[TeamGame]:
    """Team box-score lines, narrowed by season and/or team."""
    seasons = _as_tuple(seasons)
    teams = _as_tuple(teams)

    clauses: list[str] = []
    params: dict[str, object] = {}
    if seasons is not None:
        clauses.append("g.season = ANY(:seasons)")
        params["seasons"] = list(seasons)
    if teams is not None:
        clauses.append("tb.team_id = ANY(:teams)")
        params["teams"] = list(teams)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        sa.text(
            "SELECT tb.game_id, tb.team_id, tb.is_home, tb.points"
            "  FROM corpus_team_box tb"
            "  JOIN corpus_games g ON g.game_id = tb.game_id"
            f"{where}"
            "  ORDER BY g.game_date, tb.game_id, tb.team_id"
        ),
        params,
    ).all()
    return [
        TeamGame(game_id=r.game_id, team_id=r.team_id, is_home=r.is_home, points=r.points)
        for r in rows
    ]


def load_venues(conn: sa.Connection) -> dict[str, Venue]:
    """Every venue in the corpus, keyed by `venue_id`.

    Not narrowed: there are 45 of them, and T-026's static geography table needs to cover all of
    them regardless of which games are being looked at.
    """
    rows = conn.execute(
        sa.text(
            "SELECT venue_id, full_name, city, state, indoor FROM corpus_venues ORDER BY venue_id"
        )
    ).all()
    return {
        r.venue_id: Venue(
            venue_id=r.venue_id,
            full_name=r.full_name,
            city=r.city,
            state=r.state,
            indoor=r.indoor,
        )
        for r in rows
    }


def game_venue_ids(
    conn: sa.Connection, *, seasons: Iterable[int] | None = None
) -> dict[str, str | None]:
    """`game_id` -> `venue_id`, for the travel and altitude features (T-026).

    Kept off `Game` deliberately. `Game` mirrors the loader's normalized frame one-for-one and is
    what `dataset.games_from_frame` produces; adding a field here would mean the same record type
    had two shapes depending on which side of the store it came from.
    """
    seasons = _as_tuple(seasons)
    where = " WHERE season = ANY(:seasons)" if seasons is not None else ""
    params = {"seasons": list(seasons)} if seasons is not None else {}
    rows = conn.execute(
        sa.text(f"SELECT game_id, venue_id FROM corpus_games{where}"), params
    ).all()
    return {r.game_id: r.venue_id for r in rows}


def assert_corpus_present(conn: sa.Connection, *, expect_seasons: Sequence[int] | None = None) -> None:
    """Refuse a database that has not been ingested, before a caller reads priors out of emptiness.

    An empty corpus is the same silent failure `Coverage` guards against one layer up: every feature
    falls back to its prior and the model reports plausible-looking numbers computed from nothing.
    """
    count = conn.execute(sa.text("SELECT count(*) FROM corpus_games")).scalar_one()
    if count == 0:
        raise StoreError(
            "corpus_games is empty -- run `python -m model.ingest` before reading. An empty corpus "
            "does not raise downstream; it silently yields a vector of priors for every game."
        )
    if expect_seasons is not None:
        present = set(
            conn.execute(sa.text("SELECT DISTINCT season FROM corpus_games")).scalars().all()
        )
        missing = sorted(set(expect_seasons) - present)
        if missing:
            raise StoreError(
                f"corpus_games is missing season(s) {missing} -- ingest them before reading. A "
                "partially ingested corpus produces shrinkage priors, not an error."
            )


def load_appearances(
    conn: sa.Connection,
    *,
    seasons: Iterable[int] | None = None,
    teams: Iterable[str] | None = None,
) -> dict[str, list[Appearance]]:
    """`team_id` -> that team's `availability.Appearance` records, dated from `corpus_games`.

    The date is the join's whole reason for existing. `PlayerGame` carries no date -- participation
    is a fact about a game, and the game owns when it happened -- but `availability` filters by
    instant, so the box rows have to arrive already carrying the game's tip-off. Reading a date off
    the box parquet's own column instead would put two sources behind one fact, which is one too
    many (the same rule `load_player_box` applies to `season`).

    Narrowing follows D-039: by season or by team, never by an as-of predicate. **Narrowing by team
    is what a Context wants; narrowing by season is not** -- availability looks back across a season
    boundary the same way rest does, so a season-narrowed map makes every opening night read as a
    cold start. Season narrowing exists here for the same reason it exists on `load_player_box`:
    exploratory reads, not feature computation.
    """
    seasons = _as_tuple(seasons)
    teams = _as_tuple(teams)

    clauses: list[str] = []
    params: dict[str, object] = {}
    if seasons is not None:
        clauses.append("g.season = ANY(:seasons)")
        params["seasons"] = list(seasons)
    if teams is not None:
        clauses.append("pb.team_id = ANY(:teams)")
        params["teams"] = list(teams)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    rows = conn.execute(
        sa.text(
            "SELECT pb.game_id, pb.team_id, pb.player_id, pb.minutes, pb.did_not_play,"
            "       g.game_date"
            "  FROM corpus_player_box pb"
            "  JOIN corpus_games g ON g.game_id = pb.game_id"
            f"{where}"
            "  ORDER BY g.game_date, pb.game_id, pb.player_id"
        ),
        params,
    ).all()

    by_team: dict[str, list[Appearance]] = {}
    for row in rows:
        by_team.setdefault(row.team_id, []).append(
            Appearance(
                game_id=row.game_id,
                date=row.game_date,
                player_id=row.player_id,
                minutes=row.minutes,
                did_not_play=row.did_not_play,
            )
        )
    return by_team


def load_game_cities(
    conn: sa.Connection, *, seasons: Iterable[int] | None = None
) -> dict[str, City]:
    """`game_id` -> the `venues.City` it was played in, resolved through `corpus_venues`.

    The join D-036 is built on, done once. `venues.city_for` has no fallback and raises on a city it
    does not know, which is the behaviour that matters here: an unrecognized venue fails at load,
    naming the city, rather than surfacing later as a game with mysteriously zero travel. A game
    whose `venue_id` is null fails the same way -- the corpus has none, and the moment it does, the
    right answer is to fix the ingest rather than to quietly average over it.
    """
    seasons = _as_tuple(seasons)
    where = " WHERE g.season = ANY(:seasons)" if seasons is not None else ""
    params = {"seasons": list(seasons)} if seasons is not None else {}
    rows = conn.execute(
        sa.text(
            "SELECT g.game_id, g.venue_id, v.city, v.state"
            "  FROM corpus_games g"
            "  LEFT JOIN corpus_venues v ON v.venue_id = g.venue_id"
            f"{where}"
            "  ORDER BY g.game_id"
        ),
        params,
    ).all()

    cities: dict[str, City] = {}
    unlocated = []
    for row in rows:
        if row.venue_id is None or row.city is None:
            unlocated.append(row.game_id)
            continue
        cities[row.game_id] = city_for(row.city, row.state)
    if unlocated:
        raise StoreError(
            f"{len(unlocated)} game(s) have no venue in the corpus (first few: {unlocated[:5]}) -- "
            "travel and altitude are read off the venue and neither has a defensible default. "
            "Re-run the ingest rather than computing features over a gap."
        )
    return cities


def load_context(conn: sa.Connection, *, teams: Iterable[str] | None = None) -> Context:
    """The `Context` `compute_features` consumes, assembled from the corpus in one place.

    **Narrows by team only**, for the reason `load_history` spells out and which now applies to all
    three sources: every look-back here is unbounded in time. Elo is running state over every prior
    season including D-037's warm-up, rest reads back across a season boundary, and availability
    reads back fifteen games. A season-narrowed Context is not a smaller correct answer, it is a
    different and wrong one -- so this function has no parameter that could express it.

    Loading the participation map and the venue join here rather than at each call site is what
    makes the Context's "no partial Context" rule enforceable: there is one supported way to build
    one from Postgres, and it cannot produce a Context missing a source.
    """
    teams = _as_tuple(teams)
    history = load_history(conn, teams=teams)
    # Deliberately NOT narrowed by team. Availability is a property of a team's own games, so a
    # team-narrowed participation map is complete for the teams it covers -- but `Context` checks
    # its map against the history's teams, and a team-narrowed history still contains the *opponents*
    # of every game those teams played. Loading all participation is the honest way to satisfy that;
    # it is one query either way.
    appearances = load_appearances(conn)
    return Context(history, appearances, load_game_cities(conn))
