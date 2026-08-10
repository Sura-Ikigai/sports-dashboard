"""Historical data loader (T-005).

Downloads the NBA season schedule CSVs sportsdataverse-data publishes from ESPN's schedule feed,
pinned to a fixed GitHub release tag, and normalizes them into a completed-game collection for the
Phase 1 analytical core (the not-yet-built features/splits/estimator modules).

Security (T-005's security note -- honored point for point):
  - The source is pinned by release TAG, not a moving branch/ref: `RELEASE_TAG` below.
  - Bytes are verified (size bound + header sanity) *before* the CSV parser ever sees them, for both
    a fresh download and a previously-cached file.
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
detect that by construction. `verify_completed_counts` is the accepted tripwire in its place: every
run re-asserts the known-good per-season counts and fails hard (raises, not logs) on any mismatch.
"""

import ssl
import urllib.error
import urllib.request
from pathlib import Path

import certifi
import pandas as pd

# --- pinned source (security note: a release TAG, never a branch) --------------------------------
_OWNER = "sportsdataverse"
_REPO = "sportsdataverse-data"
RELEASE_TAG = "espn_nba_schedules"
_RELEASE_BASE_URL = f"https://github.com/{_OWNER}/{_REPO}/releases/download/{RELEASE_TAG}"

SEASONS: tuple[int, ...] = (2022, 2023, 2024, 2025, 2026)

# Verified by direct download + count (docs/IMPLEMENTATION.md, T-005) -- the tripwire this loader
# must never silently pass through. A mismatch means either the upstream file changed shape, or this
# loader's parsing/filtering logic drifted from it; either way downstream numbers would be silently
# wrong without this check.
EXPECTED_COMPLETED_COUNTS: dict[int, int] = {
    2022: 1324,
    2023: 1321,
    2024: 1320,
    2025: 1324,
    2026: 1326,
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
# Explicit CA bundle (certifi -- already a transitive dependency via httpx in requirements.txt, not
# a new one added for this task) rather than the platform default: the python.org macOS build does
# not read the system keychain, so `ssl.create_default_context()` alone fails closed with
# CERTIFICATE_VERIFY_FAILED on a clean install. This still verifies the certificate chain in full;
# it is a portability fix, never a relaxation of verification.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
_MAX_DOWNLOAD_BYTES = 50_000_000  # actual files run ~1.5-2.5 MB; this bounds a misbehaving response
_HEADER_PEEK_BYTES = 8192  # comfortably more than one header line of this source's ~80 columns


class LoaderVerificationError(RuntimeError):
    """Raised when a downloaded (or cached) file fails verification before parsing."""


class LoaderIntegrityError(RuntimeError):
    """Raised when normalized per-season counts don't match the pinned expected values."""


def _asset_url(season: int) -> str:
    return f"{_RELEASE_BASE_URL}/nba_schedule_{season}.csv"


def _dest_path(season: int, data_dir: Path) -> Path:
    return data_dir / f"nba_schedule_{season}.csv"


def _validate_header(raw: bytes, season: int, *, source: str) -> None:
    """Verify raw bytes look like the expected CSV before trusting them for parsing -- catches an
    HTML error page, a truncated download, or an unrelated file using only its first line."""
    if not raw:
        raise LoaderVerificationError(f"{source} for season {season} is empty")
    if len(raw) > _MAX_DOWNLOAD_BYTES:
        raise LoaderVerificationError(
            f"{source} for season {season} is {len(raw)} bytes, over the {_MAX_DOWNLOAD_BYTES} cap"
        )
    header_line = raw.split(b"\n", 1)[0].decode("utf-8", errors="strict")
    header_columns = {c.strip() for c in header_line.split(",")}
    missing = [c for c in REQUIRED_COLUMNS if c not in header_columns]
    if missing:
        raise LoaderVerificationError(
            f"{source} for season {season} is missing expected column(s) {missing} -- "
            "got a different file than expected, refusing to parse it"
        )


def download_season_csv(season: int, data_dir: Path = DEFAULT_DATA_DIR, *, force: bool = False) -> Path:
    """Download one season's schedule CSV into `data_dir`, verifying it before it touches disk.

    Idempotent: if the destination already exists and still passes verification, this is a no-op
    unless `force=True`. Re-running the loader therefore never re-fetches or duplicates data.
    """
    dest = _dest_path(season, data_dir)

    if dest.exists() and not force:
        with dest.open("rb") as fh:
            header_chunk = fh.read(_HEADER_PEEK_BYTES)
        _validate_header(header_chunk, season, source=f"cached file {dest}")
        return dest  # already downloaded and still looks like the right file

    url = _asset_url(season)
    if not url.startswith(_RELEASE_BASE_URL + "/"):
        # Defensive, not reachable with today's fixed inputs: refuse anything that isn't under the
        # pinned release, in case this function is ever called with a different `season` source.
        raise LoaderVerificationError(f"refusing to fetch a URL outside the pinned release: {url}")

    request = urllib.request.Request(url, headers={"User-Agent": "sports-dashboard-loader/1.0"})
    try:
        with urllib.request.urlopen(
            request, timeout=_REQUEST_TIMEOUT_SECONDS, context=_SSL_CONTEXT
        ) as response:
            if response.status != 200:
                raise LoaderVerificationError(f"GET {url} returned HTTP {response.status}")
            raw = response.read(_MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.URLError as exc:
        raise LoaderVerificationError(f"failed to download {url}: {exc}") from exc

    _validate_header(raw, season, source=f"download from {url}")

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


def load_season(season: int, data_dir: Path = DEFAULT_DATA_DIR, *, force: bool = False) -> pd.DataFrame:
    """Download (if needed) and normalize one season to its completed-game collection."""
    path = download_season_csv(season, data_dir, force=force)
    return _read_completed_games(path, season)


def verify_completed_counts(
    actual: dict[int, int], expected: dict[int, int] = EXPECTED_COMPLETED_COUNTS
) -> None:
    """The loader's safety net in place of a test suite (Phase 1 Testing Decisions): fail hard, and
    report every mismatch at once, rather than warn or drop silently mis-parsed games."""
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
