import json

import pytest

from satpass.cli import main

from .conftest import ISS_TLE_FILE

BASE_ARGS = [
    "--lat", "35.68", "--lon", "139.69",
    "--tle-file", str(ISS_TLE_FILE),
    "--start", "2026-05-26T00:00+00:00",
    "--days", "2",
    "--tz", "Asia/Tokyo",
]


def test_table_output(capsys):
    assert main(BASE_ARGS) == 0
    out = capsys.readouterr().out
    assert "ISS (ZARYA) (NORAD 25544) over 35.6800°N, 139.6900°E" in out
    assert "Times in Asia/Tokyo" in out
    assert "2026-05-26  20:25 NW" in out
    assert "yes, ~" in out


def test_visible_only(capsys):
    assert main(BASE_ARGS + ["--visible-only"]) == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if line.startswith("2026-")]
    assert rows
    assert all("yes" in row for row in rows)


def test_json_output(capsys):
    assert main(BASE_ARGS + ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["norad_id"] == 25544
    first = data["passes"][0]
    assert set(first) >= {"rise", "peak", "set", "duration_s", "visible", "visible_s"}
    assert first["rise"]["time"].endswith("+09:00")


def test_plot_is_saved(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    chart = tmp_path / "pass.png"
    assert main(BASE_ARGS + ["--visible-only", "--plot", str(chart)]) == 0
    assert chart.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_bad_latitude_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--lat", "95", "--lon", "0", "--tle-file", str(ISS_TLE_FILE)])
    assert exit_info.value.code == 2
    assert "Latitude" in capsys.readouterr().err


def test_unknown_time_zone_is_a_usage_error(capsys):
    with pytest.raises(SystemExit):
        main(BASE_ARGS[:-1] + ["Mars/Olympus_Mons"])
    assert "unknown time zone" in capsys.readouterr().err


def test_missing_tle_file_is_reported(tmp_path, capsys):
    code = main(["--lat", "0", "--lon", "0", "--tle-file", str(tmp_path / "missing.tle")])
    assert code == 1
    assert "error:" in capsys.readouterr().err


def test_old_orbit_data_triggers_a_warning(capsys):
    args = BASE_ARGS.copy()
    args[args.index("2026-05-26T00:00+00:00")] = "2026-07-01T00:00+00:00"
    assert main(args) == 0
    assert "days away" in capsys.readouterr().err
