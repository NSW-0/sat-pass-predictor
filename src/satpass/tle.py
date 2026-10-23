"""Load two-line element sets (TLEs) from CelesTrak, a local cache, or a file.

A TLE is the standard text format for a satellite's orbit. It is two lines of
69 characters, often with a name line above them, for example:

    ISS (ZARYA)
    1 25544U 98067A   26148.13113954  .00011691  00000+0  21663-3 0  9996
    2 25544  51.6335  39.3887 0007375 106.1024 254.0777 15.49434162568649
"""

from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__

log = logging.getLogger(__name__)

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={catnr}&FORMAT=TLE"

# Short names for satellites people often want to watch.
ALIASES = {
    "ISS": 25544,
    "HST": 20580,
    "HUBBLE": 20580,
    "CSS": 48274,
    "TIANGONG": 48274,
}

# CelesTrak updates orbits a few times a day and asks users not to download
# the same data more often than that, so cached TLEs are reused for 12 hours.
DEFAULT_MAX_AGE_HOURS = 12.0


class TLEError(Exception):
    """Raised when a TLE cannot be found, downloaded or parsed."""


@dataclass(frozen=True)
class TLE:
    name: str
    line1: str
    line2: str

    @property
    def catalog_number(self) -> int:
        return int(self.line1[2:7])

    def to_text(self) -> str:
        return f"{self.name}\n{self.line1}\n{self.line2}\n"


def checksum(line: str) -> int:
    """Return the modulo-10 checksum of a TLE line.

    Every digit counts as its value, a minus sign counts as 1, and everything
    else counts as 0. The result must match the last character of the line.
    """
    total = 0
    for char in line[:68]:
        if char.isdigit():
            total += int(char)
        elif char == "-":
            total += 1
    return total % 10


def _validate(line: str, number: int) -> None:
    if len(line) != 69:
        raise TLEError(f"Line {number} should be 69 characters long, got {len(line)}.")
    if not line[68].isdigit() or checksum(line) != int(line[68]):
        raise TLEError(f"Line {number} failed its checksum; the TLE may be corrupted.")


def parse_tle(text: str) -> TLE:
    """Parse the first TLE found in `text` (2-line or named 3-line format)."""
    lines = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if not (line.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 ")):
            continue
        line1, line2 = line, lines[i + 1]
        _validate(line1, 1)
        _validate(line2, 2)
        if line1[2:7] != line2[2:7]:
            raise TLEError("Line 1 and line 2 describe different satellites.")
        previous = lines[i - 1].strip() if i > 0 else ""
        if previous and not previous.startswith(("1 ", "2 ")):
            name = previous[2:] if previous.startswith("0 ") else previous
        else:
            name = f"NORAD {int(line1[2:7])}"
        return TLE(name=name, line1=line1, line2=line2)
    raise TLEError("No two-line element set found in the input.")


def resolve_catalog_number(satellite: str) -> int:
    """Turn 'ISS', 'hubble' or '25544' into a NORAD catalog number."""
    key = satellite.strip().upper()
    if key in ALIASES:
        return ALIASES[key]
    if key.isdigit():
        return int(key)
    known = ", ".join(sorted(ALIASES))
    raise TLEError(f"Unknown satellite '{satellite}'. Use a NORAD number or one of: {known}.")


def default_cache_dir() -> Path:
    if "SATPASS_CACHE_DIR" in os.environ:
        return Path(os.environ["SATPASS_CACHE_DIR"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "satpass"
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "satpass"


def _download(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": f"satpass/{__version__}"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_tle(
    catalog_number: int,
    *,
    cache_dir: Path | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    download: Callable[[str], str] = _download,
) -> TLE:
    """Return a TLE from the cache if it is fresh, otherwise from CelesTrak.

    If the download fails but an older cached copy exists, the old copy is
    used and a warning is logged, so the tool still works offline.
    """
    cache_dir = cache_dir or default_cache_dir()
    cache_file = cache_dir / f"{catalog_number}.tle"

    if cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < max_age_hours:
            return parse_tle(cache_file.read_text())

    try:
        text = download(CELESTRAK_URL.format(catnr=catalog_number))
        tle = parse_tle(text)
    except (urllib.error.URLError, OSError, TLEError) as error:
        if cache_file.exists():
            log.warning("Could not download a fresh TLE (%s); using the cached copy.", error)
            return parse_tle(cache_file.read_text())
        raise TLEError(f"Could not get a TLE for satellite {catalog_number}: {error}") from error

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(tle.to_text())
    return tle


def load_tle_file(path: Path) -> TLE:
    try:
        return parse_tle(Path(path).read_text())
    except OSError as error:
        raise TLEError(f"Could not read {path}: {error}") from error
