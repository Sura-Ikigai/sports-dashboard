"""T-022 / F-111 -- tests for `backend/model/loader.py`'s verification machinery.

Until now this module had **no test file of any kind**. Every branch T-005 added -- content-hash
mismatch, header/size/UTF-8 refusals, the redirect allowlist, path-traversal containment,
unpinned-season refusal, per-season `game_id` uniqueness -- had zero coverage, and every one of them
is a pure function needing no network.

That gap got worse rather than better with age. **D-046 made this machinery the integrity guarantee
for the entire Postgres corpus**: content hashes over source bytes before parsing, pinned counts, and
the curation invariant re-asserted on read. Everything downstream of `ingest` trusts these functions
to refuse rather than repair. So they are tested here **before** the parquet path is written, which
is the order F-111 asks for and the opposite of how this module has been treated so far.

## What these tests do NOT claim to catch

The module's own docstring made an argument for leaving it untested: upstream format drift is the
real risk, and no committed fixture can detect that by construction. That argument is still correct
and nothing below refutes it. What it does not cover is the *other* half -- that when a tripwire
should fire, it fires, and with the right error type. A fixture proves that perfectly well, and it
is the half that D-046 now leans on.

## The F-114 trap, closed rather than worked around

`_verify_season` and `verify_completed_counts` used to bind `EXPECTED_COMPLETED_COUNTS` as a
**signature default**, which is evaluated once at def time and captures that dict *object*. A test
doing `monkeypatch.setattr(loader, "EXPECTED_COMPLETED_COUNTS", ...)` would rebind the module
attribute while the functions went on consulting the original -- patch in a deliberately wrong count,
watch the check "pass", conclude the tripwire works. The trap lay directly across these tests, so
T-022 closed it in the module (defaults resolve inside the body now) and
`test_patching_the_pinned_counts_actually_reaches_the_check` is the regression that keeps it closed.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

pd = pytest.importorskip("pandas", reason="training-only dependency (D-016)")

from model import loader  # noqa: E402 -- must follow the pandas guard above
from model.loader import (  # noqa: E402
    REQUIRED_COLUMNS,
    LoaderIntegrityError,
    LoaderVerificationError,
)

SEASON = 2024


# ── fixtures: a minimally valid season CSV ────────────────────────────────────


def _row(
    game_id: str,
    *,
    home: str = "1",
    away: str = "2",
    completed: str = "true",
    status: str | None = None,
) -> dict:
    # T-031 added `status_type_name` to REQUIRED_COLUMNS: the live path needs to tell scheduled from
    # postponed, and this source states it explicitly rather than leaving it to be inferred. It
    # defaults to agreeing with `completed` so no existing fixture has to say the same thing twice.
    return {
        "id": game_id,
        "game_id": game_id,
        "date": "2024-01-15T00:00Z",
        "season": str(SEASON),
        "season_type": "2",
        "home_id": home,
        "away_id": away,
        "home_score": "110",
        "away_score": "104",
        "status_type_completed": completed,
        "status_type_name": status
        or ("STATUS_FINAL" if completed == "true" else "STATUS_SCHEDULED"),
        "neutral_site": "false",
        "venue_id": "3421",
        "venue_full_name": "Test Arena",
        "venue_address_city": "Denver",
        "venue_address_state": "CO",
        "venue_indoor": "true",
    }


def _csv_bytes(rows: list[dict]) -> bytes:
    """A CSV carrying exactly `REQUIRED_COLUMNS`, which is what `_read_completed_games` asks for."""
    lines = [",".join(REQUIRED_COLUMNS)]
    lines += [",".join(row[column] for column in REQUIRED_COLUMNS) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.fixture
def valid_csv() -> bytes:
    return _csv_bytes([_row("401-a"), _row("401-b"), _row("401-c")])


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch, valid_csv: bytes):
    """Pin the fixture's own bytes and count, so the happy path is genuinely reachable.

    Without this every download test would fail on the hash check and every assertion below would be
    passing for the wrong reason -- the non-vacuity discipline T-006 established.
    """
    monkeypatch.setattr(loader, "EXPECTED_SHA256", {SEASON: hashlib.sha256(valid_csv).hexdigest()})
    monkeypatch.setattr(loader, "EXPECTED_COMPLETED_COUNTS", {SEASON: 3})
    return valid_csv


@contextmanager
def _fake_response(body: bytes, *, url: str, status: int = 200):
    """Stand in for `urllib.request.urlopen`'s context manager.

    The loader reads `geturl()`, `status` and `read(n)` and nothing else, so this covers the surface
    it actually depends on.
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return url

        @property
        def status(self):
            return status

        def read(self, _n=None):
            return body

    yield _Response()


def _urlopen_returning(body: bytes, *, url: str, status: int = 200):
    def _open(_request, **_kwargs):
        return _fake_response(body, url=url, status=status).__enter__()

    return _open


# ── season validation and path containment (F-033) ────────────────────────────


@pytest.mark.parametrize("season", [2015, 2027, 0, -2024])
def test_an_unpinned_season_cannot_shape_a_url_or_a_path(season: int) -> None:
    with pytest.raises(LoaderVerificationError, match="not one of the pinned seasons"):
        loader._validate_season(season)


def test_every_pinned_season_is_accepted() -> None:
    """Non-vacuity control: a guard that rejected everything would satisfy the test above.

    Covers `SCHEDULE_SEASONS`, not just `SEASONS` — T-022 widened what a schedule download may ask
    for, and a check still gated on the narrower tuple would refuse every warm-up season.
    """
    for season in loader.SCHEDULE_SEASONS:
        loader._validate_season(season)


def test_a_destination_escaping_the_data_dir_is_refused(tmp_path: Path) -> None:
    """F-033's containment check, exercised rather than assumed.

    Not reachable through today's callers -- `_validate_season` runs first and only ever admits
    plain ints -- but this is the assertion that actually proves the traversal is closed, and the
    guard it replaced (`url.startswith(...)`) looked like it did the same thing and did not.

    Note how many `..` segments this takes, because it is not obvious. The season is interpolated
    into the *filename* (`nba_schedule_{season}.csv`), so a payload starting with `../` yields the
    component `nba_schedule_..` -- a literal directory name, which the following `..` merely pops.
    Two segments therefore land back inside the data dir and three are needed to leave it. A test
    written with the obvious `../../` would have passed containment honestly and proved nothing.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with pytest.raises(LoaderVerificationError, match="escapes the data dir"):
        loader._dest_path("../../../etc/passwd", data_dir)  # type: ignore[arg-type]


def test_a_traversal_that_lands_back_inside_the_data_dir_is_allowed(tmp_path: Path) -> None:
    """The companion to the count above: containment is about where the path *resolves*, not about
    whether it contained a `..`. This one is contained and passes, which is what makes the test
    above a test of containment rather than of punctuation."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dest = loader._dest_path("../../etc/passwd", data_dir)  # type: ignore[arg-type]
    assert dest.resolve().is_relative_to(data_dir.resolve())


def test_a_pinned_season_resolves_inside_the_data_dir(tmp_path: Path) -> None:
    dest = loader._dest_path(SEASON, tmp_path)
    assert dest.resolve().is_relative_to(tmp_path.resolve())
    assert dest.name == f"nba_schedule_{SEASON}.csv"


# ── header, size and encoding refusals ────────────────────────────────────────


def test_an_empty_body_is_refused() -> None:
    with pytest.raises(LoaderVerificationError, match="is empty"):
        loader._validate_header(b"", SEASON, source="test")


def test_a_body_over_the_size_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_MAX_DOWNLOAD_BYTES", 32)
    with pytest.raises(LoaderVerificationError, match="over the 32 byte|over the 32"):
        loader._validate_header(b"x" * 64, SEASON, source="test")


def test_a_non_utf8_header_raises_this_modules_error_not_a_stdlib_one() -> None:
    """F-029: a binary blob or an HTML error page in an unexpected encoding must fail through
    `LoaderVerificationError`, not leak a `UnicodeDecodeError` callers are not catching."""
    with pytest.raises(LoaderVerificationError, match="not valid UTF-8"):
        loader._validate_header(b"\xff\xfe\x00garbage\n", SEASON, source="test")


def test_an_html_error_page_is_refused_by_the_header_check() -> None:
    body = b"<!DOCTYPE html>\n<html><body>404 Not Found</body></html>\n"
    with pytest.raises(LoaderVerificationError, match="missing expected column"):
        loader._validate_header(body, SEASON, source="test")


@pytest.mark.parametrize("dropped", REQUIRED_COLUMNS)
def test_a_file_missing_any_required_column_is_refused(dropped: str) -> None:
    """Each column separately: a header check that only noticed a *fully* wrong file would pass a
    single-column rename, which is exactly what upstream drift looks like."""
    columns = [c for c in REQUIRED_COLUMNS if c != dropped]
    body = (",".join(columns) + "\n").encode("utf-8")
    with pytest.raises(LoaderVerificationError, match="missing expected column"):
        loader._validate_header(body, SEASON, source="test")


def test_a_well_formed_header_passes(valid_csv: bytes) -> None:
    loader._validate_header(valid_csv, SEASON, source="test")


# ── the content pin (F-030, F-038) ────────────────────────────────────────────


def test_a_content_hash_mismatch_is_a_hard_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-030: the header check catches a wrong-*shaped* file and nothing else. A silent upstream
    re-upload -- a corrected score, a swapped team id -- keeps the shape and the row count identical.
    This is the only check that sees it."""
    monkeypatch.setattr(loader, "EXPECTED_SHA256", {SEASON: "0" * 64})
    with pytest.raises(LoaderVerificationError, match="upstream data has changed"):
        loader._validate_content_hash(b"anything", SEASON, source="test")


def test_the_mismatch_message_refuses_to_offer_a_retry_or_a_rebaseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wording is load-bearing, not decoration. This failure is not transient, and the one
    tempting "fix" -- editing EXPECTED_SHA256 until it passes -- destroys the guarantee entirely.
    Whoever hits it at 2am reads this message and nothing else."""
    monkeypatch.setattr(loader, "EXPECTED_SHA256", {SEASON: "0" * 64})
    with pytest.raises(LoaderVerificationError) as excinfo:
        loader._validate_content_hash(b"anything", SEASON, source="test")
    message = str(excinfo.value)
    assert "do not retry it" in message
    assert "do not edit EXPECTED_SHA256" in message


def test_a_season_with_no_pinned_hash_is_refused_rather_than_keyerroring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-038: a bare KeyError fails closed but reads as a bug rather than as "pin a hash first"."""
    monkeypatch.setattr(loader, "EXPECTED_SHA256", {})
    with pytest.raises(LoaderVerificationError, match="no pinned SHA-256"):
        loader._validate_content_hash(b"anything", SEASON, source="test")


def test_matching_bytes_pass_the_content_pin(pinned: bytes) -> None:
    loader._validate_content_hash(pinned, SEASON, source="test")


# ── the redirect allowlist (F-032) ────────────────────────────────────────────


def test_a_redirect_that_drops_tls_is_refused(tmp_path: Path, pinned: bytes) -> None:
    fake = _urlopen_returning(pinned, url="http://release-assets.githubusercontent.com/x.csv")
    with patch.object(loader.urllib.request, "urlopen", fake):
        with pytest.raises(LoaderVerificationError, match="refusing a redirect that dropped TLS"):
            loader.download_season_csv(SEASON, tmp_path)


def test_a_redirect_to_an_unlisted_host_is_refused(tmp_path: Path, pinned: bytes) -> None:
    fake = _urlopen_returning(pinned, url="https://evil.example.com/nba_schedule_2024.csv")
    with patch.object(loader.urllib.request, "urlopen", fake):
        with pytest.raises(LoaderVerificationError, match="not in the allowlist"):
            loader.download_season_csv(SEASON, tmp_path)


def test_a_non_200_response_is_refused(tmp_path: Path, pinned: bytes) -> None:
    fake = _urlopen_returning(
        pinned, url="https://release-assets.githubusercontent.com/x.csv", status=503
    )
    with patch.object(loader.urllib.request, "urlopen", fake):
        with pytest.raises(LoaderVerificationError, match="returned HTTP 503"):
            loader.download_season_csv(SEASON, tmp_path)


@pytest.mark.parametrize("host", sorted(loader._ALLOWED_DOWNLOAD_HOSTS))
def test_each_allowlisted_host_is_actually_accepted(
    tmp_path: Path, pinned: bytes, host: str
) -> None:
    """Non-vacuity for the two tests above: an allowlist check that refused *everything* would
    satisfy them both and break every real download -- which is the shape of F-037's original bug,
    where a guessed host shipped broken and the populated cache hid it."""
    fake = _urlopen_returning(pinned, url=f"https://{host}/nba_schedule_2024.csv")
    with patch.object(loader.urllib.request, "urlopen", fake):
        dest = loader.download_season_csv(SEASON, tmp_path)
    assert dest.read_bytes() == pinned


# ── download and cache behaviour ──────────────────────────────────────────────


def test_a_download_writes_atomically_and_leaves_no_part_file(
    tmp_path: Path, pinned: bytes
) -> None:
    fake = _urlopen_returning(pinned, url="https://release-assets.githubusercontent.com/x.csv")
    with patch.object(loader.urllib.request, "urlopen", fake):
        dest = loader.download_season_csv(SEASON, tmp_path)
    assert dest.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_a_download_into_an_empty_data_directory_works(tmp_path: Path, pinned: bytes) -> None:
    """F-037, stated as a test rather than as a habit. The original allowlist bug shipped broken
    precisely because every local run hit a populated `data/` cache and the download path never
    executed. `tmp_path` is empty by construction, so this exercises the branch that hid it."""
    empty = tmp_path / "does-not-exist-yet"
    assert not empty.exists()
    fake = _urlopen_returning(pinned, url="https://release-assets.githubusercontent.com/x.csv")
    with patch.object(loader.urllib.request, "urlopen", fake):
        dest = loader.download_season_csv(SEASON, empty)
    assert dest.exists()


def test_a_valid_cached_file_is_not_re_downloaded(tmp_path: Path, pinned: bytes) -> None:
    dest = tmp_path / f"nba_schedule_{SEASON}.csv"
    dest.write_bytes(pinned)

    def _explode(*_args, **_kwargs):
        raise AssertionError("a valid cached file must not trigger a download")

    with patch.object(loader.urllib.request, "urlopen", _explode):
        assert loader.download_season_csv(SEASON, tmp_path) == dest


def test_force_re_downloads_even_over_a_valid_cache(tmp_path: Path, pinned: bytes) -> None:
    dest = tmp_path / f"nba_schedule_{SEASON}.csv"
    dest.write_bytes(pinned)
    calls: list[str] = []

    def _counting(request, **kwargs):
        calls.append("hit")
        return _urlopen_returning(
            pinned, url="https://release-assets.githubusercontent.com/x.csv"
        )(request, **kwargs)

    with patch.object(loader.urllib.request, "urlopen", _counting):
        loader.download_season_csv(SEASON, tmp_path, force=True)
    assert calls == ["hit"]


def test_a_cached_file_that_fails_the_content_pin_is_refused(tmp_path: Path, pinned: bytes) -> None:
    """The cache is verified on every read, not trusted because it is local -- a tampered or
    half-corrected file on disk must not reach the parser."""
    dest = tmp_path / f"nba_schedule_{SEASON}.csv"
    dest.write_bytes(pinned.replace(b"110", b"111"))
    with pytest.raises(LoaderVerificationError, match="upstream data has changed"):
        loader.download_season_csv(SEASON, tmp_path)


def test_an_oversized_cached_file_is_refused_before_it_is_read_into_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned: bytes
) -> None:
    """F-031: the size cap is checked with `stat()` on the cached path. The version this replaced
    only ever peeked at a fixed 8KB header, so the cap was applied to that slice and an oversized
    cached file satisfied it trivially."""
    dest = tmp_path / f"nba_schedule_{SEASON}.csv"
    dest.write_bytes(pinned)
    monkeypatch.setattr(loader, "_MAX_DOWNLOAD_BYTES", 4)
    with pytest.raises(LoaderVerificationError, match="refusing to read it into memory"):
        loader.download_season_csv(SEASON, tmp_path)


# ── per-season integrity: count and game_id uniqueness (F-026, F-027, F-028) ──


def _frame(game_ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"game_id": game_ids})


def test_an_unpinned_season_is_refused_rather_than_loaded_unverified() -> None:
    with pytest.raises(LoaderIntegrityError, match="no pinned expected count"):
        loader._verify_season(_frame(["a"]), 1999, expected={SEASON: 1})


def test_a_duplicate_game_id_is_caught_even_when_the_count_matches() -> None:
    """F-027, and the reason a count alone was never sufficient: a file that drops one real game and
    duplicates another in its place keeps the row count identical. One real game silently goes
    missing, and every number downstream is computed over the wrong set."""
    with pytest.raises(LoaderIntegrityError, match="duplicate game_id"):
        loader._verify_season(_frame(["a", "b", "b"]), SEASON, expected={SEASON: 3})


def test_uniqueness_is_checked_before_the_count_so_the_message_names_the_real_fault() -> None:
    """Both checks fail here. The duplicate is the diagnosis; the count is the symptom."""
    with pytest.raises(LoaderIntegrityError, match="duplicate game_id"):
        loader._verify_season(_frame(["a", "a"]), SEASON, expected={SEASON: 99})


def test_a_count_mismatch_is_caught() -> None:
    with pytest.raises(LoaderIntegrityError, match="got 2 completed games, expected 3"):
        loader._verify_season(_frame(["a", "b"]), SEASON, expected={SEASON: 3})


def test_a_correct_frame_passes() -> None:
    loader._verify_season(_frame(["a", "b", "c"]), SEASON, expected={SEASON: 3})


def test_patching_the_pinned_counts_actually_reaches_the_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-114 regression -- the one test here that exists to protect the other tests.

    `EXPECTED_COMPLETED_COUNTS` used to be a signature default, bound to the dict object that existed
    at def time. `setattr` rebound the module attribute; the function kept reading the original. A
    test written against that would patch in a wrong count, see no failure, and record the tripwire
    as working. If this test ever passes vacuously again, so does half this file.
    """
    monkeypatch.setattr(loader, "EXPECTED_COMPLETED_COUNTS", {SEASON: 999})
    with pytest.raises(LoaderIntegrityError, match="expected 999"):
        loader._verify_season(_frame(["a", "b", "c"]), SEASON)

    monkeypatch.setattr(loader, "EXPECTED_COMPLETED_COUNTS", {SEASON: 3})
    loader._verify_season(_frame(["a", "b", "c"]), SEASON)


# ── the aggregate second pass ─────────────────────────────────────────────────


def test_a_season_present_in_actual_but_unpinned_is_refused() -> None:
    with pytest.raises(LoaderIntegrityError, match=r"season\(s\) \[1999\]"):
        loader.verify_completed_counts({1999: 10}, expected={SEASON: 3})


def test_aggregate_count_mismatches_are_reported_together() -> None:
    with pytest.raises(LoaderIntegrityError) as excinfo:
        loader.verify_completed_counts({2024: 1, 2025: 2}, expected={2024: 10, 2025: 20})
    message = str(excinfo.value)
    assert "season 2024: got 1, expected 10" in message
    assert "season 2025: got 2, expected 20" in message


def test_a_season_missing_from_actual_entirely_is_a_mismatch() -> None:
    """`actual.get(season)` returns None, which is not the expected count -- a season that failed to
    load at all must not read as "nothing to compare"."""
    with pytest.raises(LoaderIntegrityError, match="got None, expected 10"):
        loader.verify_completed_counts({}, expected={2024: 10})


def test_matching_aggregate_counts_pass() -> None:
    loader.verify_completed_counts({2024: 10, 2025: 20}, expected={2024: 10, 2025: 20})


def test_patching_the_pinned_counts_reaches_the_aggregate_check_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the F-114 regression -- `verify_completed_counts` carried the same trap."""
    monkeypatch.setattr(loader, "EXPECTED_COMPLETED_COUNTS", {SEASON: 999})
    with pytest.raises(LoaderIntegrityError, match="expected 999"):
        loader.verify_completed_counts({SEASON: 3})


# ── parsing: the normalization contract `dataset.games_from_frame` depends on ──


def test_only_completed_games_with_scores_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    rows = [
        _row("done"),
        _row("scheduled", completed="false"),
        {**_row("no-score"), "home_score": "", "away_score": ""},
    ]
    path.write_bytes(_csv_bytes(rows))
    frame = loader._read_completed_games(path, SEASON)
    assert list(frame["game_id"]) == ["done"]


def test_a_file_carrying_another_season_is_refused(tmp_path: Path) -> None:
    """A mis-filed download must not quietly contribute rows to the wrong season's count."""
    path = tmp_path / "s.csv"
    path.write_bytes(_csv_bytes([{**_row("x"), "season": "2025"}]))
    with pytest.raises(LoaderVerificationError, match="contains rows for season"):
        loader._read_completed_games(path, SEASON)


def test_home_win_is_derived_from_the_scores_not_from_a_source_column(tmp_path: Path) -> None:
    """The source carries `home_winner`, which this module deliberately does not read -- the
    derivation is one place and the scores are the fact."""
    path = tmp_path / "s.csv"
    rows = [
        _row("home-wins"),
        {**_row("away-wins"), "home_score": "99", "away_score": "104"},
    ]
    path.write_bytes(_csv_bytes(rows))
    frame = loader._read_completed_games(path, SEASON).set_index("game_id")
    assert bool(frame.loc["home-wins", "home_win"]) is True
    assert bool(frame.loc["away-wins", "home_win"]) is False


def test_the_normalized_frame_is_sorted_by_date(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    rows = [
        {**_row("late"), "date": "2024-03-01T00:00Z"},
        {**_row("early"), "date": "2024-01-01T00:00Z"},
    ]
    path.write_bytes(_csv_bytes(rows))
    frame = loader._read_completed_games(path, SEASON)
    assert list(frame["game_id"]) == ["early", "late"]


def test_load_season_verifies_before_returning(tmp_path: Path, pinned: bytes) -> None:
    """F-026: there is no path out of this module that skips verification. `load_season` is the
    chokepoint, so a wrong pinned count must surface here even though the bytes verify fine."""
    dest = tmp_path / f"nba_schedule_{SEASON}.csv"
    dest.write_bytes(pinned)
    with pytest.raises(LoaderIntegrityError, match="expected 999"):
        with patch.object(loader, "EXPECTED_COMPLETED_COUNTS", {SEASON: 999}):
            loader.load_season(SEASON, tmp_path)


def test_load_season_returns_a_verified_frame(tmp_path: Path, pinned: bytes) -> None:
    dest = tmp_path / f"nba_schedule_{SEASON}.csv"
    dest.write_bytes(pinned)
    frame = loader.load_season(SEASON, tmp_path)
    assert len(frame) == 3
    assert set(frame["game_id"]) == {"401-a", "401-b", "401-c"}


# ══════════════════════════════════════════════════════════════════════════════
# T-022 -- warm-up seasons and the box-score parquet assets
# ══════════════════════════════════════════════════════════════════════════════


BOX_SEASON = 2024


def _box_frame(kind: str, *, games: int, rows_per_game: int | None = None) -> pd.DataFrame:
    """A minimally valid box frame carrying exactly the required columns."""
    columns = (
        loader.PLAYER_BOX_REQUIRED_COLUMNS
        if kind == "player_box"
        else loader.TEAM_BOX_REQUIRED_COLUMNS
    )
    per_game = rows_per_game if rows_per_game is not None else (2 if kind == "team_box" else 15)
    game_ids = [f"g{i}" for i in range(games) for _ in range(per_game)]
    return pd.DataFrame({c: game_ids if c == "game_id" else [0] * len(game_ids) for c in columns})


def _parquet_bytes(frame: pd.DataFrame) -> bytes:
    import io

    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


# ── the warm-up split (D-037) ─────────────────────────────────────────────────


def test_warmup_seasons_are_pinned_but_are_not_modeling_seasons() -> None:
    """D-037's whole point. Being *pinned* is not the same as being *trainable*: warm-up seasons
    carry counts and hashes so they can be verified, and sit outside `SEASONS` so the training
    path's default cannot reach them."""
    assert set(loader.WARMUP_SEASONS).isdisjoint(loader.SEASONS)
    assert set(loader.WARMUP_SEASONS) <= set(loader.EXPECTED_COMPLETED_COUNTS)
    assert set(loader.WARMUP_SEASONS) <= set(loader.EXPECTED_SHA256)
    assert set(loader.SCHEDULE_SEASONS) == set(loader.WARMUP_SEASONS) | set(loader.SEASONS)


def test_the_training_default_cannot_return_a_warmup_row() -> None:
    """The pre-2020 home-advantage regime must never enter the fit. T-029 enforces this at the split
    boundary; this pins the ergonomic half -- `load_completed_games`'s default season set."""
    import inspect

    default = inspect.signature(loader.load_completed_games).parameters["seasons"].default
    assert default == loader.SEASONS
    assert set(default).isdisjoint(loader.WARMUP_SEASONS)


def test_a_warmup_season_is_a_valid_schedule_season_but_not_a_valid_box_season() -> None:
    """Box scores exist for the modeling window only -- Elo needs scores, availability does not
    reach back before the training window. Asking for a 2016 box must be refused here rather than
    discovered as a 404."""
    for season in loader.WARMUP_SEASONS:
        loader._validate_season(season)  # schedules: fine
        with pytest.raises(LoaderVerificationError, match="not one of the pinned seasons"):
            loader._validate_season(season, loader.BOX_SEASONS)


# ── parquet framing (T-022's new format) ──────────────────────────────────────


def test_an_empty_parquet_body_is_refused() -> None:
    with pytest.raises(LoaderVerificationError, match="is empty"):
        loader._validate_parquet_magic(b"", BOX_SEASON, source="test")


@pytest.mark.parametrize(
    "body",
    [b"PAR1", b"PAR1xx", b"not a parquet file at all", b"PAR1data", b"dataPAR1"],
    ids=["magic-only", "too-short", "no-magic", "head-only", "tail-only"],
)
def test_a_body_not_framed_as_parquet_is_refused(body: bytes) -> None:
    """A CSV header names its columns, so the text check can assert the schema. Parquet keeps its
    schema in a binary footer, so bytes-level verification stops at the framing -- which still
    catches an HTML error page, a truncated download, or a file of another format entirely."""
    with pytest.raises(LoaderVerificationError, match="not framed as a parquet file"):
        loader._validate_parquet_magic(body, BOX_SEASON, source="test")


def test_an_html_error_page_is_refused_by_the_parquet_framing_check() -> None:
    with pytest.raises(LoaderVerificationError, match="not framed as a parquet file"):
        loader._validate_parquet_magic(b"<html>404</html>", BOX_SEASON, source="test")


def test_a_real_parquet_file_passes_the_framing_check() -> None:
    """Non-vacuity: a framing check that refused everything would satisfy every test above."""
    raw = _parquet_bytes(_box_frame("team_box", games=3))
    loader._validate_parquet_magic(raw, BOX_SEASON, source="test")


def test_an_oversized_parquet_body_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_MAX_DOWNLOAD_BYTES", 16)
    with pytest.raises(LoaderVerificationError, match="over the 16"):
        loader._validate_parquet_magic(b"PAR1" + b"x" * 64 + b"PAR1", BOX_SEASON, source="test")


# ── box-score frame verification ──────────────────────────────────────────────


def test_an_unknown_box_family_is_refused() -> None:
    with pytest.raises(LoaderVerificationError, match="unknown box-score family"):
        loader._box_family("shot_chart")


@pytest.mark.parametrize("kind", ["player_box", "team_box"])
def test_a_correct_box_frame_passes(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """Non-vacuity control for every refusal below."""
    games = 10
    frame = _box_frame(kind, games=games)
    monkeypatch.setitem(loader.EXPECTED_COMPLETED_COUNTS, BOX_SEASON, games)
    monkeypatch.setitem(loader._BOX_FAMILIES[kind]["rows"], BOX_SEASON, len(frame))
    loader._verify_box(frame, kind, BOX_SEASON)


@pytest.mark.parametrize("kind", ["player_box", "team_box"])
def test_a_box_frame_missing_a_required_column_is_refused(kind: str) -> None:
    frame = _box_frame(kind, games=2).drop(columns=["game_id" if kind == "team_box" else "minutes"])
    with pytest.raises(LoaderIntegrityError, match="missing required column"):
        loader._verify_box(frame, kind, BOX_SEASON)


def test_minutes_and_did_not_play_are_both_required_and_not_redundant() -> None:
    """User story 12, pinned at the loader boundary.

    Verified on the real 2024 file: 6,638 rows carry `did_not_play=True` with null minutes (absence)
    while 268 carry `minutes == 0` with `did_not_play=False` (dressed, active, played nothing).
    Collapsing the two is precisely the garbage-time-reads-as-injury failure T-027 must avoid, so
    losing either column upstream has to be a hard failure rather than a silent degradation.
    """
    assert "minutes" in loader.PLAYER_BOX_REQUIRED_COLUMNS
    assert "did_not_play" in loader.PLAYER_BOX_REQUIRED_COLUMNS
    for dropped in ("minutes", "did_not_play"):
        frame = _box_frame("player_box", games=2).drop(columns=[dropped])
        with pytest.raises(LoaderIntegrityError, match="missing required column"):
            loader._verify_box(frame, "player_box", BOX_SEASON)


def test_a_box_row_count_mismatch_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _box_frame("team_box", games=10)
    monkeypatch.setitem(loader._BOX_FAMILIES["team_box"]["rows"], BOX_SEASON, 999)
    with pytest.raises(LoaderIntegrityError, match="got 20 rows, expected 999"):
        loader._verify_box(frame, "team_box", BOX_SEASON)


def test_a_box_file_covering_the_wrong_games_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cross-asset check, and the one that earns its keep.

    A file covering the wrong *set* of games at the right size passes every per-file check ever
    written -- it hashes to whatever it now contains, and its row count is whatever was pinned from
    it. Only comparing its game coverage against the schedule's pinned completed count can see it.
    """
    frame = _box_frame("team_box", games=10)
    monkeypatch.setitem(loader._BOX_FAMILIES["team_box"]["rows"], BOX_SEASON, len(frame))
    monkeypatch.setitem(loader.EXPECTED_COMPLETED_COUNTS, BOX_SEASON, 11)
    with pytest.raises(LoaderIntegrityError, match="covers 10 distinct games"):
        loader._verify_box(frame, "team_box", BOX_SEASON)


def test_a_team_box_game_missing_a_side_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two teams per game, always. A row count and a game count can both be right while one game
    carries three rows and another carries one."""
    frame = _box_frame("team_box", games=10)
    frame.loc[len(frame)] = frame.iloc[0]  # an eleventh row for an existing game
    monkeypatch.setitem(loader._BOX_FAMILIES["team_box"]["rows"], BOX_SEASON, len(frame))
    monkeypatch.setitem(loader.EXPECTED_COMPLETED_COUNTS, BOX_SEASON, 10)
    with pytest.raises(LoaderIntegrityError, match="is not 2 per game"):
        loader._verify_box(frame, "team_box", BOX_SEASON)


def test_player_box_is_not_subject_to_a_rows_per_game_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Roster sizes vary, so a fixed multiple would be wrong. The family descriptor says so
    explicitly rather than the check silently not applying."""
    assert loader._BOX_FAMILIES["player_box"]["rows_per_game"] is None
    frame = _box_frame("player_box", games=4, rows_per_game=13)
    monkeypatch.setitem(loader._BOX_FAMILIES["player_box"]["rows"], BOX_SEASON, len(frame))
    monkeypatch.setitem(loader.EXPECTED_COMPLETED_COUNTS, BOX_SEASON, 4)
    loader._verify_box(frame, "player_box", BOX_SEASON)


def test_an_unpinned_box_season_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "TEAM_BOX_EXPECTED_ROWS", {})
    monkeypatch.setitem(loader._BOX_FAMILIES["team_box"], "rows", {})
    with pytest.raises(LoaderIntegrityError, match="no pinned row count"):
        loader._verify_box(_box_frame("team_box", games=2), "team_box", BOX_SEASON)


def test_a_box_season_with_no_schedule_pin_has_nothing_to_cross_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _box_frame("team_box", games=2)
    monkeypatch.setitem(loader._BOX_FAMILIES["team_box"]["rows"], BOX_SEASON, len(frame))
    monkeypatch.setattr(loader, "EXPECTED_COMPLETED_COUNTS", {})
    with pytest.raises(LoaderIntegrityError, match="nothing to cross-check"):
        loader._verify_box(frame, "team_box", BOX_SEASON)


# ── box downloads reuse the schedule's security posture ───────────────────────


@pytest.fixture
def box_pinned(monkeypatch: pytest.MonkeyPatch) -> bytes:
    raw = _parquet_bytes(_box_frame("team_box", games=3))
    monkeypatch.setitem(
        loader._BOX_FAMILIES["team_box"]["hashes"], BOX_SEASON, hashlib.sha256(raw).hexdigest()
    )
    return raw


def test_a_box_download_refuses_a_redirect_that_drops_tls(
    tmp_path: Path, box_pinned: bytes
) -> None:
    fake = _urlopen_returning(box_pinned, url="http://release-assets.githubusercontent.com/x.parquet")
    with patch.object(loader.urllib.request, "urlopen", fake):
        with pytest.raises(LoaderVerificationError, match="refusing a redirect that dropped TLS"):
            loader.download_box_parquet("team_box", BOX_SEASON, tmp_path)


def test_a_box_download_refuses_an_unlisted_host(tmp_path: Path, box_pinned: bytes) -> None:
    """The box families share `_download_verified` with the schedules rather than reimplementing it,
    so this asserts the shared posture actually applies to the new path -- a second implementation
    is exactly how the two would drift."""
    fake = _urlopen_returning(box_pinned, url="https://evil.example.com/x.parquet")
    with patch.object(loader.urllib.request, "urlopen", fake):
        with pytest.raises(LoaderVerificationError, match="not in the allowlist"):
            loader.download_box_parquet("team_box", BOX_SEASON, tmp_path)


def test_a_box_download_refuses_a_content_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _parquet_bytes(_box_frame("team_box", games=3))
    monkeypatch.setitem(loader._BOX_FAMILIES["team_box"]["hashes"], BOX_SEASON, "0" * 64)
    fake = _urlopen_returning(raw, url="https://release-assets.githubusercontent.com/x.parquet")
    with patch.object(loader.urllib.request, "urlopen", fake):
        with pytest.raises(LoaderVerificationError, match="TEAM_BOX_EXPECTED_SHA256 pins"):
            loader.download_box_parquet("team_box", BOX_SEASON, tmp_path)


def test_a_box_download_into_an_empty_directory_works(tmp_path: Path, box_pinned: bytes) -> None:
    """F-037 again, for the new path. A populated cache has hidden a broken download in this repo
    before, so the branch is exercised against a directory that does not exist yet."""
    empty = tmp_path / "not-created-yet"
    assert not empty.exists()
    fake = _urlopen_returning(box_pinned, url="https://release-assets.githubusercontent.com/x.parquet")
    with patch.object(loader.urllib.request, "urlopen", fake):
        dest = loader.download_box_parquet("team_box", BOX_SEASON, empty)
    assert dest.read_bytes() == box_pinned
    assert list(empty.glob("*.part")) == []


def test_a_valid_cached_box_file_is_not_re_downloaded(tmp_path: Path, box_pinned: bytes) -> None:
    """Acceptance: re-running skips valid cached files."""
    dest = tmp_path / f"team_box_{BOX_SEASON}.parquet"
    dest.write_bytes(box_pinned)

    def _explode(*_args, **_kwargs):
        raise AssertionError("a valid cached box file must not trigger a download")

    with patch.object(loader.urllib.request, "urlopen", _explode):
        assert loader.download_box_parquet("team_box", BOX_SEASON, tmp_path) == dest


def test_a_tampered_cached_box_file_is_refused(tmp_path: Path, box_pinned: bytes) -> None:
    dest = tmp_path / f"team_box_{BOX_SEASON}.parquet"
    dest.write_bytes(box_pinned[:-8] + b"\x00\x00\x00\x00" + b"PAR1")
    with pytest.raises(LoaderVerificationError, match="upstream data has changed"):
        loader.download_box_parquet("team_box", BOX_SEASON, tmp_path)


def test_a_box_download_for_an_unpinned_season_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LoaderVerificationError, match="not one of the pinned seasons"):
        loader.download_box_parquet("team_box", 2016, tmp_path)
