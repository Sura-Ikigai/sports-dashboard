"""T-031 -- the daily prediction job: append-only, idempotent, and the only writer.

Three claims are worth pinning here and the rest is plumbing:

  1. **A double-fire writes nothing twice.** The plan's *Future hardening* section already names the
     hazard -- an in-process scheduler double-syncing under a second replica -- so the job has to be
     safe against it rather than merely unlikely to meet it.
  2. **A game that has tipped off is never predicted.** D-010 is the decision that a model
     re-predicting a finished game is worthless; this is where that becomes unreachable.
  3. **Nothing else writes predictions.** Asserted by parsing the package, in the spirit of T-024's
     D-039 check: the security note says the job writes and the API reads, and a note is not a
     control.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas", reason="training-only dependency (D-016)")

import sqlalchemy as sa  # noqa: E402

from conftest import alembic_config, make_scratch_database  # noqa: E402
from model import job  # noqa: E402
from model.estimator import LogisticModel, save_artifact  # noqa: E402
from model.features import FEATURE_NAMES  # noqa: E402
from model.job import JobError, truncate_to_hour  # noqa: E402
from model.prediction import Scorer  # noqa: E402

SCRATCH_DB = "t031_job_test"
TEAMS = [str(400 + i) for i in range(30)]
LIVE_SEASON = 2027
OPENER = datetime(2026, 10, 20, 23, 0, tzinfo=UTC)


# ── the as-of moment ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 20, 18, 0, tzinfo=UTC), datetime(2026, 10, 20, 18, 0, tzinfo=UTC)),
        (datetime(2026, 10, 20, 18, 59, 59, 999999, tzinfo=UTC),
         datetime(2026, 10, 20, 18, 0, tzinfo=UTC)),
        (datetime(2026, 10, 20, 19, 0, 1, tzinfo=UTC), datetime(2026, 10, 20, 19, 0, tzinfo=UTC)),
    ],
)
def test_the_as_of_moment_is_truncated_to_the_hour(moment, expected):
    """Two runs in the same hour collide on the unique constraint, which is what makes a double-fire
    a no-op. Truncating to the *day* was the obvious alternative and is wrong: features would be
    computed at midnight UTC, before the previous evening's US games have finished."""
    assert truncate_to_hour(moment) == expected


def test_a_naive_as_of_is_refused():
    with pytest.raises(JobError, match="timezone-aware"):
        truncate_to_hour(datetime(2026, 10, 20, 18, 0))  # noqa: DTZ001 -- the point of the test


# ── the job is the only writer ────────────────────────────────────────────────


def test_no_module_but_the_job_writes_predictions():
    """The security note says the job writes and the API reads. A note is not a control, so this
    parses every module in the package and fails the build on a second writer.

    Matches on the statement rather than the table name so that prose may keep discussing it --
    `model/schedule.py` reads `predictions` to find games it must not drop, and that must stay
    possible.
    """
    package = Path(job.__file__).parent
    offenders = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            text = " ".join(node.value.split()).upper()
            if "INSERT INTO PREDICTIONS" in text and path.name != "job.py":
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"only model/job.py may write predictions; found {offenders}"


def test_the_check_above_would_notice_a_second_writer(tmp_path):
    """The non-vacuity control. A check that matched nothing would pass forever."""
    module = tmp_path / "rogue.py"
    module.write_text('SQL = "INSERT INTO predictions (game_id) VALUES (:g)"\n')
    tree = ast.parse(module.read_text())
    found = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and "INSERT INTO PREDICTIONS" in " ".join(n.value.split()).upper()
    ]
    assert found, "the matcher must actually match an INSERT it is shown"


# ── against a database ────────────────────────────────────────────────────────


#: 2024 is pinned at exactly one exhibition in `corpus.EXPECTED_EXHIBITION_COUNTS`, and
#: `assert_curated` refuses a season that sheds a different number -- so the fixture has to contain
#: one, played between two phantom ids, exactly as the real corpus does (F-042).
CORPUS_SEASON = 2024
PHANTOMS = ("990", "991")


def _corpus_rows() -> list[dict]:
    rows, n = [], 0
    for i, home in enumerate(TEAMS):
        for away in TEAMS[i + 1:]:
            rows.append({
                "game_id": f"c{n}", "season": CORPUS_SEASON, "season_type": 2,
                "game_date": datetime(2023, 10, 21, tzinfo=UTC) + timedelta(hours=n),
                "home_id": home, "away_id": away,
                "home_score": 110 + (n % 7), "away_score": 104, "neutral_site": False,
            })
            n += 1
    rows.append({
        "game_id": "c-allstar", "season": CORPUS_SEASON, "season_type": 2,
        "game_date": datetime(2023, 10, 21, tzinfo=UTC) + timedelta(hours=n),
        "home_id": PHANTOMS[0], "away_id": PHANTOMS[1],
        "home_score": 200, "away_score": 190, "neutral_site": True,
    })
    return rows


@pytest.fixture(scope="module")
def job_db(pg_admin_url: str) -> Iterator[str]:
    from alembic import command

    for url in make_scratch_database(pg_admin_url, SCRATCH_DB):
        command.upgrade(alembic_config(url), "head")
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO corpus_games (game_id, season, season_type, game_date, home_id,"
                    " away_id, home_score, away_score, neutral_site)"
                    " VALUES (:game_id, :season, :season_type, :game_date, :home_id, :away_id,"
                    " :home_score, :away_score, :neutral_site)"
                ),
                _corpus_rows(),
            )
            # Every team needs a participation row or `Context` refuses the history.
            conn.execute(
                sa.text(
                    "INSERT INTO corpus_player_box (game_id, team_id, player_id, minutes, started,"
                    " did_not_play, points) VALUES (:g, :t, :p, 30.0, true, false, 12)"
                ),
                [
                    {"g": row["game_id"], "t": team, "p": f"{team}-p{i}"}
                    for row in _corpus_rows()[:60]
                    for team in (row["home_id"], row["away_id"])
                    for i in range(9)
                ],
            )
            conn.execute(
                sa.text(
                    "INSERT INTO schedule_snapshots (sha256, season, row_count, scheduled_count,"
                    " completed_count, postponed_count)"
                    " VALUES ('snap', :s, 3, 3, 0, 0)"
                ),
                {"s": LIVE_SEASON},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO scheduled_games (game_id, season, season_type, game_date, home_id,"
                    " away_id, neutral_site, status, snapshot_sha256)"
                    " VALUES (:game_id, :season, 2, :game_date, :home_id, :away_id, false,"
                    " :status, 'snap')"
                ),
                [
                    {"game_id": "up-1", "season": LIVE_SEASON, "game_date": OPENER,
                     "home_id": TEAMS[0], "away_id": TEAMS[1], "status": "STATUS_SCHEDULED"},
                    {"game_id": "up-2", "season": LIVE_SEASON,
                     "game_date": OPENER + timedelta(days=3),
                     "home_id": TEAMS[2], "away_id": TEAMS[3], "status": "STATUS_SCHEDULED"},
                    {"game_id": "far", "season": LIVE_SEASON,
                     "game_date": OPENER + timedelta(days=30),
                     "home_id": TEAMS[4], "away_id": TEAMS[5], "status": "STATUS_SCHEDULED"},
                    {"game_id": "postponed", "season": LIVE_SEASON,
                     "game_date": OPENER + timedelta(days=1),
                     "home_id": TEAMS[6], "away_id": TEAMS[7], "status": "STATUS_POSTPONED"},
                    {"game_id": "gone", "season": LIVE_SEASON,
                     "game_date": OPENER - timedelta(days=2),
                     "home_id": TEAMS[8], "away_id": TEAMS[9], "status": "STATUS_SCHEDULED"},
                ],
            )
        engine.dispose()
        yield url


@pytest.fixture
def artifact(tmp_path: Path) -> Path:
    n = len(FEATURE_NAMES)
    model = LogisticModel(
        feature_names=FEATURE_NAMES,
        coefficients=tuple(0.4 - 0.05 * i for i in range(n)),
        intercept=0.2,
        means=(0.0,) * n,
        stds=(1.0,) * n,
    )
    path = tmp_path / "model.json"
    save_artifact(model, path, training={"note": "test"})
    return path


@pytest.fixture(autouse=True)
def _clean_predictions(request):
    """Empty `predictions` before each database test.

    The corpus fixture is module-scoped because building it is slow, which makes the *table* shared
    unless something clears it -- and a test that asserts "the table contains exactly these rows"
    would then pass or fail on the order pytest happened to choose. Ordering dependence in a suite
    is a bug that reports itself as a different bug.
    """
    if "job_db" not in request.fixturenames:
        return
    engine = sa.create_engine(request.getfixturevalue("job_db"))
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM predictions"))
    engine.dispose()


@pytest.fixture
def run_job(job_db: str, artifact: Path, monkeypatch):
    """`job.run` with the network refresh stubbed. What is under test is the scoring and the append,
    not the fetch -- `test_schedule.py` covers that."""
    from model.schedule import SnapshotReport

    monkeypatch.setattr(
        job, "refresh",
        lambda url, season: SnapshotReport(
            sha256="snap", season=season, row_count=3, scheduled_count=3, completed_count=0,
            postponed_count=0, added_game_ids=0, changed_game_ids=0, excluded_placeholders=0,
        ),
    )

    def _run(as_of: datetime, **kwargs):
        return job.run(job_db, artifact, LIVE_SEASON, as_of=as_of, **kwargs)

    return _run


def _rows(url: str) -> list:
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        out = conn.execute(
            sa.text("SELECT game_id, as_of, home_win_prob, schedule_snapshot FROM predictions"
                    " ORDER BY game_id")
        ).all()
    engine.dispose()
    return out


def test_the_job_predicts_the_horizon_and_nothing_outside_it(run_job, job_db):
    report = run_job(OPENER - timedelta(days=1))
    assert report.upcoming == 2, "up-1 and up-2 only"
    assert report.written == 2
    assert [r.game_id for r in _rows(job_db)] == ["up-1", "up-2"]


def test_a_second_run_in_the_same_hour_writes_nothing(run_job, job_db):
    """The double-fire the plan's Future hardening section names. `ON CONFLICT DO NOTHING` is also
    the strongest conflict clause `sports_job`'s grant permits -- `DO UPDATE` needs UPDATE, which it
    deliberately does not hold."""
    run_job(OPENER - timedelta(days=1))
    before = len(_rows(job_db))
    second = run_job(OPENER - timedelta(days=1, minutes=-45))  # same hour, later minute
    assert second.written == 0
    assert len(_rows(job_db)) == before


def test_a_later_hour_appends_rather_than_overwrites(run_job, job_db):
    """Seven daily predictions for one game are seven different statements, and D-012 keeps them
    all: a rest value computed six days out is *wrong* rather than merely stale."""
    run_job(OPENER - timedelta(days=2))
    run_job(OPENER - timedelta(days=1))
    rows = [r for r in _rows(job_db) if r.game_id == "up-1"]
    assert len(rows) == 2
    assert len({r.as_of for r in rows}) == 2


def test_a_game_that_has_tipped_off_is_never_predicted(run_job, job_db):
    """D-010, made unreachable rather than merely discouraged. `load_upcoming` filters strictly
    after `as_of`, and `compute_features` would refuse it anyway.

    `up-1` tips off at the opener and `gone` two days before it, so a run an hour after the opener
    must reach neither -- while `up-2`, three days out, must still be predicted. Without that last
    assertion a filter that returned nothing at all would pass.
    """
    report = run_job(OPENER + timedelta(hours=1))
    predicted = {r.game_id for r in _rows(job_db)}
    assert "gone" not in predicted
    assert "up-1" not in predicted
    assert predicted == {"up-2"}
    assert report.upcoming == 1


def test_a_postponed_game_is_not_predicted(run_job, job_db):
    run_job(OPENER - timedelta(days=1))
    assert all(r.game_id != "postponed" for r in _rows(job_db))


def test_every_prediction_records_the_snapshot_it_was_made_against(run_job, job_db):
    """D-048's provenance half. The schedule bytes were never pinned in advance, so this is what
    makes "what did we know when we predicted this?" answerable."""
    run_job(OPENER - timedelta(days=1))
    assert {r.schedule_snapshot for r in _rows(job_db)} == {"snap"}


def test_the_horizon_is_configurable_and_widening_it_finds_more(run_job, job_db):
    """The control for the horizon test: a filter that returned nothing would satisfy it."""
    report = run_job(OPENER - timedelta(days=1), horizon_days=40)
    assert report.upcoming == 3, "the far game comes into range"


def test_the_job_reports_the_model_version_it_scored_with(run_job, artifact):
    report = run_job(OPENER - timedelta(days=1))
    assert report.model_version == Scorer.load(artifact).model_version
    assert len(report.model_version) == 12
