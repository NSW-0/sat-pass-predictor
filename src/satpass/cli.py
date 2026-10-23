"""Command-line interface: `satpass --lat 51.48 --lon 0 --sat ISS`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import __version__
from .passes import Observer, Pass, find_passes, now_utc, tle_epoch
from .tle import TLE, TLEError, fetch_tle, load_tle_file, resolve_catalog_number

# SGP4 predictions get steadily worse as you move away from the TLE's epoch.
STALE_TLE_DAYS = 7


class _StderrHandler(logging.Handler):
    """Print log warnings (e.g. from the TLE cache) to whatever stderr is now."""

    def emit(self, record: logging.LogRecord) -> None:
        print(f"warning: {record.getMessage()}", file=sys.stderr)


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _setup_logging() -> None:
    logger = logging.getLogger("satpass")
    if not any(isinstance(handler, _StderrHandler) for handler in logger.handlers):
        logger.addHandler(_StderrHandler(level=logging.WARNING))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="satpass",
        description="Predict when a satellite passes over you and whether you can see it.",
    )
    parser.add_argument("--lat", type=float, required=True, help="your latitude in degrees (north is positive)")
    parser.add_argument("--lon", type=float, required=True, help="your longitude in degrees (east is positive)")
    parser.add_argument("--elevation", type=float, default=0.0, help="your height above sea level in metres")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--sat", default="ISS", help="NORAD number or name: ISS, HST, CSS (default: ISS)")
    source.add_argument("--tle-file", type=Path, help="read the orbit from a TLE file instead of downloading it")
    parser.add_argument("--days", type=float, default=3.0, help="how many days ahead to search (default: 3)")
    parser.add_argument("--min-alt", type=float, default=10.0, help="ignore the satellite below this altitude in degrees (default: 10)")
    parser.add_argument("--start", help="start of the search, e.g. 2026-11-01T18:00 (default: now)")
    parser.add_argument("--tz", default="UTC", help="time zone for the output, e.g. Europe/London (default: UTC)")
    parser.add_argument("--visible-only", action="store_true", help="only list passes you can see with the naked eye")
    parser.add_argument("--json", action="store_true", help="print the passes as JSON")
    parser.add_argument("--plot", type=Path, metavar="FILE.png", help="save a sky chart of the first listed pass")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _parse_start(value: str | None, tz: ZoneInfo) -> datetime:
    if value is None:
        return now_utc()
    # Python 3.10 does not accept a trailing "Z" for UTC, so translate it.
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    start = datetime.fromisoformat(value)
    return start if start.tzinfo else start.replace(tzinfo=tz)


def _format_duration(delta: timedelta) -> str:
    minutes, seconds = divmod(round(delta.total_seconds()), 60)
    return f"{minutes}m {seconds:02d}s"


def _visible_label(sky_pass: Pass) -> str:
    if not sky_pass.visible:
        return "no"
    minutes = max(1, round(sky_pass.visible_duration.total_seconds() / 60))
    return f"yes, ~{minutes} min"


def format_table(passes: list[Pass], tle: TLE, observer: Observer, tz: ZoneInfo, epoch_age_days: float) -> str:
    lines = [
        f"{tle.name} (NORAD {tle.catalog_number}) over {observer.describe()}",
        f"Times in {tz.key} · orbit data is {epoch_age_days:.1f} days old",
        "",
    ]
    if not passes:
        lines.append("No passes found in this window.")
        return "\n".join(lines)

    header = f"{'Date':<10}  {'Rise':>5} {'from':<4}  {'Highest':>7} {'at':>5}  {'Set':>5} {'to':<4}  {'Length':>7}  Visible"
    lines += [header, "-" * len(header)]
    for sky_pass in passes:
        rise = sky_pass.rise.time.astimezone(tz)
        peak = sky_pass.peak.time.astimezone(tz)
        set_ = sky_pass.set.time.astimezone(tz)
        lines.append(
            f"{rise:%Y-%m-%d}  {rise:%H:%M} {sky_pass.rise.direction:<4}  "
            f"{sky_pass.max_altitude:>6.0f}° {peak:%H:%M}  "
            f"{set_:%H:%M} {sky_pass.set.direction:<4}  "
            f"{_format_duration(sky_pass.duration):>7}  "
            f"{_visible_label(sky_pass)}"
        )
    return "\n".join(lines)


def to_json(passes: list[Pass], tle: TLE, tz: ZoneInfo) -> str:
    def point(p):
        return {
            "time": p.time.astimezone(tz).isoformat(timespec="seconds"),
            "altitude_deg": round(p.altitude, 1),
            "azimuth_deg": round(p.azimuth, 1),
            "direction": p.direction,
            "visible": p.visible,
        }

    return json.dumps(
        {
            "satellite": tle.name,
            "norad_id": tle.catalog_number,
            "passes": [
                {
                    "rise": point(p.rise),
                    "peak": point(p.peak),
                    "set": point(p.set),
                    "duration_s": round(p.duration.total_seconds()),
                    "visible": p.visible,
                    "visible_s": round(p.visible_duration.total_seconds()),
                }
                for p in passes
            ],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging()

    try:
        tz = ZoneInfo(args.tz)
    except (ZoneInfoNotFoundError, ValueError):
        hint = " (on Windows, run: pip install tzdata)" if sys.platform == "win32" else ""
        parser.error(f"unknown time zone '{args.tz}'{hint}")
    if not 0 < args.days <= 30:
        parser.error("--days must be between 0 and 30")
    try:
        observer = Observer(args.lat, args.lon, args.elevation)
        start = _parse_start(args.start, tz)
    except ValueError as error:
        parser.error(str(error))

    try:
        if args.tle_file:
            tle = load_tle_file(args.tle_file)
        else:
            tle = fetch_tle(resolve_catalog_number(args.sat))
    except TLEError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    epoch_age_days = abs((start - tle_epoch(tle)).total_seconds()) / 86400
    if epoch_age_days > STALE_TLE_DAYS:
        _warn(
            f"the orbit data is {epoch_age_days:.0f} days away from the search start, "
            "so times may be off by minutes."
        )

    passes = find_passes(tle, observer, start, days=args.days, min_altitude=args.min_alt)
    if args.visible_only:
        passes = [p for p in passes if p.visible]

    if args.json:
        print(to_json(passes, tle, tz))
    else:
        print(format_table(passes, tle, observer, tz, epoch_age_days))

    if args.plot:
        if not passes:
            _warn("no pass to plot.")
        else:
            from .plot import plot_pass

            try:
                plot_pass(passes[0], args.plot, f"{tle.name} pass", tz)
            except ImportError:
                print("error: plotting needs matplotlib: pip install 'satpass[plot]'", file=sys.stderr)
                return 1
            if not args.json:
                print(f"\nSky chart saved to {args.plot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
