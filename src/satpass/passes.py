"""Find a satellite's passes over an observer and decide which are visible.

A pass is visible to the naked eye when two things are true at the same time:
the satellite is in sunlight (so it reflects light down to you), and your sky
is dark (the Sun is at least 6 degrees below your horizon).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import numpy as np
from skyfield.api import EarthSatellite, Loader, load, wgs84

from .tle import TLE, default_cache_dir

COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)

# The Sun must be this far below the horizon for the sky to be dark enough.
DARK_SKY_SUN_ALTITUDE = -6.0

# How many positions are computed along each pass to check visibility and
# draw its track across the sky.
TRACK_SAMPLES = 60


def compass_point(azimuth_degrees: float) -> str:
    """Convert an azimuth (0 = north, 90 = east) to a 16-point compass direction."""
    index = int((azimuth_degrees % 360) / 22.5 + 0.5) % 16
    return COMPASS_POINTS[index]


@dataclass(frozen=True)
class Observer:
    latitude: float
    longitude: float
    elevation_m: float = 0.0

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"Latitude must be between -90 and 90, got {self.latitude}.")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"Longitude must be between -180 and 180, got {self.longitude}.")

    def topos(self):
        return wgs84.latlon(self.latitude, self.longitude, elevation_m=self.elevation_m)

    def describe(self) -> str:
        ns = "N" if self.latitude >= 0 else "S"
        ew = "E" if self.longitude >= 0 else "W"
        return f"{abs(self.latitude):.4f}°{ns}, {abs(self.longitude):.4f}°{ew}"


@dataclass(frozen=True)
class SkyPoint:
    """Where the satellite is in your sky at one moment."""

    time: datetime  # always UTC
    altitude: float  # degrees above the horizon
    azimuth: float  # degrees clockwise from north
    sunlit: bool  # the satellite is in sunlight
    sky_dark: bool  # the Sun is far enough below your horizon

    @property
    def visible(self) -> bool:
        return self.sunlit and self.sky_dark

    @property
    def direction(self) -> str:
        return compass_point(self.azimuth)


@dataclass(frozen=True)
class Pass:
    rise: SkyPoint
    peak: SkyPoint
    set: SkyPoint
    track: tuple[SkyPoint, ...]

    @property
    def duration(self) -> timedelta:
        return self.set.time - self.rise.time

    @property
    def max_altitude(self) -> float:
        return self.peak.altitude

    @property
    def visible(self) -> bool:
        return any(point.visible for point in self.track)

    @property
    def visible_duration(self) -> timedelta:
        """Roughly how long you can see it (from the sampled track)."""
        fraction = sum(point.visible for point in self.track) / len(self.track)
        return self.duration * fraction


@lru_cache(maxsize=1)
def _timescale():
    # builtin=True uses the leap-second and Earth-rotation tables shipped
    # with Skyfield, so no download is needed.
    return load.timescale(builtin=True)


@lru_cache(maxsize=1)
def load_ephemeris():
    """Load the JPL DE421 ephemeris, used to locate the Sun.

    The skyfield-data package bundles it, so normally nothing is downloaded.
    """
    try:
        with warnings.catch_warnings():
            # skyfield-data warns about an Earth-orientation file we don't use.
            warnings.simplefilter("ignore", RuntimeWarning)
            import skyfield_data

            return Loader(skyfield_data.get_skyfield_data_path(), verbose=False)("de421.bsp")
    except (ImportError, OSError):
        return Loader(default_cache_dir(), verbose=False)("de421.bsp")


def make_satellite(tle: TLE) -> EarthSatellite:
    return EarthSatellite(tle.line1, tle.line2, tle.name, _timescale())


def tle_epoch(tle: TLE) -> datetime:
    """The moment the TLE's orbit was measured; predictions drift away from it."""
    return make_satellite(tle).epoch.utc_datetime()


def _sky_points(satellite, topos, ephemeris, times) -> list[SkyPoint]:
    altitude, azimuth, _ = (satellite - topos).at(times).altaz()
    sunlit = satellite.at(times).is_sunlit(ephemeris)
    observer = ephemeris["earth"] + topos
    sun_altitude, _, _ = observer.at(times).observe(ephemeris["sun"]).apparent().altaz()
    sky_dark = sun_altitude.degrees <= DARK_SKY_SUN_ALTITUDE
    return [
        SkyPoint(
            time=moment,
            altitude=float(alt),
            azimuth=float(az),
            sunlit=bool(lit),
            sky_dark=bool(dark),
        )
        for moment, alt, az, lit, dark in zip(
            times.utc_datetime(), altitude.degrees, azimuth.degrees, sunlit, sky_dark
        )
    ]


def find_passes(
    tle: TLE,
    observer: Observer,
    start: datetime,
    days: float = 3.0,
    min_altitude: float = 10.0,
) -> list[Pass]:
    """Return every complete pass above `min_altitude` in the time window.

    A pass already under way at `start` is skipped, because its rise time is
    outside the window.
    """
    if start.tzinfo is None:
        raise ValueError("start must be timezone-aware, e.g. datetime.now(timezone.utc)")
    if days <= 0:
        raise ValueError("days must be positive")

    ts = _timescale()
    ephemeris = load_ephemeris()
    satellite = make_satellite(tle)
    topos = observer.topos()

    t0 = ts.from_datetime(start)
    t1 = ts.from_datetime(start + timedelta(days=days))
    # Skyfield searches for the moments the satellite crosses min_altitude
    # (rise and set) and the moments it reaches its highest point.
    times, events = satellite.find_events(topos, t0, t1, altitude_degrees=min_altitude)

    passes: list[Pass] = []
    rise = None
    peaks: list[float] = []
    for moment, event in zip(times, events):
        if event == 0:  # rises above min_altitude
            rise, peaks = moment, []
        elif event == 1 and rise is not None:  # highest point
            peaks.append(moment.tt)
        elif event == 2 and rise is not None:  # sets below min_altitude
            passes.append(_build_pass(satellite, topos, ephemeris, rise, moment, peaks))
            rise, peaks = None, []
    return passes


def _build_pass(satellite, topos, ephemeris, rise, set_, peaks) -> Pass:
    ts = _timescale()
    if not peaks:
        peaks = [(rise.tt + set_.tt) / 2]
    track_tt = np.linspace(rise.tt, set_.tt, TRACK_SAMPLES)
    all_tt = np.concatenate([[rise.tt, set_.tt], peaks, track_tt])
    points = _sky_points(satellite, topos, ephemeris, ts.tt_jd(all_tt))

    rise_point, set_point = points[0], points[1]
    peak_candidates = points[2 : 2 + len(peaks)]
    peak_point = max(peak_candidates, key=lambda point: point.altitude)
    track = tuple(points[2 + len(peaks) :])
    return Pass(rise=rise_point, peak=peak_point, set=set_point, track=track)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
