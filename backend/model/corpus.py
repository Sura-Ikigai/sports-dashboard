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
    2022: 1,
    2023: 1,
    2024: 1,
    2025: 3,
    2026: 4,
}

TOTAL_EXPECTED_EXHIBITIONS: int = sum(EXPECTED_EXHIBITION_COUNTS.values())  # 10


class CorpusIntegrityError(RuntimeError):
    """Raised when the curated corpus does not have the shape this module asserts it must."""


def exhibition_team_ids(
    games: Sequence[Game], *, min_games: int = MIN_SEASON_GAMES_FOR_A_REAL_TEAM
) -> frozenset[str]:
    """Team ids that play too few games in a season to be a franchise.

    Evaluated **per season** and then unioned: a team that is real in one season and absent from
    another must not be judged on its total across the corpus.
    """
    per_season: dict[int, Counter[str]] = defaultdict(Counter)
    for game in games:
        per_season[game.season][game.home_id] += 1
        per_season[game.season][game.away_id] += 1
    return frozenset(
        team
        for counts in per_season.values()
        for team, played in counts.items()
        if played < min_games
    )


def partition_exhibitions(
    games: Sequence[Game], *, min_games: int = MIN_SEASON_GAMES_FOR_A_REAL_TEAM
) -> tuple[list[Game], list[Game]]:
    """Split into (nba_games, exhibitions). A game is an exhibition if **either** side is not a
    franchise -- in the pinned corpus no game mixes the two populations, but a rule that only caught
    both-sides cases would silently pass one that did."""
    exhibition_ids = exhibition_team_ids(games, min_games=min_games)
    nba: list[Game] = []
    exhibitions: list[Game] = []
    for game in games:
        target = (
            exhibitions
            if game.home_id in exhibition_ids or game.away_id in exhibition_ids
            else nba
        )
        target.append(game)
    return nba, exhibitions


def exclude_exhibitions(
    games: Sequence[Game],
    *,
    min_games: int = MIN_SEASON_GAMES_FOR_A_REAL_TEAM,
    teams_per_season: int = NBA_TEAMS_PER_SEASON,
    expected: dict[int, int] | None = EXPECTED_EXHIBITION_COUNTS,
) -> list[Game]:
    """The modeling corpus: `games` minus the exhibitions, verified.

    Two assertions, both of which must hold, and both of which exist because a silent miscount here
    would be invisible downstream -- it would show up only as a slightly disappointing headline
    number in T-009, which is indistinguishable from "the features carry no signal":

      1. every season is left with exactly `teams_per_season` team ids;
      2. every season sheds exactly the number of games pinned in `expected`, and a season absent
         from `expected` is refused outright rather than curated unverified (F-028's lesson).

    Pass `expected=None` to skip only the second check -- for a partial season, or a season being
    measured for the first time before its count is pinned.
    """
    nba, exhibitions = partition_exhibitions(games, min_games=min_games)

    teams_by_season: dict[int, set[str]] = defaultdict(set)
    for game in nba:
        teams_by_season[game.season].update((game.home_id, game.away_id))
    wrong = {s: len(t) for s, t in teams_by_season.items() if len(t) != teams_per_season}
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
