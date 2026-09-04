"""Corpus curation (F-042) -- separating NBA games from the exhibitions the source mixes in.

The pinned sportsdataverse schedules carry **42 distinct team ids across 2022-2026, not 30**. The
extra twelve belong to All-Star teams: they are `season_type = 2` (regular season) upstream, which is
why T-005's pinned counts include them and why the loader is right not to filter them out -- those
counts are the tripwire that proves the download is intact, and quietly changing what they count
would defeat it.

But they are not NBA games, and they must not reach the model. Ten of them exist:

    season | NBA team ids | games each | exhibition ids | games each | exhibitions
      2022 |      30      |    >= 82   |        2       |    <= 1    |      1
      2023 |      30      |    >= 82   |        2       |    <= 1    |      1
      2024 |      30      |    >= 82   |        2       |    <= 1    |      1
      2025 |      30      |    >= 82   |        4       |    <= 2    |      3
      2026 |      30      |    >= 82   |        3       |    <= 3    |      4

6,615 verified games -> **6,605 modeling games**.

## Why identification is by games-played, not by a hardcoded list

A list of the ten `game_id`s (or the twelve team ids) would be exact today and silently wrong later:
D-017 retrains on a new season before the 2026-27 opener, that season will contain its own All-Star
game with its own fresh ids, and a hardcoded list would let it through with no signal at all. Games
played per season separates the two populations by a margin that is not close -- **3 versus 82** --
so the threshold below is nowhere near either edge, and it keeps working for seasons nobody has
downloaded yet.

## What this rule structurally cannot see (F-069)

It classifies **teams**, never games, so an exhibition played *between two franchise ids* is
invisible to it -- an All-Star game fielding real team ids, or a preseason friendly, would pass
through with both assertions satisfied. That is not a live gap: the pinned corpus carries only
`season_type` 2/3/5 (no preseason rows at all) and every one of the ten removed games is a
phantom-id game. It is a limitation to know before trusting this module with a differently-shaped
source. The clean closure, if that day comes, is to carry `season_type` through -- the loader already
parses it, and `dataset.REQUIRED_FRAME_COLUMNS` drops it before this module could ever see it.

The identification is also *verified* rather than trusted, in the same spirit as T-005's count
tripwire: after filtering, every season must be left with exactly 30 team ids, and every pinned
season must have shed exactly the expected number of games. If the upstream shape changes -- a real
expansion team, a shortened season, a new exhibition format -- the assertion fires instead of the
model quietly training on the wrong rows.

Standard library only, deliberately (D-021). This decides what T-009 trains on, so it must be
covered by tests that actually run in CI -- and `dataset.py`'s tests do not, because pandas is a
training-only dependency. `dataset.py` calls this; the logic lives here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from .features import Game

# A real NBA season fields exactly 30 franchises.
NBA_TEAMS_PER_SEASON: int = 30

# A team id appearing in fewer than this many games in a season is not a franchise. Observed gap in
# the pinned corpus: exhibition ids appear in at most 3 games, real ones in at least 82 (the full
# regular season). Anything in 20..81 would be a shape this module has never seen, and the
# post-filter assertions below are what turn that into a loud failure rather than a silent one.
MIN_SEASON_GAMES_FOR_A_REAL_TEAM: int = 20

# Per-season exhibition counts, measured from the corpus that produced the verified 6,615 total.
# A season absent from this dict is refused rather than curated unverified -- the same discipline as
# the loader's EXPECTED_COMPLETED_COUNTS (F-028). Adding a season (D-017's 2026-27 retrain) means
# measuring its count by hand and pinning it here, deliberately, in a reviewed commit.
EXPECTED_EXHIBITION_COUNTS: dict[int, int] = {
    # T-024 added the D-037 warm-up seasons, measured 2026-09-04 against the ingested corpus. Each
    # is left with exactly 30 franchises after exclusion, the same shape the modeling seasons have.
    # Pinned here rather than assumed: `exclude_exhibitions` refuses an unpinned season outright
    # (F-028's lesson), and the warm-up seasons now reach it through `store`.
    2016: 1,
    2017: 1,
    2018: 2,
    2019: 2,
    2022: 1,
    2023: 1,
    2024: 1,
    2025: 3,
    2026: 4,
}

TOTAL_EXPECTED_EXHIBITIONS: int = sum(EXPECTED_EXHIBITION_COUNTS.values())  # 16

# F-070: `expected=EXPECTED_EXHIBITION_COUNTS` as a default binds the dict OBJECT at definition time,
# so rebinding the module attribute (what a monkeypatching test does) was silently ignored while
# in-place mutation of the same dict took effect. A future test that patches the constant would have
# passed vacuously -- the precise class of vacuous test F-051..F-059 were all about. The sentinel
# defers the lookup to call time.
_USE_PINNED_COUNTS = object()


class CorpusIntegrityError(RuntimeError):
    """Raised when the curated corpus does not have the shape this module asserts it must."""


def exhibition_team_seasons(
    games: Sequence[Game], *, min_games: int = MIN_SEASON_GAMES_FOR_A_REAL_TEAM
) -> frozenset[tuple[int, str]]:
    """`(season, team_id)` pairs where that team plays too few games *in that season* to be a
    franchise.

    Keyed by season, and **not** collapsed to a bare set of team ids (F-065). The first version
    returned a union of ids and applied it across every season, so a team below the threshold in one
    season was stripped from all of them. That is not a corner case: any partial season does it. With
    2022-2025 complete plus the first 150 games of 2026, every team is under-played *in 2026*, so the
    union flagged 39 ids and `exclude_exhibitions` discarded **all 5,439 games and raised nothing**.
    Judging a team only against the season it appears in is both the correct rule and what the
    function's name always claimed.
    """
    per_season: dict[int, Counter[str]] = defaultdict(Counter)
    for game in games:
        per_season[game.season][game.home_id] += 1
        per_season[game.season][game.away_id] += 1
    return frozenset(
        (season, team)
        for season, counts in per_season.items()
        for team, played in counts.items()
        if played < min_games
    )


def partition_exhibitions(
    games: Sequence[Game], *, min_games: int = MIN_SEASON_GAMES_FOR_A_REAL_TEAM
) -> tuple[list[Game], list[Game]]:
    """Split into (nba_games, exhibitions). A game is an exhibition if **either** side is not a
    franchise *in that game's season* -- in the pinned corpus no game mixes the two populations, but
    a rule that only caught both-sides cases would silently pass one that did."""
    exhibitions_by_season = exhibition_team_seasons(games, min_games=min_games)
    nba: list[Game] = []
    exhibitions: list[Game] = []
    for game in games:
        is_exhibition = (game.season, game.home_id) in exhibitions_by_season or (
            game.season,
            game.away_id,
        ) in exhibitions_by_season
        (exhibitions if is_exhibition else nba).append(game)
    return nba, exhibitions


def exclude_exhibitions(
    games: Sequence[Game],
    *,
    min_games: int = MIN_SEASON_GAMES_FOR_A_REAL_TEAM,
    teams_per_season: int = NBA_TEAMS_PER_SEASON,
    expected: dict[int, int] | None | object = _USE_PINNED_COUNTS,
) -> list[Game]:
    """The modeling corpus: `games` minus the exhibitions, verified.

    Two assertions, both of which must hold, and both of which exist because a silent miscount here
    would be invisible downstream -- it would show up only as a slightly disappointing headline
    number in T-009, which is indistinguishable from "the features carry no signal":

      1. every season **present in the input** is left with exactly `teams_per_season` team ids --
         counted over the input's seasons, not the survivors', so a season wiped out entirely reads
         as 0 rather than as absent (F-065);
      2. every season sheds exactly the number of games pinned in `expected`, and a season absent
         from `expected` is refused outright rather than curated unverified (F-028's lesson).

    Pass `expected=None` to skip only the second check -- for a season being measured for the first
    time before its count is pinned. The first check always runs.

    **What this does NOT detect** (stated because D-025 reads stronger than it is): a partial season
    whose teams have each already cleared `min_games`. At a ~1,100-game 2026 prefix every team is
    over the threshold and all four All-Star games have been played, so curation looks perfectly
    healthy and returns a truncated corpus without complaint. The tripwire for *that* is the loader's
    `EXPECTED_COMPLETED_COUNTS`, which is why this module deliberately does not duplicate it.
    """
    if expected is _USE_PINNED_COUNTS:
        expected = EXPECTED_EXHIBITION_COUNTS  # resolved at call time, not at definition (F-070)

    nba, exhibitions = partition_exhibitions(games, min_games=min_games)

    teams_by_season: dict[int, set[str]] = defaultdict(set)
    for game in nba:
        teams_by_season[game.season].update((game.home_id, game.away_id))
    # F-065: iterate the seasons present in the INPUT, not the ones surviving in `nba`. Built from
    # `nba`, this check was vacuous exactly when it mattered most: a season whose every team fell
    # below the threshold contributed no entry at all, so `wrong` was empty and a wiped-out season
    # passed silently. A season that loses all its games must read as 0 team ids, not as absent.
    seasons_in_input = {game.season for game in games}
    wrong = {
        season: len(teams_by_season.get(season, set()))
        for season in seasons_in_input
        if len(teams_by_season.get(season, set())) != teams_per_season
    }
    if wrong:
        raise CorpusIntegrityError(
            f"after excluding exhibitions, season(s) {wrong} do not have exactly "
            f"{teams_per_season} team ids. Either the upstream shape changed (expansion, a new "
            f"exhibition format, a partial season) or MIN_SEASON_GAMES_FOR_A_REAL_TEAM={min_games} "
            "no longer separates the two populations. Do not adjust the threshold to make this "
            "pass without checking which."
        )

    if expected is not None:
        removed = Counter(g.season for g in exhibitions)
        seasons = {g.season for g in games}
        unpinned = sorted(seasons - set(expected))
        if unpinned:
            raise CorpusIntegrityError(
                f"season(s) {unpinned} have no pinned exhibition count in "
                "EXPECTED_EXHIBITION_COUNTS -- refusing to curate an unpinned season. Measure the "
                "count by hand and pin it deliberately (see D-017's retrain)."
            )
        mismatches = {
            season: (removed.get(season, 0), count)
            for season, count in expected.items()
            if season in seasons and removed.get(season, 0) != count
        }
        if mismatches:
            raise CorpusIntegrityError(
                "exhibition counts do not match the pinned expected values "
                f"(season: got, expected): {mismatches}"
            )

    return nba


def assert_curated(
    games: Sequence[Game],
    *,
    min_games: int = MIN_SEASON_GAMES_FOR_A_REAL_TEAM,
    teams_per_season: int = NBA_TEAMS_PER_SEASON,
) -> None:
    """Raise unless `games` looks already curated. **T-007 and T-009 must call this on their input.**

    `load_games` excludes by default (D-025(3)), but that is a property of the *producer*, and three
    documented paths reach a consumer uncurated with no flag and no assertion (F-067):
    `games_from_frame(load_completed_games(...))`, `load_games(include_exhibitions=True)`, and any
    hand-assembled list. Contamination has no symptom downstream — an exhibition row has
    ordinary-looking features and a coin-flip label — so a default is not a guarantee; this is what
    makes it one at the point of use.

    Checks **both** invariants `exclude_exhibitions` enforces:
      1. no `(season, team)` is under-played (identification);
      2. every season carries exactly `teams_per_season` ids.

    F-077: the first version checked only (1). Its docstring justified skipping (2) on
    `exclude_exhibitions` being non-idempotent — true of the *pinned-count* assertion, which counts
    games removed and therefore reads 0 on already-clean input, but **not** of the team-count
    assertion, which is idempotent by construction. Dropping both together meant this certified two
    shapes `exclude_exhibitions` refuses loudly on identical input: a season missing a whole franchise
    (29 ids), and a 21-game non-NBA block that clears the threshold (32 ids). Only the pinned-count
    check is genuinely un-re-runnable, and it is the only one still omitted.

    Known limitation (F-079, accepted): a legitimately curated **partial** season fails this, because
    its teams have not yet played `min_games`. Callers evaluating mid-season data pass a lower
    `min_games` deliberately — but note there is no floor on that knob, so `min_games=0` disables
    check (1) entirely.
    """
    if not isinstance(games, Sequence) or isinstance(games, (str, bytes)):
        # F-078: this is F-045's hazard, and it was reintroduced here. A generator satisfies the
        # declared type visually, passes the check, and is *consumed by the check itself* — leaving
        # the caller with zero games and no error. It only bites on CLEAN input (an uncurated
        # generator raises for the right reason), which is exactly what makes it silent.
        raise CorpusIntegrityError(
            f"games must be a Sequence (list/tuple), got {type(games).__name__} — a one-shot "
            "iterator would be consumed by this check, leaving the caller nothing."
        )
    if not games:
        # An empty collection trivially satisfies both invariants, so passing it would certify
        # nothing as curated — and would hide the emptiness a consumed generator just caused.
        raise CorpusIntegrityError("games is empty — there is nothing to certify as curated.")

    stragglers = exhibition_team_seasons(games, min_games=min_games)
    if stragglers:
        raise CorpusIntegrityError(
            f"{len(stragglers)} (season, team) pair(s) play fewer than {min_games} games "
            f"(first few: {sorted(stragglers)[:5]}) — this collection has not been curated. Obtain "
            "it from dataset.load_games(), which excludes exhibitions by default (D-025)."
        )

    teams_by_season: dict[int, set[str]] = defaultdict(set)
    for game in games:
        teams_by_season[game.season].update((game.home_id, game.away_id))
    wrong = {s: len(t) for s, t in teams_by_season.items() if len(t) != teams_per_season}
    if wrong:
        raise CorpusIntegrityError(
            f"season(s) {wrong} do not carry exactly {teams_per_season} team ids — curation did not "
            f"produce this shape. More than {teams_per_season} means an exhibition id survived."
        )


def apply_default_curation(
    games: Sequence[Game], *, include_exhibitions: bool = False
) -> list[Game]:
    """The exclude-by-default policy of D-025(3), expressed here rather than in `dataset.load_games`.

    F-092/F-093: it used to live in `load_games`, which is unreachable from the gate — `dataset.py`
    needs pandas and its tests `importorskip` out of CI. The first attempt to close that pinned the
    *signature default* with an `ast` check, which is not the same thing: inverting the flag in the
    body, or deleting the exclusion outright, both left the default reading `False` and both still
    reported green under CI's environment. F-061's own docstring had named both hazards — "flipping
    the default to True, **or inverting the flag**" — and only one was closed.

    Putting the decision in a standard-library module makes it *behaviourally* testable where the
    gate can actually run it. `load_games` now forwards to this and holds no policy of its own.
    """
    return list(games) if include_exhibitions else exclude_exhibitions(games)
