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


def _row(game_id: str, *, home: str = "1", away: str = "2", completed: str = "true") -> dict:
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
        "neutral_site": "false",
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
    """Non-vacuity control: a guard that rejected everything would satisfy the test above."""
    for season in loader.SEASONS:
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
