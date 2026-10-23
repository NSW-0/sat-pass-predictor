from datetime import datetime, timedelta, timezone

import pytest

from satpass.passes import Observer, compass_point, find_passes, tle_epoch

TOKYO = Observer(35.68, 139.69)
START = datetime(2026, 5, 26, tzinfo=timezone.utc)


@pytest.fixture
def tokyo_passes(iss_tle):
    return find_passes(iss_tle, TOKYO, START, days=2, min_altitude=10)


@pytest.mark.parametrize(
    "azimuth, expected",
    [(0, "N"), (359, "N"), (11.2, "N"), (11.3, "NNE"), (90, "E"), (180, "S"), (247.5, "WSW"), (360, "N")],
)
def test_compass_point(azimuth, expected):
    assert compass_point(azimuth) == expected


def test_observer_rejects_impossible_coordinates():
    with pytest.raises(ValueError):
        Observer(91, 0)
    with pytest.raises(ValueError):
        Observer(0, 181)


def test_start_time_must_have_a_timezone(iss_tle):
    with pytest.raises(ValueError, match="timezone"):
        find_passes(iss_tle, TOKYO, datetime(2026, 5, 26))


def test_tle_epoch(iss_tle):
    epoch = tle_epoch(iss_tle)
    assert epoch.date().isoformat() == "2026-05-28"
    assert (epoch.hour, epoch.minute) == (3, 8)


def test_passes_are_physically_sensible(tokyo_passes):
    assert len(tokyo_passes) >= 5
    for sky_pass in tokyo_passes:
        assert sky_pass.rise.time < sky_pass.peak.time < sky_pass.set.time
        # Rise and set are where the satellite crosses the 10 degree limit.
        assert sky_pass.rise.altitude == pytest.approx(10, abs=0.1)
        assert sky_pass.set.altitude == pytest.approx(10, abs=0.1)
        # The peak is the highest point of the whole track.
        assert sky_pass.max_altitude >= max(p.altitude for p in sky_pass.track) - 0.01
        assert 10 <= sky_pass.max_altitude <= 90
        # The ISS crosses the sky in under 12 minutes.
        assert timedelta(0) < sky_pass.duration < timedelta(minutes=12)


def test_passes_are_in_time_order_and_inside_the_window(tokyo_passes):
    end = START + timedelta(days=2)
    rises = [p.rise.time for p in tokyo_passes]
    assert rises == sorted(rises)
    assert all(START <= p.rise.time and p.set.time <= end for p in tokyo_passes)


def test_daytime_passes_are_not_visible(tokyo_passes):
    # 03:00 UTC is around noon in Tokyo: the sky is far too bright.
    midday = [p for p in tokyo_passes if 2 <= p.rise.time.hour <= 6]
    assert midday
    assert not any(p.visible for p in midday)


def test_evening_pass_fades_into_earths_shadow(tokyo_passes):
    """Regression check for a known pass: 26 May 2026 at 20:25 Tokyo time."""
    evening = [p for p in tokyo_passes if p.rise.time.date().isoformat() == "2026-05-26" and p.rise.time.hour == 11]
    assert len(evening) == 1
    sky_pass = evening[0]
    assert sky_pass.rise.direction == "NW"
    assert sky_pass.max_altitude == pytest.approx(68, abs=1)
    assert sky_pass.visible
    # Visible as it rises in the twilight, then it enters Earth's shadow.
    assert sky_pass.track[0].visible
    assert not sky_pass.track[-1].sunlit
    assert timedelta(minutes=2) < sky_pass.visible_duration < sky_pass.duration


def test_visible_means_sunlit_satellite_and_dark_sky(tokyo_passes):
    for sky_pass in tokyo_passes:
        for point in sky_pass.track:
            assert point.visible == (point.sunlit and point.sky_dark)


def test_higher_minimum_altitude_finds_fewer_passes(iss_tle, tokyo_passes):
    high_only = find_passes(iss_tle, TOKYO, START, days=2, min_altitude=45)
    assert 0 < len(high_only) < len(tokyo_passes)
    assert all(p.max_altitude >= 45 for p in high_only)


def test_sunlight_agrees_with_a_simple_shadow_model(iss_tle, tokyo_passes):
    """Cross-check Skyfield's sunlight test with a cylindrical Earth-shadow model.

    The satellite is in shadow when it is on the night side of Earth and
    closer to the Earth-Sun line than Earth's radius.
    """
    import numpy as np

    from satpass.passes import _timescale, load_ephemeris, make_satellite

    ts, ephemeris, satellite = _timescale(), load_ephemeris(), make_satellite(iss_tle)
    sky_pass = next(p for p in tokyo_passes if p.visible)
    for point in sky_pass.track:
        t = ts.from_datetime(point.time)
        position = satellite.at(t).position.km
        sun = (ephemeris["sun"] - ephemeris["earth"]).at(t).position.km
        sun_direction = sun / np.linalg.norm(sun)
        along = position @ sun_direction
        distance_from_axis = np.linalg.norm(position - along * sun_direction)
        in_shadow = along < 0 and distance_from_axis < 6378.137
        assert point.sunlit == (not in_shadow)
