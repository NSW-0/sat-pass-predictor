import os
import time
import urllib.error

import pytest

from satpass.tle import (
    TLEError,
    checksum,
    fetch_tle,
    parse_tle,
    resolve_catalog_number,
)

LINE1 = "1 25544U 98067A   26148.13113954  .00011691  00000+0  21663-3 0  9996"
LINE2 = "2 25544  51.6335  39.3887 0007375 106.1024 254.0777 15.49434162568649"
THREE_LINE = f"ISS (ZARYA)\n{LINE1}\n{LINE2}\n"


def test_checksum_matches_last_digit():
    assert checksum(LINE1) == 6
    assert checksum(LINE2) == 9


def test_parse_three_line_tle():
    tle = parse_tle(THREE_LINE)
    assert tle.name == "ISS (ZARYA)"
    assert tle.catalog_number == 25544


def test_parse_two_line_tle_gets_a_default_name():
    tle = parse_tle(f"{LINE1}\n{LINE2}")
    assert tle.name == "NORAD 25544"


def test_parse_strips_zero_prefix_from_name():
    assert parse_tle(f"0 ISS (ZARYA)\n{LINE1}\n{LINE2}").name == "ISS (ZARYA)"


def test_corrupted_line_fails_checksum():
    broken = LINE2[:20] + "9" + LINE2[21:]  # change one digit
    with pytest.raises(TLEError, match="checksum"):
        parse_tle(f"{LINE1}\n{broken}")


def test_lines_from_different_satellites_are_rejected():
    other = "2 25545" + LINE2[7:68]
    other += str(checksum(other))
    with pytest.raises(TLEError, match="different satellites"):
        parse_tle(f"{LINE1}\n{other}")


def test_text_without_a_tle_is_rejected():
    with pytest.raises(TLEError):
        parse_tle("No GP data found")


@pytest.mark.parametrize("name, expected", [("ISS", 25544), ("hubble", 20580), (" 25544 ", 25544)])
def test_resolve_catalog_number(name, expected):
    assert resolve_catalog_number(name) == expected


def test_unknown_satellite_name():
    with pytest.raises(TLEError, match="Unknown satellite"):
        resolve_catalog_number("not-a-satellite")


def test_fresh_cache_is_used_without_downloading(tmp_path):
    (tmp_path / "25544.tle").write_text(THREE_LINE)

    def must_not_download(url):
        raise AssertionError("should have used the cache")

    tle = fetch_tle(25544, cache_dir=tmp_path, download=must_not_download)
    assert tle.catalog_number == 25544


def test_old_cache_is_refreshed_and_saved(tmp_path):
    cache_file = tmp_path / "25544.tle"
    cache_file.write_text(THREE_LINE)
    day_ago = time.time() - 24 * 3600
    os.utime(cache_file, (day_ago, day_ago))
    calls = []

    def fake_download(url):
        calls.append(url)
        return THREE_LINE

    fetch_tle(25544, cache_dir=tmp_path, download=fake_download)
    assert calls == ["https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"]
    assert cache_file.stat().st_mtime > day_ago


def test_offline_falls_back_to_old_cache(tmp_path, caplog):
    cache_file = tmp_path / "25544.tle"
    cache_file.write_text(THREE_LINE)
    os.utime(cache_file, (0, 0))

    def offline(url):
        raise urllib.error.URLError("no network")

    tle = fetch_tle(25544, cache_dir=tmp_path, download=offline)
    assert tle.catalog_number == 25544
    assert "cached copy" in caplog.text


def test_offline_without_cache_raises(tmp_path):
    def offline(url):
        raise urllib.error.URLError("no network")

    with pytest.raises(TLEError, match="Could not get a TLE"):
        fetch_tle(25544, cache_dir=tmp_path, download=offline)
