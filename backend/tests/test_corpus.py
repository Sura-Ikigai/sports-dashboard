"""Tests for corpus curation (F-042, `backend/model/corpus.py`).

Standard library only, so these run in CI — deliberately, because this module decides which rows
T-009 trains on, and `test_dataset.py` (which needs pandas) skips there.

The fixtures build a synthetic season with the same *shape* as the real corpus — 30 franchises
playing a full schedule, plus an All-Star game between ids that appear once — rather than a toy
league, because the assertions being tested are about that shape.
"""

import collections
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from model.corpus import (
    EXPECTED_EXHIBITION_COUNTS,
    MIN_SEASON_GAMES_FOR_A_REAL_TEAM,
    NBA_TEAMS_PER_SEASON,
    CorpusIntegrityError,
    assert_curated,
    exclude_exhibitions,
    exhibition_team_seasons,
    partition_exhibitions,
)
from model.features import Game

_EPOCH = datetime(2024, 1, 1, 19, 0, tzinfo=UTC)


def _game(game_id: str, day: float, home: str, away: str, season: int = 2024) -> Game:
    return Game(
        game_id=game_id,
        date=_EPOCH + timedelta(days=day),
        season=season,
        home_id=home,
        away_id=away,
        home_score=110,
        away_score=100,
    )


def _franchise_season(season: int = 2024, *, teams: int = NBA_TEAMS_PER_SEASON, rounds: int = 2):
    """A round-robin season: every team plays every other `rounds` times, so each team gets
    `(teams - 1) * rounds` games — comfortably above the threshold, as a real 82-game season is."""
    ids = [f"t{i:02d}" for i in range(teams)]
    games, n = [], 0
    for r in range(rounds):
        for i in range(teams):
            for j in range(i + 1, teams):
                home, away = (ids[i], ids[j]) if r % 2 == 0 else (ids[j], ids[i])
                games.append(_game(f"s{season}-g{n}", n * 0.01, home, away, season))
                n += 1
    return games


def _all_star(season: int = 2024, count: int = 1):
    """Exhibition games between ids that appear nowhere else — the real corpus's shape."""
    return [
        _game(f"s{season}-as{i}", 500 + i, f"phantom{season}a{i}", f"phantom{season}b{i}", season)
        for i in range(count)
    ]


def test_a_clean_season_has_no_exhibitions():
    games = _franchise_season()
    assert exhibition_team_seasons(games) == frozenset()
    nba, exhibitions = partition_exhibitions(games)
    assert exhibitions == []
    assert len(nba) == len(games)


def test_exhibition_ids_are_identified_by_games_played():
    games = [*_franchise_season(), *_all_star(count=2)]
    pairs = exhibition_team_seasons(games)
    assert pairs == {
        (2024, "phantom2024a0"), (2024, "phantom2024b0"),
        (2024, "phantom2024a1"), (2024, "phantom2024b1"),
    }
    assert all(not team.startswith("t") for _, team in pairs)


def test_partition_removes_exactly_the_exhibition_games():
    season = _franchise_season()
    games = [*season, *_all_star(count=3)]
    nba, exhibitions = partition_exhibitions(games)
    assert len(exhibitions) == 3
    assert len(nba) == len(season)
    assert {g.game_id for g in nba} == {g.game_id for g in season}


def test_a_game_mixing_a_franchise_and_an_exhibition_id_is_excluded():
    """No real game does this today. A rule that only caught both-sides cases would silently pass
    one that did, which is why the check is `or`, not `and`."""
    games = [*_franchise_season(), _game("mixed", 500, "t00", "phantom-x")]
    nba, exhibitions = partition_exhibitions(games)
    assert [g.game_id for g in exhibitions] == ["mixed"]
    assert (2024, "phantom-x") in exhibition_team_seasons(games)


def test_identification_is_per_season_not_across_the_corpus():
    """F-062 — kills counting games globally instead of per season.

    The first version of this test used a phantom appearing once in each of two seasons, whose
    *global* count (2) was also below threshold — so a global-counting implementation passed it and
    the test pinned nothing. The discriminating fixture needs a team that is REAL in one season and
    below threshold in another: global counting would rescue it in both, per-season counting flags
    it only where it belongs. Id `111353` already recurs across two real seasons, and D-017 retrains
    annually, so a reused All-Star id crosses 20 global games after ~7 seasons.
    """
    intruder = "t00"  # a genuine franchise in 2024
    games = [
        *_franchise_season(2024),
        *_franchise_season(2025),
        # ...appearing in exactly one 2026 game, alongside an id that plays only that game
        _game("cameo", 900, intruder, "phantom-2026", 2026),
    ]
    pairs = exhibition_team_seasons(games)
    assert (2026, intruder) in pairs  # under-played in 2026
    assert (2024, intruder) not in pairs  # but a franchise in 2024
    assert (2025, intruder) not in pairs


def test_a_team_under_played_in_one_season_is_not_stripped_from_the_others():
    """F-065, cause 1 — the bug this rule existed to avoid. Identification returned a UNION of ids
    and applied it to every season, so one partial season discarded the whole corpus: 2022–2025
    complete plus 150 games of 2026 kept **0 of 5,439 games** and raised nothing."""
    complete = [*_franchise_season(2024), *_franchise_season(2025)]
    partial = _franchise_season(2026, rounds=1)[:40]  # a truncated season: everyone under-played
    nba, exhibitions = partition_exhibitions([*complete, *partial])
    kept_by_season = collections.Counter(g.season for g in nba)
    assert kept_by_season[2024] == len(_franchise_season(2024))
    assert kept_by_season[2025] == len(_franchise_season(2025))
    assert kept_by_season[2026] == 0  # only the partial season is affected
    assert all(g.season == 2026 for g in exhibitions)


def test_a_season_wiped_out_entirely_is_refused_not_silently_dropped():
    """F-065, cause 2 — the 30-team assertion was built from the SURVIVING games, so a season that
    lost every game contributed no entry, `wrong` was empty, and the check was vacuous exactly when
    it mattered. Counted over the input's seasons, a wiped season reads as 0, not as absent."""
    complete = _franchise_season(2024)
    partial = _franchise_season(2025, rounds=1)[:40]
    with pytest.raises(CorpusIntegrityError, match=r"\{2025: 0\}"):
        exclude_exhibitions([*complete, *partial], expected=None)


def test_exclude_verifies_every_season_keeps_exactly_thirty_teams():
    games = [*_franchise_season(), *_all_star(count=1)]
    kept = exclude_exhibitions(games, expected={2024: 1})
    assert len({g.home_id for g in kept} | {g.away_id for g in kept}) == NBA_TEAMS_PER_SEASON


def test_a_season_left_with_the_wrong_team_count_is_refused():
    """The assertion that turns an upstream shape change into a loud failure rather than a model
    quietly trained on the wrong rows."""
    games = [*_franchise_season(teams=29), *_all_star(count=1)]
    with pytest.raises(CorpusIntegrityError, match="do not have exactly 30 team ids"):
        exclude_exhibitions(games, expected={2024: 1})


def test_an_exhibition_count_that_does_not_match_the_pin_is_refused():
    games = [*_franchise_season(), *_all_star(count=3)]
    with pytest.raises(CorpusIntegrityError, match="do not match the pinned expected"):
        exclude_exhibitions(games, expected={2024: 1})


def test_an_unpinned_season_is_refused_rather_than_curated_unverified():
    """F-028's lesson, applied here: D-017 adds a season, and nothing keeps two structures in sync
    except a check that refuses the gap."""
    games = [*_franchise_season(2024), *_all_star(2024), *_franchise_season(2027), *_all_star(2027)]
    # F-063: `expected` deliberately OVERLAPS the fixture's seasons, and the offending season number
    # is asserted. Previously `expected={2024:1}` against a 2027-only fixture meant reversing the set
    # difference still produced a non-empty result, so the test passed while an unpinned season was
    # curated unverified — F-028's lesson defeated by a test that looked like it defended it.
    with pytest.raises(CorpusIntegrityError, match="no pinned exhibition count") as exc:
        exclude_exhibitions(games, expected={2024: 1})
    assert "2027" in str(exc.value)
    assert "2024" not in str(exc.value)  # names the season that is missing, not one that is fine


def test_verification_of_counts_can_be_opted_out_but_the_team_check_still_runs():
    """`expected=None` is for measuring a new season before its count is pinned. It must not also
    disable the structural check — that one needs no pin to be meaningful."""
    games = [*_franchise_season(2027), *_all_star(2027, count=2)]
    kept = exclude_exhibitions(games, expected=None)
    assert len(kept) == len(_franchise_season(2027))

    with pytest.raises(CorpusIntegrityError, match="do not have exactly 30 team ids"):
        exclude_exhibitions([*_franchise_season(2027, teams=28), *_all_star(2027)], expected=None)


def test_the_pinned_counts_are_the_ones_measured_from_the_real_corpus():
    """Guards the constants themselves against an edit that makes a failure "go away" — the same
    role EXPECTED_COMPLETED_COUNTS plays for the loader."""
    assert EXPECTED_EXHIBITION_COUNTS == {2022: 1, 2023: 1, 2024: 1, 2025: 3, 2026: 4}
    assert sum(EXPECTED_EXHIBITION_COUNTS.values()) == 10
    assert MIN_SEASON_GAMES_FOR_A_REAL_TEAM == 20
    # The threshold must sit clear of BOTH populations observed in the corpus (<=3 and >=82).
    assert 3 < MIN_SEASON_GAMES_FOR_A_REAL_TEAM < 82


def test_min_games_is_actually_forwarded_to_identification():
    """F-066 — `exclude_exhibitions` dropping `min_games=min_games` when calling
    `partition_exhibitions` survived the whole suite, because no test ever passed a custom
    threshold. The F-056 shape: a parameter nothing exercises is a parameter that can stop working."""
    games = [*_franchise_season(rounds=1), *_all_star(count=1)]  # each team plays 29 games
    # Default threshold (20): the 29-game teams are franchises.
    assert len(exclude_exhibitions(games, expected={2024: 1})) == len(_franchise_season(rounds=1))
    # Raise the threshold above 29 and they must all be reclassified — which is only observable if
    # the parameter is forwarded.
    with pytest.raises(CorpusIntegrityError):
        exclude_exhibitions(games, min_games=40, expected={2024: 1})
    assert exhibition_team_seasons(games, min_games=40) != exhibition_team_seasons(games)


def test_the_threshold_boundary_is_exclusive():
    """F-066 — `<` vs `<=` at exactly `min_games` was unpinned. A team ON the threshold is real."""
    at_threshold = [
        _game(f"b{i}", i, "edge", f"t{i:02d}") for i in range(MIN_SEASON_GAMES_FOR_A_REAL_TEAM)
    ]
    pairs = exhibition_team_seasons([*_franchise_season(), *at_threshold])
    assert (2024, "edge") not in pairs  # exactly min_games -> real
    one_fewer = at_threshold[:-1]
    assert (2024, "edge") in exhibition_team_seasons([*_franchise_season(), *one_fewer])


def test_expected_is_resolved_at_call_time_not_bound_as_a_default(monkeypatch):
    """F-070 — `expected=EXPECTED_EXHIBITION_COUNTS` as a default bound the dict OBJECT at definition
    time, so rebinding the module attribute was silently ignored. A future test monkeypatching the
    constant would have passed vacuously — the exact class of vacuous test F-051..F-059 were about."""
    games = [*_franchise_season(), *_all_star(count=1)]
    monkeypatch.setattr("model.corpus.EXPECTED_EXHIBITION_COUNTS", {2024: 999})
    with pytest.raises(CorpusIntegrityError, match="do not match the pinned expected"):
        exclude_exhibitions(games)  # no `expected=` — must read the patched constant


def test_assert_curated_accepts_a_curated_collection_and_rejects_a_raw_one():
    """F-067 — a default on the producer is not a guarantee at the consumer. T-007/T-009 call this."""
    season = _franchise_season()
    assert_curated(season)  # does not raise
    with pytest.raises(CorpusIntegrityError, match="has not been curated"):
        assert_curated([*season, *_all_star(count=1)])


def test_assert_curated_names_the_offending_season_and_team():
    games = [*_franchise_season(), *_all_star(count=1)]
    with pytest.raises(CorpusIntegrityError) as exc:
        assert_curated(games)
    assert "2024" in str(exc.value) and "phantom" in str(exc.value)


def test_assert_curated_checks_the_team_count_too_not_only_identification():
    """F-077 — it certified two shapes `exclude_exhibitions` refuses on identical input: a season
    missing a franchise (29 ids), and a block that clears the threshold but is not NBA (32 ids).
    Only the pinned-count assertion is genuinely un-re-runnable; the team count is idempotent."""
    short = _franchise_season(teams=29)
    with pytest.raises(CorpusIntegrityError, match="do not carry exactly 30 team ids"):
        assert_curated(short)

    # 32 ids: a full league plus a 21-game impostor pair that clears min_games entirely.
    impostor = [_game(f"imp{i}", 600 + i, "impostorA", "impostorB") for i in range(21)]
    with pytest.raises(CorpusIntegrityError, match="do not carry exactly 30 team ids"):
        assert_curated([*_franchise_season(), *impostor])


def test_assert_curated_refuses_a_generator_and_an_empty_collection():
    """F-078 — F-045's hazard reintroduced: a generator passed the check *and was consumed by it*,
    leaving the caller nothing. Silent because it only bites on clean input."""
    with pytest.raises(CorpusIntegrityError, match="must be a Sequence"):
        assert_curated(g for g in _franchise_season())
    with pytest.raises(CorpusIntegrityError, match="is empty"):
        assert_curated([])


def test_assert_curated_forwards_min_games():
    """F-087 — the knob was unexercised end to end, so dropping the forward survived."""
    season = _franchise_season(rounds=1)  # 29 games each
    assert_curated(season)  # default threshold 20: fine
    with pytest.raises(CorpusIntegrityError, match="fewer than 40 games"):
        assert_curated(season, min_games=40)


def test_load_games_excludes_exhibitions_by_default_ENFORCED_IN_CI():
    """F-091 (HIGH) — the exclude-by-default guard did not run in the gate at all.

    `test_dataset.py` pins this properly, but it `importorskip`s out of CI (no pandas), and
    `test_corpus.py` never imports `model.dataset` — so flipping `include_exhibitions` to `True`
    produced **90 passed, 1 skipped** under the gate's own environment. The F-061 fix protected local
    runs and not the gate.

    This reads the default out of the source with `ast` rather than importing the module, so it runs
    everywhere `test_corpus.py` runs — which is CI included. Deliberately narrow: the alternative was
    installing training deps in the gate, which would have removed the accidental guard on
    `features.py`'s stdlib purity that D-021 partly rests on. Pinning one policy constant is a smaller
    change than restructuring what CI installs.
    """
    import ast

    source = (pathlib.Path(__file__).resolve().parents[1] / "model" / "dataset.py").read_text()
    fn = next(
        n for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == "load_games"
    )
    kwonly = {a.arg: d for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True)}
    assert "include_exhibitions" in kwonly, "load_games lost its include_exhibitions switch"
    default = kwonly["include_exhibitions"]
    assert isinstance(default, ast.Constant) and default.value is False, (
        "load_games(include_exhibitions=...) must default to False — D-025(3). Contamination has no "
        "symptom downstream, so the safe set must be the one you get without asking."
    )
