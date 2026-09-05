"""Historical data loader (T-005).

Downloads the NBA season schedule CSVs sportsdataverse-data publishes from ESPN's schedule feed,
pinned to a fixed GitHub release tag, and normalizes them into a completed-game collection for the
Phase 1 analytical core (the not-yet-built features/splits/estimator modules).

Security (T-005's security note -- honored point for point; hardened after the T-005 review gate,
docs/IMPLEMENTATION.md "Review gate -- T-005 @ 32110ce", F-026..F-034):
  - The source is pinned by release TAG, not a moving branch/ref: `RELEASE_TAG` below. The tag alone
    does NOT pin *content* -- sportsdataverse-data re-uploads assets under it (verified via the
    GitHub API: `nba_schedule_2022.csv`, a season completed since 2022, carries `updated_at
    2026-07-29`). `EXPECTED_SHA256` is the actual content pin: every file is hashed and checked
    against a value recorded from the bytes that produced `EXPECTED_COMPLETED_COUNTS`, and a mismatch
    is a hard failure, never a silent re-baseline (F-030).
  - Bytes are verified -- size bound, header sanity, then content hash -- *before* the CSV parser
    ever sees them, for both a fresh download and a previously-cached file. The size cap is enforced
    via `Path.stat()` on the cached path too, before anything is read into memory (F-031).
  - The redirect GitHub issues for release assets (github.com -> a CDN host, observed 2026-08-09 as
    release-assets.githubusercontent.com) is required and followed, but the final response URL's
    scheme and host are checked against an explicit allowlist, so a redirect to plain `http://` or
    an unexpected host cannot pass silently (F-032). The authoritative list is
    `_ALLOWED_DOWNLOAD_HOSTS` below, with the reasoning for how to change it -- do not restate the
    host set here, which is how this note came to name a host GitHub had already stopped using
    (F-037, F-039).
  - `season` is validated against the pinned `SEASONS` tuple before it can shape a URL or a path, and
    the resolved destination path is asserted to stay inside the data dir before any write (F-033).
  - Every completed-game frame is verified -- pinned count AND `game_id` uniqueness -- inside
    `load_season` itself, the one function every caller (T-006/T-009, `load_completed_games`, or an
    ad-hoc script) goes through to get data out of this module. There is no path that returns data
    without both checks firing (F-026), and a count match alone is not treated as sufficient: a file
    that drops one real game and duplicates another keeps the count identical and previously passed
    silently (F-027). A season absent from `EXPECTED_COMPLETED_COUNTS` is refused outright rather than
    loaded unverified (F-028).
  - Parsing uses pandas' text CSV reader with every column typed as `str` -- a parser, never a
    deserializer that can execute code (no pickle, no yaml.load, no eval anywhere in this module).
  - All writes are confined to `DEFAULT_DATA_DIR`, inside the repo-root `data/` directory, which
    `.gitignore` excludes (verified with `git check-ignore`).

Dependency note (D-016, docs/plans/PLAN-current.md): this module needs pandas/numpy, which are
training-only dependencies (backend/requirements-train.txt), never installed into the served image.
backend/model/__init__.py stays empty specifically so importing the package -- as the FastAPI app
will, later, for inference -- never transitively imports this module or its heavy dependencies.

Testing note (revised by T-022 -- F-111): Phase 1 left this module deliberately un-unit-tested, on
the argument that upstream format drift is the real risk and no committed fixture can detect that by
construction. That argument is still correct and `backend/tests/test_loader.py` does not pretend
otherwise. What it covers is the half the argument never addressed: that when a tripwire *should*
fire it does, with the right error type -- which a fixture proves perfectly well, and which D-046
now leans on entirely, having made this machinery the integrity guarantee for the whole Postgres
corpus rather than for one CSV read.

The runtime assertions in `_verify_season` and `verify_completed_counts` remain the tripwire against
drift, and (post-F-026) they run on every call to `load_season`, not only when data is funneled
through `load_completed_games` -- there is no way to obtain data from this module without them
firing, and every mismatch (count, duplicate `game_id`, unpinned season, or content-hash drift)
raises hard rather than warning or logging.
"""

import hashlib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import certifi
import pandas as pd

from .corpus import WARMUP_SEASONS as _WARMUP_SEASONS

# --- pinned source (security note: a release TAG, never a branch) --------------------------------
_OWNER = "sportsdataverse"
_REPO = "sportsdataverse-data"
RELEASE_TAG = "espn_nba_schedules"
_RELEASE_BASE_URL = f"https://github.com/{_OWNER}/{_REPO}/releases/download/{RELEASE_TAG}"

# F-032: GitHub redirects release-asset downloads to a CDN host for the actual bytes -- that redirect
# is required, so it cannot simply be disabled. Instead the final response URL (after urllib follows
# it) is checked against this allowlist and against https, so a redirect to an unexpected host or a
# scheme downgrade can't pass silently.
#
# F-037: this list is OBSERVED, not assumed -- the first version guessed `objects.githubusercontent.com`
# and shipped broken, because every local run hit the populated `data/` cache and the download path
# never executed. Verified by live download on 2026-08-09: github.com/releases/download/... redirects
# to release-assets.githubusercontent.com. GitHub has moved this host before (objects.* -> release-
# assets.*), so when this check fires the correct response is to OBSERVE the new host and record it
# here with a date -- never to paste in whatever appeared, and never to delete the check.
_ALLOWED_DOWNLOAD_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",  # observed 2026-08-09
        "objects.githubusercontent.com",  # prior host; kept so older/mirrored links still resolve
    }
)

# T-022: the box-score families live under their own release tags. Same pinning discipline as the
# schedules above -- a tag, then a content hash, because a tag pins a filename and not its bytes.
_PLAYER_BOX_TAG = "espn_nba_player_boxscores"
_TEAM_BOX_TAG = "espn_nba_team_boxscores"

SEASONS: tuple[int, ...] = (2022, 2023, 2024, 2025, 2026)

# D-037: seasons ingested as Elo warm-up **state only**, never as training rows. They exist so fold 1
# trains on converged ratings instead of burn-in noise, and they stop there -- the pre-2020
# home-advantage regime must never enter the fit.
#
# Kept as a separate tuple from `SEASONS` rather than folded into it, because the difference is not
# cosmetic: `load_completed_games` defaults to `SEASONS` and that default is what keeps warm-up rows
# out of a training frame. Being *pinned* below is not the same as being *trainable*.
#
# **The tuple itself lives in `corpus`** (T-029) and is re-exported here. `splits` enforces the same
# distinction mechanically at the estimator boundary, it is standard-library-only, and it cannot
# import this pandas module -- so one definition had to live somewhere both could reach. Which
# seasons may be *trained on* is a curation policy; which seasons may be *downloaded* is this
# module's business, and the pinned counts and hashes for them stay here.
WARMUP_SEASONS = _WARMUP_SEASONS

# Every season this loader may fetch a schedule for. `_validate_season` gates on this; the split
# above governs what may be trained on.
SCHEDULE_SEASONS: tuple[int, ...] = WARMUP_SEASONS + SEASONS

# Box-score assets exist for the modeling seasons only. Availability (D-035) is a lagged feature over
# the training window; the warm-up seasons feed Elo, which needs scores and nothing else.
BOX_SEASONS: tuple[int, ...] = SEASONS

# Verified by direct download + count (docs/IMPLEMENTATION.md, T-005) -- the tripwire this loader
# must never silently pass through. A mismatch means either the upstream file changed shape, or this
# loader's parsing/filtering logic drifted from it; either way downstream numbers would be silently
# wrong without this check. Every season this loader will load MUST have an entry here -- a season
# missing one is refused outright by `_verify_season`, never loaded unverified (F-028).
#
# The 2016-2019 entries are T-022's warm-up seasons (D-037), verified the same way on 2026-09-04:
# each carries exactly 30 franchises once the All-Star phantom ids are excluded by the same
# games-played rule `corpus.py` already applies (2016/2017 carry 2 phantom ids, 2018/2019 carry 4).
EXPECTED_COMPLETED_COUNTS: dict[int, int] = {
    2016: 1317,
    2017: 1310,
    2018: 1314,
    2019: 1314,
    2022: 1324,
    2023: 1321,
    2024: 1320,
    2025: 1324,
    2026: 1326,
}

# F-030: the release TAG is stable; its assets are not. sportsdataverse-data re-uploads files under
# the same tag/filename -- verified via the GitHub API that `nba_schedule_2022.csv`, a season
# completed since 2022, carries `updated_at 2026-07-29`. A URL pinned by tag can therefore serve
# different bytes months later, silently, and a re-upload that corrects a score or a team id while
# leaving the row count unchanged would pass every check above this one. These hashes are computed
# from the files on disk that produced the verified EXPECTED_COMPLETED_COUNTS above (6,615 games
# total, all matching) -- this is what "pinned" means in this module: content, not just a filename.
#
# A mismatch here is a hard, deliberate failure (see `_validate_content_hash`) -- it means upstream
# data changed since this hash was recorded. It is NOT self-healing, must NEVER be edited to make a
# failure go away, and must NEVER be silently re-baselined by code. If it fires: stop, re-verify the
# new file's row counts and content by hand, decide whether the change is legitimate, and only then
# update both this dict and EXPECTED_COMPLETED_COUNTS deliberately, in a reviewed commit.
EXPECTED_SHA256: dict[int, str] = {
    # T-022 warm-up seasons, recorded 2026-09-04 from the bytes that produced the counts above.
    2016: "3373fb4ade99e7056228a4dfaa1c2128f7a255672e09c27dd3cab731edb53a9c",
    2017: "d17829cbcaaa5fee9f3cbb73289357e702786b02f5bc08ea9eb9b4ab4c718f03",
    2018: "ce772be7fc1627c0be22d5d18a65eef3dc699683738538d922320e4546ad12aa",
    2019: "b99913f6167a59d52150ab69fcc59df2fd588b8d255c2e5d4d3e0f8828c6c084",
    2022: "8cd13a11b16aa49f26e766e7af1d674aecb96ef427bfe1655740b7fd252b24ed",
    2023: "71aad62f4c082455ba7659b5cc900745a72b113b85bc22727ac04a603a9b5dca",
    2024: "ae89a6e51dec1817b41e457d02ffc4cfc26d25b411d318906c1ec5f39a6c5527",
    2025: "a7a5b6607a256c84a324f819e4461248bdff90a78b1ddb675a5a117a9a94e74f",
    2026: "5a4a7473fce1c49d56382211c5955ad405f421d59869143b1d501e997e79223e",
}

# --- T-022: box-score assets -------------------------------------------------------------------
#
# Same discipline as the schedules, one addition: these carry TWO pinned counts, not one. Rows catch
# a truncated or padded file; distinct `game_id`s catch the thing a row count structurally cannot --
# a file covering the wrong set of games at the right size.
#
# Recorded 2026-09-04 from live downloads. Two invariants held exactly across all five seasons and
# are asserted in `_verify_box`, not merely noted here:
#   - box `game_id` count == EXPECTED_COMPLETED_COUNTS for the same season, for BOTH families. The
#     box scores cover exactly the completed games the schedule pins -- no more, no fewer.
#   - team_box rows == 2 x games. Two teams per game, always.
# An upstream change that broke either would be invisible to a per-file hash alone, because each
# file would still hash to whatever it now contains; these are cross-asset checks.
PLAYER_BOX_EXPECTED_ROWS: dict[int, int] = {
    2022: 33880,
    2023: 34057,
    2024: 35028,
    2025: 35250,
    2026: 34883,
}

PLAYER_BOX_EXPECTED_SHA256: dict[int, str] = {
    2022: "474d15255f2a908f2c0ac0f7104b504c3cf2815b02dd0b0097c69cb634fe4395",
    2023: "968d835cd04b10533c5b5af8c0e41bf0154a73782e9446c76f765109223e8679",
    2024: "1e500a538ec45ddbcb58d9789b5494f8db4f25658eafd01d90f2d09600921665",
    2025: "e89f7fbc1124092c715b722e6d72d9dd1b9d28776ca6888b508fd2831cd705e2",
    2026: "29a29dc9efd055a93e069a0382ac830d98a13274bee655beba69d95cff099713",
}

TEAM_BOX_EXPECTED_ROWS: dict[int, int] = {
    2022: 2648,
    2023: 2642,
    2024: 2640,
    2025: 2648,
    2026: 2652,
}

TEAM_BOX_EXPECTED_SHA256: dict[int, str] = {
    2022: "f8185936836a81240d6f170ec7afeab47e3e736a718fe2c9f72999e681434b7f",
    2023: "b86bb087fe57eeeacead4e27965a20d1e67fdb39a3ee2ca36294a0b7d014e9b6",
    2024: "5e4d1b7a2ccc287f212f65888dcd674e370adec8a0e32d7ae0825eecacaaa8a9",
    2025: "f735bf3b743618e50987f3e3e296734e1dba43c450a21af848d7c34f23bf2aee",
    2026: "db846dfb39b0f75eee50ad91b1b5b7a7a62dd31d3ec0bb066a0a3bb08e366f7e",
}

# Only the columns the modeling layer actually consumes are required to be present. The source
# carries 57; naming all 57 here would turn a cosmetic upstream addition into a hard failure, while
# naming none would let the file this loader depends on change shape unnoticed.
#
# `minutes` and `did_not_play` are both required and are NOT redundant -- verified on the 2024 file:
# 6,638 rows have `did_not_play=True` with null minutes (absence) while 268 rows have `minutes == 0`
# with `did_not_play=False` (dressed, active, played nothing). Collapsing them is exactly the
# garbage-time-reads-as-injury failure user story 12 forbids, and T-027 needs both to avoid it.
PLAYER_BOX_REQUIRED_COLUMNS: tuple[str, ...] = (
    "game_id",
    "season",
    "season_type",
    "game_date",
    "athlete_id",
    "team_id",
    "minutes",
    "starter",
    "did_not_play",
    "active",
    "points",
)

TEAM_BOX_REQUIRED_COLUMNS: tuple[str, ...] = (
    "game_id",
    "season",
    "season_type",
    "game_date",
    "team_id",
    "team_home_away",
    "team_score",
)

# season_type: 2 = regular season, 3 = postseason, 5 = play-in. Not filtered on here -- the expected
# counts above are measured across all three, per T-005's acceptance criteria.
#
# `id` and `game_id` are identical in this source (verified); `game_id` is the name carried forward.
# T-023 added the five `venue_*` columns. They are *required*, not optional: `corpus_venues` is the
# join from a game to a city, and T-026's travel and altitude features have no fallback if it is
# absent -- a silent zero would read as "no travel". Verified present in all nine pinned schedule
# seasons on 2026-09-04, with `venue_id` and `venue_address_city` never blank. `venue_address_state`
# *is* sometimes blank (international games -- Mexico City, Paris, Abu Dhabi), which is why the
# corpus column is nullable and the city column is not.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "id",
    "game_id",
    "date",
    "season",
    "season_type",
    "home_id",
    "away_id",
    "home_score",
    "away_score",
    "status_type_completed",
    "neutral_site",
    "venue_id",
    "venue_full_name",
    "venue_address_city",
    "venue_address_state",
    "venue_indoor",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "raw" / "nba_schedules"
DEFAULT_PLAYER_BOX_DIR = REPO_ROOT / "data" / "raw" / "nba_player_box"
DEFAULT_TEAM_BOX_DIR = REPO_ROOT / "data" / "raw" / "nba_team_box"

_REQUEST_TIMEOUT_SECONDS = 30
# Explicit CA bundle (certifi -- declared directly in both requirements files as of F-034; already
# present transitively via httpx in the served image's requirements.txt, so this adds nothing there)
# rather than the platform default: the python.org macOS build does not read the system keychain, so
# `ssl.create_default_context()` alone fails closed with CERTIFICATE_VERIFY_FAILED on a clean install.
# This still verifies the certificate chain in full; it is a portability fix, never a relaxation of
# verification.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
_MAX_DOWNLOAD_BYTES = 50_000_000  # actual files run ~1.5-2.5 MB; this bounds a misbehaving response


class LoaderVerificationError(RuntimeError):
    """Raised when a downloaded (or cached) file fails verification before parsing."""


class LoaderIntegrityError(RuntimeError):
    """Raised when a normalized season's row count or row identity doesn't match the pinned expected
    values -- a wrong count, a duplicate `game_id`, or a season with no pinned expectation at all."""


def _validate_season(season: int, allowed: tuple[int, ...] = SCHEDULE_SEASONS) -> None:
    """F-033: refuse anything not in a pinned season tuple before it can shape a URL or a path.
    This is the single choke point every download function calls through, so `_asset_url` and
    `_dest_path` only ever see an already-validated season.

    T-022 parameterized `allowed` because the families no longer share one set: schedules exist for
    `SCHEDULE_SEASONS` (modeling plus D-037 warm-up) and box scores only for `BOX_SEASONS`. Asking a
    box download for 2016 must be refused here, not discovered as a 404 later.
    """
    if season not in allowed:
        raise LoaderVerificationError(
            f"season {season!r} is not one of the pinned seasons {allowed} -- refusing to build a "
            "URL or destination path from it"
        )


def _asset_url(season: int) -> str:
    return f"{_RELEASE_BASE_URL}/nba_schedule_{season}.csv"


def _contained_path(filename: str, data_dir: Path) -> Path:
    """Resolve `filename` inside `data_dir`, asserting it cannot escape (F-033).

    Defensive, not reachable with today's fixed inputs -- `season` is validated against a tuple of
    plain ints by the caller before this runs, so there is no way to smuggle a `..` segment through
    it. Kept because this is the assertion that actually proves the traversal is closed, rather than
    the old `url.startswith(...)` guard, which looked like it did but didn't (a `..` segment passes
    it).
    """
    dest = data_dir / filename
    resolved_dest = dest.resolve()
    resolved_data_dir = data_dir.resolve()
    if not resolved_dest.is_relative_to(resolved_data_dir):
        raise LoaderVerificationError(
            f"resolved destination {resolved_dest} escapes the data dir {resolved_data_dir} -- "
            "refusing to write"
        )
    return dest


def _dest_path(season: int, data_dir: Path) -> Path:
    return _contained_path(f"nba_schedule_{season}.csv", data_dir)


def _validate_header(raw: bytes, season: int, *, source: str) -> None:
    """Verify raw bytes look like the expected CSV before trusting them for parsing -- catches an
    HTML error page, a truncated download, or an unrelated file using only its first line."""
    if not raw:
        raise LoaderVerificationError(f"{source} for season {season} is empty")
    if len(raw) > _MAX_DOWNLOAD_BYTES:
        raise LoaderVerificationError(
            f"{source} for season {season} is {len(raw)} bytes, over the {_MAX_DOWNLOAD_BYTES} cap"
        )
    header_bytes = raw.split(b"\n", 1)[0]
    try:
        header_line = header_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        # F-029: a non-UTF-8 body (an HTML error page in an unexpected encoding, a binary blob served
        # at this URL) must fail through this module's own error type, not leak a raw stdlib one that
        # callers of this module aren't expecting to catch.
        raise LoaderVerificationError(
            f"{source} for season {season} is not valid UTF-8 in its header line -- got a different "
            "file than expected, refusing to parse it"
        ) from exc
    header_columns = {c.strip() for c in header_line.split(",")}
    missing = [c for c in REQUIRED_COLUMNS if c not in header_columns]
    if missing:
        raise LoaderVerificationError(
            f"{source} for season {season} is missing expected column(s) {missing} -- "
            "got a different file than expected, refusing to parse it"
        )


def _validate_content_hash(
    raw: bytes,
    season: int,
    *,
    source: str,
    pins: dict[int, str] | None = None,
    pin_name: str = "EXPECTED_SHA256",
) -> None:
    """F-030: the header/size checks above catch a wrong-*shaped* file; they do nothing against a
    same-shaped file with different content -- exactly what a silent upstream re-upload produces (a
    corrected score, a swapped team id) while row counts stay identical. This is the actual content
    pin `EXPECTED_SHA256` exists for.

    T-022 parameterized `pins`/`pin_name` so the box-score families get the identical treatment
    against their own dicts. The refusal wording is shared deliberately: it is the message someone
    reads at 2am, and the one tempting "fix" -- editing the pin until it passes -- destroys the
    guarantee for every asset equally.

    `pins` resolves inside the body rather than as a signature default, for the reason F-114
    documents on `_verify_season`.
    """
    if pins is None:
        pins = EXPECTED_SHA256

    actual_hash = hashlib.sha256(raw).hexdigest()
    # F-038: guard the lookup. A season can be in SEASONS and EXPECTED_COMPLETED_COUNTS but miss a
    # hash; a bare KeyError fails closed but reads as a bug rather than as "pin a hash first" -- the
    # same inconsistency F-028 and F-029 fixed in the other two verification paths.
    if season not in pins:
        raise LoaderVerificationError(
            f"season {season} has no pinned SHA-256 in {pin_name} -- add one (computed from a "
            f"download whose row counts you have verified by hand) before loading it."
        )
    expected_hash = pins[season]
    if actual_hash != expected_hash:
        raise LoaderVerificationError(
            f"{source} for season {season} has SHA-256 {actual_hash}, but {pin_name} pins "
            f"{expected_hash} -- upstream data has changed since that hash was recorded (see the "
            f"{pin_name} docstring above). This is not a transient failure: do not retry it, and "
            f"do not edit {pin_name} to make it pass. Re-verify the new file's row counts and "
            "content by hand first, then update EXPECTED_SHA256 and EXPECTED_COMPLETED_COUNTS "
            "deliberately, in a reviewed commit."
        )


# Parquet's own framing: every file opens and closes with this 4-byte magic, with the footer
# metadata immediately before the trailing copy.
_PARQUET_MAGIC = b"PAR1"


def _validate_parquet_magic(raw: bytes, season: int, *, source: str) -> None:
    """The parquet analogue of `_validate_header` -- does this look like the format we expect,
    before a reader is pointed at it.

    Weaker than the CSV header check by nature: a CSV header names its columns, so the text check
    can assert the *schema*. Parquet keeps its schema in a binary footer that only a reader can
    parse, so bytes-level verification can go no further than the framing. The column check for
    these files therefore happens after parsing, in `_verify_box` -- which is safe in a way it would
    not be for an executable format, because of what parquet is:

    **T-022 security note -- confirmed, not assumed.** Parquet is a columnar data format read here
    through pyarrow. It carries no code, no callables and no import directives; there is no parquet
    analogue of `pickle.loads`. The one genuine subtlety is that pandas stores its own metadata in
    the file's key-value footer and will honour a declared *extension dtype*, which means a crafted
    file can name a type pandas then resolves. That resolution is a lookup in an already-imported
    registry, not an import of an arbitrary module, and every file reaching this function has
    already matched a pinned SHA-256 over its exact bytes -- so the content is fixed before the
    reader sees it either way. This remains a parser, never a deserializer that can execute code,
    which is the invariant T-005's security note established and this extension had to preserve.
    """
    if not raw:
        raise LoaderVerificationError(f"{source} for season {season} is empty")
    if len(raw) > _MAX_DOWNLOAD_BYTES:
        raise LoaderVerificationError(
            f"{source} for season {season} is {len(raw)} bytes, over the {_MAX_DOWNLOAD_BYTES} cap"
        )
    # 8 bytes is the smallest conceivable well-framed file (two copies of the magic and nothing
    # between); anything shorter cannot carry both and is truncated by definition.
    if len(raw) < 8 or not raw.startswith(_PARQUET_MAGIC) or not raw.endswith(_PARQUET_MAGIC):
        raise LoaderVerificationError(
            f"{source} for season {season} is not framed as a parquet file (expected {_PARQUET_MAGIC!r} "
            "at both ends) -- got a different file than expected, refusing to parse it"
        )


def download_season_csv(season: int, data_dir: Path = DEFAULT_DATA_DIR, *, force: bool = False) -> Path:
    """Download one season's schedule CSV into `data_dir`, verifying it before it touches disk.

    Idempotent: if the destination already exists and still passes verification, this is a no-op
    unless `force=True`. Re-running the loader therefore never re-fetches or duplicates data.
    """
    _validate_season(season)
    dest = _dest_path(season, data_dir)

    def _verify(raw: bytes, source: str) -> None:
        _validate_header(raw, season, source=source)
        _validate_content_hash(raw, season, source=source)

    return _download_verified(_asset_url(season), dest, season=season, verify=_verify, force=force)


def _download_verified(
    url: str,
    dest: Path,
    *,
    season: int,
    verify: Callable[[bytes, str], None],
    force: bool = False,
) -> Path:
    """Fetch `url` to `dest`, running `verify` over the raw bytes before they are trusted.

    T-022 extracted this from `download_season_csv` so the box-score families get the identical
    posture rather than a second implementation that drifts from it: the same redirect allowlist,
    the same TLS assertion, the same size cap, the same cache verification, the same atomic write.
    `verify` is the only thing that varies -- a CSV header check or a parquet framing check, each
    followed by that family's content pin.

    Idempotent: an existing `dest` that still passes `verify` is a no-op unless `force=True`.
    """
    if dest.exists() and not force:
        # F-031: check the real size on disk, cheaply, before reading anything into memory. The
        # previous version only ever read a fixed 8KB header peek here, so the size cap was checked
        # against that slice, not the file -- trivially satisfied by an oversized cached file.
        cached_size = dest.stat().st_size
        if cached_size > _MAX_DOWNLOAD_BYTES:
            raise LoaderVerificationError(
                f"cached file {dest} is {cached_size} bytes, over the {_MAX_DOWNLOAD_BYTES} cap -- "
                "refusing to read it into memory; delete it and re-run to re-download"
            )
        raw = dest.read_bytes()
        verify(raw, f"cached file {dest}")
        return dest  # already downloaded, still the right shape, and hashes to the pinned content

    request = urllib.request.Request(url, headers={"User-Agent": "sports-dashboard-loader/1.0"})
    try:
        with urllib.request.urlopen(
            request, timeout=_REQUEST_TIMEOUT_SECONDS, context=_SSL_CONTEXT
        ) as response:
            # F-032: assert what we actually landed on after redirects, not just the request URL.
            final_url = urllib.parse.urlsplit(response.geturl())
            if final_url.scheme != "https":
                raise LoaderVerificationError(
                    f"final response for {url} came back over {final_url.scheme!r}, not https -- "
                    "refusing a redirect that dropped TLS"
                )
            if final_url.hostname not in _ALLOWED_DOWNLOAD_HOSTS:
                raise LoaderVerificationError(
                    f"final response for {url} came from host {final_url.hostname!r}, not in the "
                    f"allowlist {sorted(_ALLOWED_DOWNLOAD_HOSTS)} -- refusing an unexpected redirect "
                    "target"
                )
            if response.status != 200:
                raise LoaderVerificationError(f"GET {url} returned HTTP {response.status}")
            raw = response.read(_MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.URLError as exc:
        raise LoaderVerificationError(f"failed to download {url}: {exc}") from exc

    verify(raw, f"download from {url}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_name(dest.name + ".part")
    tmp_path.write_bytes(raw)
    tmp_path.replace(dest)  # atomic rename -- `dest` never observably holds a partial write
    return dest


# --- T-022: box-score assets --------------------------------------------------------------------
#
# One descriptor per family, so `download_box_parquet` and `_verify_box` stay single functions
# rather than a pair of near-copies that drift. Each names its release tag, filename stem, default
# directory, pinned hashes, pinned row counts and required columns.
_BOX_FAMILIES: dict[str, dict] = {
    "player_box": {
        "tag": _PLAYER_BOX_TAG,
        "stem": "player_box",
        "dir": DEFAULT_PLAYER_BOX_DIR,
        "hashes": PLAYER_BOX_EXPECTED_SHA256,
        "hash_name": "PLAYER_BOX_EXPECTED_SHA256",
        "rows": PLAYER_BOX_EXPECTED_ROWS,
        "columns": PLAYER_BOX_REQUIRED_COLUMNS,
        "rows_per_game": None,  # varies with roster size
    },
    "team_box": {
        "tag": _TEAM_BOX_TAG,
        "stem": "team_box",
        "dir": DEFAULT_TEAM_BOX_DIR,
        "hashes": TEAM_BOX_EXPECTED_SHA256,
        "hash_name": "TEAM_BOX_EXPECTED_SHA256",
        "rows": TEAM_BOX_EXPECTED_ROWS,
        "columns": TEAM_BOX_REQUIRED_COLUMNS,
        "rows_per_game": 2,  # two teams per game, always
    },
}


def _box_family(kind: str) -> dict:
    if kind not in _BOX_FAMILIES:
        raise LoaderVerificationError(
            f"unknown box-score family {kind!r} -- expected one of {sorted(_BOX_FAMILIES)}"
        )
    return _BOX_FAMILIES[kind]


def download_box_parquet(
    kind: str, season: int, data_dir: Path | None = None, *, force: bool = False
) -> Path:
    """Download one season's player or team box-score parquet, verifying it before it touches disk.

    Same posture as `download_season_csv` point for point -- pinned release tag, allowlisted
    redirect target, size cap, content hash over the exact bytes, atomic write inside the gitignored
    data directory. The only difference is the framing check, because parquet's schema lives in a
    binary footer rather than a text header; see `_validate_parquet_magic`.
    """
    family = _box_family(kind)
    _validate_season(season, BOX_SEASONS)
    target_dir = family["dir"] if data_dir is None else data_dir
    dest = _contained_path(f"{family['stem']}_{season}.parquet", target_dir)

    def _verify(raw: bytes, source: str) -> None:
        _validate_parquet_magic(raw, season, source=source)
        _validate_content_hash(
            raw, season, source=source, pins=family["hashes"], pin_name=family["hash_name"]
        )

    url = f"https://github.com/{_OWNER}/{_REPO}/releases/download/{family['tag']}/{dest.name}"
    return _download_verified(url, dest, season=season, verify=_verify, force=force)


def _verify_box(df: pd.DataFrame, kind: str, season: int) -> None:
    """Verify one season's box-score frame after parsing.

    Three checks, none of which the others subsume:

      - **required columns present.** The bytes check can only assert parquet framing, so this is
        where the schema is actually pinned. Only the columns the modeling layer consumes are
        required, so a cosmetic upstream addition is not a hard failure.
      - **row count matches the pin.** Catches truncation and padding.
      - **distinct `game_id` count matches `EXPECTED_COMPLETED_COUNTS` for the same season.** This is
        the cross-asset check, and it is the one that earns its keep: a file covering the wrong set
        of games at the right size passes every per-file check ever written, because it hashes to
        whatever it now contains. Verified to hold exactly across all five seasons and both families
        on 2026-09-04. For `team_box` the row count is additionally asserted to be exactly twice the
        game count.
    """
    family = _box_family(kind)

    missing = [c for c in family["columns"] if c not in df.columns]
    if missing:
        raise LoaderIntegrityError(
            f"{kind} {season}: missing required column(s) {missing} -- the upstream schema has "
            "changed; refusing to return a frame the modeling layer cannot read"
        )

    if season not in family["rows"]:
        raise LoaderIntegrityError(
            f"{kind} season {season} has no pinned row count in {kind.upper()}_EXPECTED_ROWS -- "
            "refusing to load an unpinned season. Verify its counts by hand and pin them "
            "deliberately before loading it."
        )
    expected_rows = family["rows"][season]
    if len(df) != expected_rows:
        raise LoaderIntegrityError(
            f"{kind} {season}: got {len(df)} rows, expected {expected_rows} "
            f"(pinned in {kind.upper()}_EXPECTED_ROWS)"
        )

    if season not in EXPECTED_COMPLETED_COUNTS:
        raise LoaderIntegrityError(
            f"{kind} season {season} has no pinned expected count in EXPECTED_COMPLETED_COUNTS -- "
            "there is nothing to cross-check the game coverage against; refusing to return it"
        )
    expected_games = EXPECTED_COMPLETED_COUNTS[season]
    actual_games = int(df["game_id"].nunique())
    if actual_games != expected_games:
        raise LoaderIntegrityError(
            f"{kind} {season}: covers {actual_games} distinct games, but the schedule pins "
            f"{expected_games} completed games for that season -- the box scores and the schedule "
            "disagree about which games exist. A row count alone cannot see this, because the file "
            "hashes to whatever it now contains; refusing to return it."
        )

    rows_per_game = family["rows_per_game"]
    if rows_per_game is not None and len(df) != rows_per_game * actual_games:
        raise LoaderIntegrityError(
            f"{kind} {season}: {len(df)} rows across {actual_games} games is not "
            f"{rows_per_game} per game -- a game is missing a side, or carries an extra one"
        )


def load_box(
    kind: str, season: int, data_dir: Path | None = None, *, force: bool = False
) -> pd.DataFrame:
    """Download (if needed), read, and verify one season's box-score frame.

    F-026's shape, applied to the new families: this is the one function callers go through, so
    there is no path that returns box-score data without `_verify_box` firing.
    """
    path = download_box_parquet(kind, season, data_dir, force=force)
    df = pd.read_parquet(path)
    _verify_box(df, kind, season)
    return df


def load_warmup_games(
    data_dir: Path = DEFAULT_DATA_DIR, *, force: bool = False
) -> pd.DataFrame:
    """Load the D-037 warm-up seasons as a completed-game frame.

    Deliberately a **separate function** from `load_completed_games` rather than a wider default on
    it. Warm-up seasons exist to converge Elo ratings and must never become training rows, and the
    cheapest way to guarantee that is for the training path's default to be incapable of returning
    them. T-029 enforces the same thing mechanically at the split boundary; this is the ergonomic
    half -- nobody reaches warm-up rows by accident.
    """
    frames = [load_season(season, data_dir, force=force) for season in WARMUP_SEASONS]
    return pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)


def _read_completed_games(path: Path, season: int) -> pd.DataFrame:
    """Parse one season's CSV and normalize it to its completed games.

    Uses pandas' text CSV reader (never a deserializer that can execute code) with every column
    typed as string, so embedded newlines/commas inside quoted fields -- present in this source's
    `highlights` column, for example -- can't be mistaken for row or column boundaries.
    """
    raw = pd.read_csv(
        path,
        usecols=list(REQUIRED_COLUMNS),
        dtype=str,
        keep_default_na=False,
        low_memory=False,
    )

    bad_seasons = sorted(raw.loc[raw["season"] != str(season), "season"].unique())
    if bad_seasons:
        raise LoaderVerificationError(
            f"{path} contains rows for season(s) {bad_seasons}, expected only {season}"
        )

    is_completed = raw["status_type_completed"] == "true"
    has_scores = (raw["home_score"].str.strip() != "") & (raw["away_score"].str.strip() != "")
    completed = raw.loc[is_completed & has_scores]

    normalized = pd.DataFrame({
        "game_id": completed["game_id"].astype(str),
        "date": pd.to_datetime(completed["date"], utc=True),
        "season": completed["season"].astype(int),
        "season_type": completed["season_type"].astype(int),
        "home_id": completed["home_id"].astype(str),
        "away_id": completed["away_id"].astype(str),
        "home_score": completed["home_score"].astype(int),
        "away_score": completed["away_score"].astype(int),
        "neutral_site": completed["neutral_site"] == "true",
        # T-023: carried through for `corpus_venues`. Blanks become None rather than the empty
        # string, so "the source did not say" is one value in the frame and in the database instead
        # of two that compare unequal. `venue_id` and `venue_address_city` are never blank in the
        # pinned files; `venue_address_state` is, for international games.
        "venue_id": completed["venue_id"].astype(str).str.strip().replace("", None),
        "venue_name": completed["venue_full_name"].astype(str).str.strip().replace("", None),
        "venue_city": completed["venue_address_city"].astype(str).str.strip().replace("", None),
        "venue_state": completed["venue_address_state"].astype(str).str.strip().replace("", None),
        "venue_indoor": completed["venue_indoor"] == "true",
    })
    normalized["home_win"] = normalized["home_score"] > normalized["away_score"]
    return normalized.sort_values("date").reset_index(drop=True)


def _verify_season(
    df: pd.DataFrame, season: int, *, expected: dict[int, int] | None = None
) -> None:
    """Verify one season's normalized completed-game frame. Called from `load_season` itself (F-026)
    so there is no path -- `load_completed_games`, T-006/T-009 calling `load_season` directly, or an
    ad-hoc script -- that can obtain data from this module without both checks below firing.

    Two independent checks, both required:
      - the season is pinned in `expected` at all (F-028): an unpinned season is refused outright,
        never loaded silently unverified.
      - `game_id` is unique across the frame (F-027): a count match alone passes a corrupted file
        that drops one real game and duplicates another in its place -- the count stays identical,
        one real game silently goes missing. Checked before the count comparison, not instead of it.

    F-114 (the F-070 trap): `expected` defaults to `None` and resolves to the module global *inside
    the body*, rather than binding `EXPECTED_COMPLETED_COUNTS` as a signature default. A signature
    default is evaluated once at def time, so it captures the dict *object* that existed then --
    `monkeypatch.setattr(loader, "EXPECTED_COMPLETED_COUNTS", ...)` would rebind the module attribute
    and this function would go on consulting the original. A test written that way would patch in a
    deliberately wrong count, watch the check pass, and conclude the tripwire works. The trap lies
    directly across the tests F-111 asks for, which is why it is closed here rather than worked
    around in `test_loader.py`.
    """
    if expected is None:
        expected = EXPECTED_COMPLETED_COUNTS

    if season not in expected:
        raise LoaderIntegrityError(
            f"season {season} has no pinned expected count in EXPECTED_COMPLETED_COUNTS -- refusing "
            "to load an unpinned season. Verify its count by hand and pin it deliberately before "
            "loading it."
        )

    game_id_counts = df["game_id"].value_counts()
    duplicated = game_id_counts[game_id_counts > 1]
    if not duplicated.empty:
        raise LoaderIntegrityError(
            f"season {season}: {len(duplicated)} duplicate game_id value(s) in the normalized "
            f"completed-game frame (first few: {dict(duplicated.head(5))}) -- a matching row count "
            "can hide a dropped real game behind a duplicated one; refusing to return unverified data"
        )

    actual_count = len(df)
    expected_count = expected[season]
    if actual_count != expected_count:
        raise LoaderIntegrityError(
            f"season {season}: got {actual_count} completed games, expected {expected_count} "
            "(pinned in EXPECTED_COMPLETED_COUNTS)"
        )


def load_season(season: int, data_dir: Path = DEFAULT_DATA_DIR, *, force: bool = False) -> pd.DataFrame:
    """Download (if needed), normalize, and verify one season's completed-game collection.

    F-026: verification is not optional and not deferred to a caller. This is the one function every
    path through this module goes through to get data out of it, so it is the one place verification
    cannot be bypassed from -- `load_completed_games` calls it in a loop, and T-006/T-009 or an
    ad-hoc script calling it directly get exactly the same guarantee.
    """
    path = download_season_csv(season, data_dir, force=force)
    df = _read_completed_games(path, season)
    _verify_season(df, season)
    return df


def verify_completed_counts(
    actual: dict[int, int], expected: dict[int, int] | None = None
) -> None:
    """Aggregate second pass (docs/plans/PLAN-current.md Testing Decisions): by the time
    `load_completed_games` calls this, every season it loaded already passed `_verify_season`
    individually inside `load_season`, so this mainly guards the concatenation step itself. Kept
    strict independently of that ordering (F-028) in case this is ever called directly with a
    hand-built `actual`: a season present in `actual` but absent from `expected` fails here too,
    rather than silently contributing nothing to the loop below.

    `expected` resolves inside the body for the same reason `_verify_season`'s does -- see F-114
    there.
    """
    if expected is None:
        expected = EXPECTED_COMPLETED_COUNTS

    unpinned = sorted(set(actual) - set(expected))
    if unpinned:
        raise LoaderIntegrityError(
            f"season(s) {unpinned} are present in `actual` but have no pinned expected count in "
            "EXPECTED_COMPLETED_COUNTS -- refusing to treat them as verified"
        )
    mismatches = [
        f"  season {season}: got {actual.get(season)}, expected {expected_count}"
        for season, expected_count in expected.items()
        if actual.get(season) != expected_count
    ]
    if mismatches:
        raise LoaderIntegrityError(
            "completed-game counts do not match the pinned expected values:\n" + "\n".join(mismatches)
        )


def load_completed_games(
    seasons: tuple[int, ...] = SEASONS, data_dir: Path = DEFAULT_DATA_DIR, *, force: bool = False
) -> pd.DataFrame:
    """Load and normalize every requested season, verify the tripwire counts, and return one
    combined completed-game collection sorted by date."""
    per_season_counts: dict[int, int] = {}
    frames = []
    for season in seasons:
        df = load_season(season, data_dir, force=force)
        per_season_counts[season] = len(df)
        frames.append(df)

    expected_subset = {s: c for s, c in EXPECTED_COMPLETED_COUNTS.items() if s in seasons}
    verify_completed_counts(per_season_counts, expected_subset)

    return pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)


def main() -> int:
    combined = load_completed_games()
    print(f"Loaded {len(combined)} completed games across {len(SEASONS)} seasons from {DEFAULT_DATA_DIR}")
    for season in SEASONS:
        count = int((combined["season"] == season).sum())
        print(f"  {season}: {count} completed (expected {EXPECTED_COMPLETED_COUNTS[season]})")
    print("All per-season counts match the pinned expected values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
