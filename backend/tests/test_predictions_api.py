"""T-032 -- the predictions API: read-only, model outputs only, and wired into the real app.

The endpoint behaviour is thin; `test_track_record.py` holds the logic tests. What this file exists
for is the three claims the router itself makes, none of which a reader could verify by looking:

  1. **It never writes.** D-047 leaves this surface unauthenticated, so the strongest statement
     available is that it does not try. Asserted by parsing the module: no SQL at all, let alone an
     INSERT -- every read goes through `model.track_record`.
  2. **It serves model outputs, never corpus rows.** The other half of T-032's security note.
  3. **It pulls no training dependency.** D-016/D-021 keep the served path standard-library-only
     (plus sqlalchemy and fastapi). This one was found the hard way: `track_record` first imported a
     constant from `model.schedule`, which imports pandas, and the whole training stack came with it.

Each of the three has a non-vacuity control, because a checker that matches nothing passes forever.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from conftest import alembic_config, executable_strings, make_scratch_database
from database import get_connection
from main import app
from model.estimator import LogisticModel, save_artifact
from model.features import FEATURE_NAMES
from routers import predictions as router_module

SCRATCH_DB = "t032_api_test"
ROUTER_PATH = Path(router_module.__file__)
BACKEND_DIR = Path(__file__).resolve().parents[1]

V1 = "aaaaaaaaaaaa"
OLD_VERSION = "cccccccccccc"
TIP = datetime(2026, 10, 20, 23, 0, tzinfo=UTC)


# ── the router is read-only ───────────────────────────────────────────────────


# Docstrings are excluded: this router's own docstring states the rules it is being checked
# against, naming both `corpus_` and the SQL verbs. Explaining a hazard has to stay possible or the
# explanation gets deleted to appease the checker.


_SQL_SHAPES = ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "FROM ", " WHERE ")


def _sql_looking(strings: list[str]) -> list[str]:
    return [s for s in strings if any(shape in s.upper() for shape in _SQL_SHAPES)]


def test_the_router_contains_no_sql_at_all():
    """Not merely no INSERT. A router that issues its own SELECT is a router whose queries no test
    covers, and the reason `model.track_record` exists is that its queries are covered."""
    offenders = _sql_looking(executable_strings(ROUTER_PATH.read_text()))
    assert offenders == [], (
        f"routers/predictions.py builds SQL. Reads belong in model.track_record: {offenders}"
    )


def test_the_router_never_names_a_corpus_table():
    """T-032's security note: model outputs out, corpus rows never. Not because a visitor could do
    harm with them -- D-047 says they could not -- but because an API that returns whatever is
    convenient stops having a shape, and the shape is what makes the next endpoint's security
    question answerable."""
    offenders = [s for s in executable_strings(ROUTER_PATH.read_text()) if "corpus_" in s]
    assert offenders == [], f"the predictions router names a corpus table: {offenders}"


@pytest.mark.parametrize(
    "offending",
    ["SELECT game_id FROM predictions", "insert into predictions values (1)",
     "DELETE FROM scheduled_games", "UPDATE predictions SET home_win_prob = 1"],
)
def test_the_sql_matcher_catches_what_it_claims_to(offending):
    assert _sql_looking([offending]), f"the matcher must catch {offending!r}"


def test_the_corpus_matcher_catches_what_it_claims_to():
    assert [s for s in ["SELECT * FROM corpus_games"] if "corpus_" in s]


def _write_verbs(prefix: str) -> list[tuple[str, str]]:
    """Every non-read method served under `prefix`, read off the OpenAPI schema.

    Not `app.routes`: FastAPI 0.139 keeps an included router as a single `_IncludedRouter` entry
    rather than flattening its children, so walking that list finds neither the paths nor the
    methods. The schema is what is actually served, which is the thing a request meets.
    """
    paths = app.openapi()["paths"]
    return [
        (path, method)
        for path, operations in paths.items()
        if path.startswith(prefix)
        for method in operations
        if method.lower() not in {"get", "head", "options"}
    ]


def test_no_write_verb_is_registered_under_the_predictions_prefix():
    """The routing-table form of the read-only claim. The AST check reads the source; this reads
    what FastAPI actually serves."""
    offenders = _write_verbs("/predictions")
    assert offenders == [], f"a write verb is mounted under /predictions: {offenders}"


def test_the_write_check_would_notice_one():
    """Non-vacuity: the same predicate applied to `/nba`, which genuinely has POST endpoints."""
    assert _write_verbs("/nba"), "the predicate must see a write endpoint when there is one"


def test_the_endpoints_are_mounted_on_the_real_application():
    """These tests drive `main.app` rather than a bare `FastAPI()` with the router attached, so a
    router that was written but never included would fail here rather than passing everywhere."""
    assert {
        "/predictions/upcoming",
        "/predictions/accuracy",
        "/predictions/model",
        "/predictions/games/{game_id}",
    } <= set(app.openapi()["paths"])


# ── the served path carries no training dependency ────────────────────────────

_TRAINING_MODULES = ("pandas", "numpy", "sklearn", "scipy")
_PROBE = (
    "import sys; import {module}; "
    "print(','.join(m for m in {names!r} if m in sys.modules))"
)


def _pulled_by(module: str) -> set[str]:
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, test-local
        [sys.executable, "-c", _PROBE.format(module=module, names=_TRAINING_MODULES)],
        cwd=BACKEND_DIR, capture_output=True, text=True, check=True,
    )
    return {m for m in result.stdout.strip().split(",") if m}


def test_importing_the_predictions_router_pulls_no_training_dependency():
    """D-016/D-021. Checked in a subprocess because the rest of this suite has already imported
    pandas, so an in-process `sys.modules` check would pass no matter what this module did.

    This is not hypothetical. `track_record` first read its status constants from `model.schedule`,
    which imports pandas -- so the API would have loaded the entire training stack to learn the
    string `"STATUS_SCHEDULED"`. The constants moved to `model.records`, which is stdlib-only.
    """
    assert _pulled_by("routers.predictions") == set()


def test_the_dependency_probe_can_see_a_training_dependency():
    """The control. `model.schedule` genuinely imports pandas; if the probe reported nothing here,
    it would report nothing above for the wrong reason."""
    assert "pandas" in _pulled_by("model.schedule")


# ── against a database ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def api_db(pg_admin_url: str) -> Iterator[str]:
    from alembic import command

    for url in make_scratch_database(pg_admin_url, SCRATCH_DB):
        command.upgrade(alembic_config(url), "head")
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO schedule_snapshots (sha256, season, row_count, scheduled_count,"
                    " completed_count, postponed_count) VALUES ('snap', 2027, 3, 1, 2, 0)"
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
                    {"game_id": "done", "game_date": TIP, "home_id": "1", "away_id": "2",
                     "status": "STATUS_FINAL", "home_score": 112, "away_score": 100},
                    {"game_id": "lost", "game_date": TIP + timedelta(days=1),
                     "home_id": "3", "away_id": "4",
                     "status": "STATUS_FINAL", "home_score": 90, "away_score": 101},
                    {"game_id": "next", "game_date": TIP + timedelta(days=3),
                     "home_id": "5", "away_id": "6",
                     "status": "STATUS_SCHEDULED", "home_score": None, "away_score": None},
                    {"game_id": "unscored", "game_date": TIP + timedelta(days=4),
                     "home_id": "7", "away_id": "8",
                     "status": "STATUS_SCHEDULED", "home_score": None, "away_score": None},
                ],
            )
            full = {name: 0.25 for name in FEATURE_NAMES}
            contributions = {"elo_diff": 1.10, "home_b2b": -0.20, "away_b2b": 0.15,
                             "avail_diff": 0.05}
            conn.execute(
                sa.text(
                    "INSERT INTO predictions (game_id, model_version, as_of, home_win_prob,"
                    " features, contributions, schedule_snapshot)"
                    " VALUES (:game_id, :version, :as_of, :p, :features, :contributions, 'snap')"
                ),
                [
                    {"game_id": "done", "version": V1, "as_of": TIP - timedelta(days=2),
                     "p": 0.62, "features": json.dumps(full),
                     "contributions": json.dumps(contributions)},
                    {"game_id": "done", "version": V1, "as_of": TIP - timedelta(hours=2),
                     "p": 0.7772998611746947, "features": json.dumps(full),
                     "contributions": json.dumps(contributions)},
                    {"game_id": "lost", "version": V1, "as_of": TIP, "p": 0.29,
                     "features": json.dumps(full), "contributions": json.dumps(contributions)},
                    {"game_id": "next", "version": V1, "as_of": TIP - timedelta(days=1),
                     "p": 0.58, "features": json.dumps(full),
                     "contributions": json.dumps(contributions)},
                    # A row written by a superseded model version, carrying a feature this code no
                    # longer computes. Its decomposition must still render whole.
                    {"game_id": "done", "version": OLD_VERSION, "as_of": TIP - timedelta(hours=1),
                     "p": 0.66, "features": json.dumps({"elo_diff": 0.4, "travel_diff": 900.0}),
                     "contributions": json.dumps({"elo_diff": 0.5, "travel_diff": -0.16})},
                ],
            )
        engine.dispose()
        yield url


@pytest.fixture
def client(api_db: str) -> Iterator[TestClient]:
    """`main.app` with only the database dependency swapped.

    `TestClient(app)` is deliberately not used as a context manager: entering it runs the lifespan,
    which starts the APScheduler that syncs from ESPN every fifteen minutes. A read-only endpoint
    test has no business starting a background job that makes network calls.
    """
    engine = sa.create_engine(api_db)

    def _connection():
        with engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_connection] = _connection
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_connection, None)
        engine.dispose()


def test_upcoming_returns_the_window_with_its_counts(client):
    response = client.get(
        "/predictions/upcoming",
        params={"as_of": (TIP + timedelta(days=1)).isoformat(), "horizon_days": 7},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["games"] == 2
    assert body["predicted"] == 1, "only `next` has a prediction"
    by_id = {item["game"]["game_id"]: item for item in body["items"]}
    assert set(by_id) == {"next", "unscored"}
    assert by_id["next"]["prediction"]["band"]["slug"] == "lean"
    assert by_id["unscored"]["prediction"] is None, (
        "a game the job has not reached is listed, not omitted -- otherwise 'the job has not run' "
        "and 'there are no games' render identically"
    )


def test_upcoming_refuses_a_horizon_outside_its_bounds(client):
    """D-047 leaves this unauthenticated, so the horizon is bounded rather than open. FastAPI's own
    validation returns 422, which is the right answer and one fewer branch to write."""
    assert client.get("/predictions/upcoming", params={"horizon_days": 400}).status_code == 422
    assert client.get("/predictions/upcoming", params={"horizon_days": 0}).status_code == 422


def test_the_accuracy_record_scores_the_last_prediction_before_tip_off(client):
    response = client.get("/predictions/accuracy", params={"model_version": V1})
    assert response.status_code == 200
    body = response.json()
    assert body["model_version"] == V1
    assert body["games"] == 2, "`done` and `lost`; `next` has not been played"
    assert body["pending"] == 1
    assert body["correct"] == 2, "home favoured and won; away favoured at .71 and won"
    assert body["accuracy"] == 1.0
    assert body["log_loss"] is not None and body["brier"] is not None


def test_an_untested_band_reports_no_hit_rate_rather_than_zero(client):
    """`0.0` and "nothing to report" render identically as `0%`, and one of them is a lie."""
    body = client.get("/predictions/accuracy", params={"model_version": V1}).json()
    by_slug = {band["band"]["slug"]: band for band in body["bands"]}
    assert by_slug["toss-up"]["games"] == 0
    assert by_slug["toss-up"]["hit_rate"] is None
    assert by_slug["strong"]["games"] == 1
    assert by_slug["strong"]["hit_rate"] == 1.0


def test_every_band_with_games_carries_an_interval_wide_enough_to_be_honest(client):
    """One-from-one is 100%. A surface shown only the point estimate has no way to say that means
    nothing yet, so the Wilson interval travels with it."""
    body = client.get("/predictions/accuracy", params={"model_version": V1}).json()
    tested = [band for band in body["bands"] if band["games"] > 0]
    assert tested
    for band in tested:
        assert band["hit_rate_low"] is not None and band["hit_rate_high"] is not None
        assert 0.0 <= band["hit_rate_low"] <= band["hit_rate"] <= band["hit_rate_high"] <= 1.0
        assert band["hit_rate_low"] < 0.6, "a one-game band must not look settled"


def test_game_detail_returns_the_standing_prediction_decomposed(client):
    response = client.get("/predictions/games/done", params={"model_version": V1})
    assert response.status_code == 200
    body = response.json()
    assert body["game"]["status"] == "STATUS_FINAL"
    assert body["game"]["home_score"] == 112
    assert body["latest"]["prediction"]["home_win_probability"] == pytest.approx(0.7773, abs=1e-4)
    assert [p["home_win_probability"] for p in body["history"]] == pytest.approx([0.62, 0.7773], abs=1e-4)
    assert body["band_record"]["band"]["slug"] == "strong"


def test_the_decomposition_reconstructs_the_probability_exactly(client):
    """D-040's waterfall is a *decomposition*, not an attribution: the contributions and the
    intercept sum to the logit by construction (D-023 kept the model linear for this).

    This is the serialization's half of that guarantee -- it fails if `_factors` drops a
    contribution, reorders one away, or if the recovered baseline is wrong.
    """
    body = client.get("/predictions/games/done", params={"model_version": V1}).json()
    latest = body["latest"]
    total = latest["baseline_logit"] + sum(f["contribution"] for f in latest["factors"])
    assert total == pytest.approx(latest["logit"])
    assert 1 / (1 + math.exp(-total)) == pytest.approx(
        latest["prediction"]["home_win_probability"], abs=1e-9
    )


def test_every_factor_carries_the_value_that_produced_it(client):
    """User story 23: the point is seeing *why* a factor contributed what it did, not only that it
    did. A contribution with no value beside it is a number nobody can check."""
    body = client.get("/predictions/games/done", params={"model_version": V1}).json()
    factors = body["latest"]["factors"]
    assert [f["name"] for f in factors] == list(FEATURE_NAMES), "the model's own order, stably"
    assert all("value" in f for f in factors)
    assert factors[0]["value"] == pytest.approx(0.25)


def test_a_superseded_versions_feature_still_renders(client):
    """A prediction row carries the feature set of the version that wrote it. Dropping the half this
    code no longer computes would render an incomplete decomposition that still looked complete."""
    body = client.get("/predictions/games/done", params={"model_version": OLD_VERSION}).json()
    names = [f["name"] for f in body["latest"]["factors"]]
    assert names == ["elo_diff", "travel_diff"], "unknown names sort after the known ones, not away"


def test_a_game_nobody_has_predicted_is_a_404(client):
    assert client.get("/predictions/games/unscored").status_code == 404
    assert client.get("/predictions/games/no-such-game").status_code == 404


# ── the frozen model ──────────────────────────────────────────────────────────


@pytest.fixture
def artifact(tmp_path: Path, monkeypatch) -> Path:
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
    monkeypatch.setattr(router_module, "ARTIFACT_PATH", path)
    router_module._load_scorer.cache_clear()
    yield path
    router_module._load_scorer.cache_clear()


def test_the_model_endpoint_serves_the_parameters_a_second_scorer_needs(client, artifact):
    """T-033's TypeScript scorer and T-034's what-if panel run in the browser, and `/models/` is
    gitignored -- so the artifact cannot be imported at build time. Public by D-047."""
    body = client.get("/predictions/model").json()
    assert body["feature_names"] == list(FEATURE_NAMES)
    assert body["intercept"] == 0.2
    assert body["coefficients"]["elo_diff"] == pytest.approx(0.4)
    assert set(body["means"]) == set(FEATURE_NAMES)
    assert set(body["stds"]) == set(FEATURE_NAMES)
    assert len(body["model_version"]) == 12


def test_a_missing_artifact_is_a_503_rather_than_a_crash(client, monkeypatch, tmp_path):
    """The artifact is a deployment input, not code. A service that 500s on a missing one says
    "internal error"; 503 with the path in it says what to fix."""
    monkeypatch.setattr(router_module, "ARTIFACT_PATH", tmp_path / "absent.json")
    router_module._load_scorer.cache_clear()
    response = client.get("/predictions/model")
    assert response.status_code == 503
    assert "absent.json" in response.json()["detail"]
    router_module._load_scorer.cache_clear()
