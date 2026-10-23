"""Draw a pass as a sky chart: the horizon is the outer ring, overhead is the centre."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from .passes import Pass

SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#d9d8d4"
VISIBLE = "#2a78d6"  # the part of the pass you can see
NOT_VISIBLE = "#9a9994"  # in Earth's shadow, or the sky is too bright


def plot_pass(sky_pass: Pass, path: Path, title: str, tz: ZoneInfo) -> Path:
    """Save a polar sky chart of one pass to `path` (PNG) and return the path."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    figure = plt.figure(figsize=(6.4, 7.0), dpi=150, facecolor=SURFACE)
    axes = figure.add_axes([0.08, 0.1, 0.84, 0.72], projection="polar", facecolor=SURFACE)

    # North at the top, east to the right, like a compass.
    axes.set_theta_zero_location("N")
    axes.set_theta_direction(-1)
    axes.set_rlim(0, 90)
    axes.set_rticks([30, 60, 90])
    axes.set_yticklabels(["60°", "30°", ""], color=TEXT_SECONDARY, fontsize=8)
    axes.set_rlabel_position(202.5)
    axes.set_xticks(np.radians(np.arange(0, 360, 45)))
    axes.set_xticklabels(["N", "NE", "E", "SE", "S", "SW", "W", "NW"], color=TEXT_PRIMARY, fontsize=10)
    axes.grid(color=GRID, linewidth=0.8)
    axes.spines["polar"].set_color(GRID)

    theta = np.radians([point.azimuth for point in sky_pass.track])
    radius = np.array([90 - point.altitude for point in sky_pass.track])
    visible = np.array([point.visible for point in sky_pass.track])

    # Draw the whole track faintly, then the visible part on top.
    axes.plot(theta, radius, color=NOT_VISIBLE, linewidth=2, linestyle=(0, (3, 2)), label="Not visible")
    visible_track = np.where(visible, radius, np.nan)
    axes.plot(theta, visible_track, color=VISIBLE, linewidth=2.5, label="Visible to the naked eye")

    def local(point):
        return point.time.astimezone(tz).strftime("%H:%M")

    for point, name, offset in (
        (sky_pass.rise, "Rise", (8, -14)),
        (sky_pass.peak, "Highest", (10, 6)),
        (sky_pass.set, "Set", (8, 6)),
    ):
        colour = VISIBLE if point.visible else NOT_VISIBLE
        axes.plot(
            np.radians(point.azimuth), 90 - point.altitude, "o",
            markersize=8, color=colour, markeredgecolor=SURFACE, markeredgewidth=2, zorder=5,
        )
        axes.annotate(
            f"{name} {local(point)}\n{point.altitude:.0f}° {point.direction}",
            xy=(np.radians(point.azimuth), 90 - point.altitude),
            xytext=offset, textcoords="offset points",
            fontsize=8.5, color=TEXT_PRIMARY, zorder=6,
        )

    date = sky_pass.rise.time.astimezone(tz).strftime("%d %b %Y")
    figure.text(0.06, 0.95, title, fontsize=14, fontweight="bold", color=TEXT_PRIMARY)
    figure.text(
        0.06, 0.915,
        f"{date} · times in {tz.key} · centre = overhead, outer ring = horizon",
        fontsize=9.5, color=TEXT_SECONDARY,
    )
    legend = figure.legend(
        loc="lower center", ncol=2, frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.015)
    )
    for text in legend.get_texts():
        text.set_color(TEXT_PRIMARY)

    path = Path(path)
    figure.savefig(path, facecolor=SURFACE)
    plt.close(figure)
    return path
