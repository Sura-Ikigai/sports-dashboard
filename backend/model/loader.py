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
  - The redirect GitHub issues for release assets (github.com -> objects.githubusercontent.com) is
    required and followed, but the final response URL's scheme and host are checked against an
    explicit allowlist, so a redirect to plain `http://` or an unexpected host cannot pass silently
    (F-032).
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

Testing note: this loader is deliberately not unit-tested in Phase 1 (docs/plans/PLAN-current.md,
Testing Decisions) -- upstream format drift is the real risk here, and no committed fixture can
detect that by construction. The runtime assertions in `_verify_season` and `verify_completed_counts`
are the accepted tripwire in its place, and (post-F-026) they run on every call to `load_season`, not
only when data is funneled through `load_completed_games` -- there is no way to obtain data from this
module without them firing, and every mismatch (count, duplicate `game_id`, unpinned season, or
content-hash drift) raises hard rather than warning or logging.
"""

import hashlib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import certifi
import pandas as pd

# --- pinned source (security note: a release TAG, never a branch) --------------------------------
_OWNER = "sportsdataverse"
_REPO = "sportsdataverse-data"
RELEASE_TAG = "espn_nba_schedules"
_RELEASE_BASE_URL = f"https://github.com/{_OWNER}/{_REPO}/releases/download/{RELEASE_TAG}"

# F-032: GitHub redirects release-asset downloads to objects.githubusercontent.com for the actual
# bytes -- that redirect is required, so it cannot simply be disabled. Instead the final response URL
# (after urllib follows it) is checked against this allowlist and against https, so a redirect to an
# unexpected host or a scheme downgrade can't pass silently.
_ALLOWED_DOWNLOAD_HOSTS: frozenset[str] = frozenset({"github.com", "objects.githubusercontent.com"})

SEASONS: tuple[int, ...] = (2022, 2023, 2024, 2025, 2026)

# Verified by direct download + count (docs/IMPLEMENTATION.md, T-005) -- the tripwire this loader
# must never silently pass through. A mismatch means either the upstream file changed shape, or this
# loader's parsing/filtering logic drifted from it; either way downstream numbers would be silently
# wrong without this check. Every season this loader will load MUST have an entry here -- a season
# missing one is refused outright by `_verify_season`, never loaded unverified (F-028).
EXPECTED_COMPLETED_COUNTS: dict[int, int] = {
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
    2022: "8cd13a11b16aa49f26e766e7af1d674aecb96ef427bfe1655740b7fd252b24ed",
    2023: "71aad62f4c082455ba7659b5cc900745a72b113b85bc22727ac04a603a9b5dca",
    2024: "ae89a6e51dec1817b41e457d02ffc4cfc26d25b411d318906c1ec5f39a6c5527",
    2025: "a7a5b6607a256c84a324f819e4461248bdff90a78b1ddb675a5a117a9a94e74f",
    2026: "5a4a7473fce1c49d56382211c5955ad405f421d59869143b1d501e997e79223e",
}

# season_type: 2 = regular season, 3 = postseason, 5 = play-in. Not filtered on here -- the expected
# counts above are measured across all three, per T-005's acceptance criteria.
#
# `id` and `game_id` are identical in this source (verified); `game_id` is the name carried forward.
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
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "raw" / "nba_schedules"

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


def _validate_season(season: int) -> None:
    """F-033: refuse anything not in the pinned SEASONS tuple before it can shape a URL or a path.
    This is the single choke point `download_season_csv` calls through, so both `_asset_url` and
    `_dest_path` only ever see an already-validated season."""
    if season not in SEASONS:
        raise LoaderVerificationError(
            f"season {season!r} is not one of the pinned seasons {SEASONS} -- refusing to build a "
            "URL or destination path from it"
        )


def _asset_url(season: int) -> str:
    return f"{_RELEASE_BASE_URL}/nba_schedule_{season}.csv"


def _dest_path(season: int, data_dir: Path) -> Path:
    dest = data_dir / f"nba_schedule_{season}.csv"
    # F-033: the resolved path is asserted to stay inside the data dir. Defensive, not reachable with
    # today's fixed inputs -- `season` is validated against SEASONS (a tuple of plain ints) by the
    # caller before this runs, so there is no way to smuggle a `..` segment through it. Kept because
    # this is the assertion that actually proves the traversal is closed, rather than the old
    # `url.startswith(...)` guard, which looked like it did but didn't (a `..` segment passes it).
    resolved_dest = dest.resolve()
    resolved_data_dir = data_dir.resolve()
    if not resolved_dest.is_relative_to(resolved_data_dir):
        raise LoaderVerificationError(
            f"resolved destination {resolved_dest} escapes the data dir {resolved_data_dir} -- "
            "refusing to write"
        )
    return dest


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


def _validate_content_hash(raw: bytes, season: int, *, source: str) -> None:
    """F-030: the header/size checks above catch a wrong-*shaped* file; they do nothing against a
    same-shaped file with different content -- exactly what a silent upstream re-upload produces (a
    corrected score, a swapped team id) while row counts stay identical. This is the actual content
    pin `EXPECTED_SHA256` exists for."""
    actual_hash = hashlib.sha256(raw).hexdigest()
    expected_hash = EXPECTED_SHA256[season]  # season already validated against SEASONS by this point
    if actual_hash != expected_hash:
        raise LoaderVerificationError(
            f"{source} for season {season} has SHA-256 {actual_hash}, but EXPECTED_SHA256 pins "
            f"{expected_hash} -- upstream data has changed since that hash was recorded (see the "
            "EXPECTED_SHA256 docstring above). This is not a transient failure: do not retry it, and "
            "do not edit EXPECTED_SHA256 to make it pass. Re-verify the new file's row counts and "
            "content by hand first, then update EXPECTED_SHA256 and EXPECTED_COMPLETED_COUNTS "
            "deliberately, in a reviewed commit."
        )


def download_season_csv(season: int, data_dir: Path = DEFAULT_DATA_DIR, *, force: bool = False) -> Path:
    """Download one season's schedule CSV into `data_dir`, verifying it before it touches disk.

    Idempotent: if the destination already exists and still passes verification, this is a no-op
    unless `force=True`. Re-running the loader therefore never re-fetches or duplicates data.
    """
    _validate_season(season)
    dest = _dest_path(season, data_dir)

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
        _validate_header(raw, season, source=f"cached file {dest}")
        _validate_content_hash(raw, season, source=f"cached file {dest}")
        return dest  # already downloaded, still the right shape, and hashes to the pinned content

    url = _asset_url(season)
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

    _validate_header(raw, season, source=f"download from {url}")
    _validate_content_hash(raw, season, source=f"download from {url}")

    data_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_name(dest.name + ".part")
    tmp_path.write_bytes(raw)
    tmp_path.replace(dest)  # atomic rename -- `dest` never observably holds a partial write
    return dest


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
    })
    normalized["home_win"] = normalized["home_score"] > normalized["away_score"]
    return normalized.sort_values("date").reset_index(drop=True)


def _verify_season(
    df: pd.DataFrame, season: int, *, expected: dict[int, int] = EXPECTED_COMPLETED_COUNTS
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
    """
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
    actual: dict[int, int], expected: dict[int, int] = EXPECTED_COMPLETED_COUNTS
) -> None:
    """Aggregate second pass (docs/plans/PLAN-current.md Testing Decisions): by the time
    `load_completed_games` calls this, every season it loaded already passed `_verify_season`
    individually inside `load_season`, so this mainly guards the concatenation step itself. Kept
    strict independently of that ordering (F-028) in case this is ever called directly with a
    hand-built `actual`: a season present in `actual` but absent from `expected` fails here too,
    rather than silently contributing nothing to the loop below.
    """
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
