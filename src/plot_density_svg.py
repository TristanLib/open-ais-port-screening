#!/usr/bin/env python3
"""Create a lightweight SVG AIS density plot without external plotting libraries."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def color_ramp(value: float) -> str:
    """Blue-to-red ramp for 0..1 density values."""
    value = max(0.0, min(1.0, value))
    if value < 0.5:
        t = value / 0.5
        r = int(24 + (82 - 24) * t)
        g = int(92 + (170 - 92) * t)
        b = int(180 + (220 - 180) * t)
    else:
        t = (value - 0.5) / 0.5
        r = int(82 + (220 - 82) * t)
        g = int(170 + (56 - 170) * t)
        b = int(220 + (48 - 220) * t)
    return f"rgb({r},{g},{b})"


def build_density(
    input_path: Path,
    bbox: tuple[float, float, float, float],
    cols: int,
    rows: int,
) -> tuple[list[list[int]], int, int]:
    min_lon, min_lat, max_lon, max_lat = bbox
    grid = [[0 for _ in range(cols)] for _ in range(rows)]
    total = 0
    used = 0

    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            total += 1
            lon = parse_float(row.get("longitude"))
            lat = parse_float(row.get("latitude"))
            if lon is None or lat is None:
                continue
            if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
                continue
            x = min(cols - 1, max(0, int((lon - min_lon) / (max_lon - min_lon) * cols)))
            y = min(rows - 1, max(0, int((max_lat - lat) / (max_lat - min_lat) * rows)))
            grid[y][x] += 1
            used += 1
    return grid, total, used


def write_svg(
    grid: list[list[int]],
    output_path: Path,
    bbox: tuple[float, float, float, float],
    title: str,
    total: int,
    used: int,
) -> None:
    plot_width = 960
    plot_height = 720
    margin_left = 80
    margin_top = 70
    margin_right = 40
    margin_bottom = 80
    width = plot_width + margin_left + margin_right
    height = plot_height + margin_top + margin_bottom
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    cell_w = plot_width / cols
    cell_h = plot_height / rows
    max_count = max((max(row) for row in grid), default=0)
    max_log = math.log1p(max_count) if max_count else 1.0

    rects: list[str] = []
    for y, row in enumerate(grid):
        for x, count in enumerate(row):
            if count == 0:
                continue
            value = math.log1p(count) / max_log
            rects.append(
                f'<rect x="{margin_left + x * cell_w:.2f}" y="{margin_top + y * cell_h:.2f}" '
                f'width="{cell_w + 0.35:.2f}" height="{cell_h + 0.35:.2f}" '
                f'fill="{color_ramp(value)}" opacity="{0.35 + 0.65 * value:.3f}" />'
            )

    min_lon, min_lat, max_lon, max_lat = bbox
    label_style = "font-family: Arial, Helvetica, sans-serif; fill: #1f2933;"
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="{width / 2:.1f}" y="34" text-anchor="middle" style="{label_style} font-size: 24px; font-weight: 700;">{title}</text>
  <text x="{width / 2:.1f}" y="58" text-anchor="middle" style="{label_style} font-size: 14px;">Records: {used:,}; MMSI-area crop from NOAA AIS 2025-05-01; log-scaled point density</text>
  <rect x="{margin_left}" y="{margin_top}" width="{plot_width}" height="{plot_height}" fill="#f5f7fa" stroke="#9aa5b1" stroke-width="1"/>
  {chr(10).join(rects)}
  <rect x="{margin_left}" y="{margin_top}" width="{plot_width}" height="{plot_height}" fill="none" stroke="#52606d" stroke-width="1.2"/>
  <text x="{margin_left}" y="{height - 34}" style="{label_style} font-size: 13px;">{min_lon:.2f}°</text>
  <text x="{margin_left + plot_width}" y="{height - 34}" text-anchor="end" style="{label_style} font-size: 13px;">{max_lon:.2f}°</text>
  <text x="{margin_left - 12}" y="{margin_top + 12}" text-anchor="end" style="{label_style} font-size: 13px;">{max_lat:.2f}°</text>
  <text x="{margin_left - 12}" y="{margin_top + plot_height}" text-anchor="end" style="{label_style} font-size: 13px;">{min_lat:.2f}°</text>
  <text x="{width / 2:.1f}" y="{height - 16}" text-anchor="middle" style="{label_style} font-size: 13px;">Longitude</text>
  <text transform="translate(22 {margin_top + plot_height / 2:.1f}) rotate(-90)" text-anchor="middle" style="{label_style} font-size: 13px;">Latitude</text>
  <text x="{margin_left}" y="{height - 56}" style="{label_style} font-size: 12px;">Total cropped CSV rows read: {total:,}; max grid cell count: {max_count:,}</text>
</svg>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create AIS density SVG.")
    parser.add_argument("--input", type=Path, required=True, help="Input cropped AIS CSV.")
    parser.add_argument("--output", type=Path, required=True, help="Output SVG path.")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        required=True,
        help="Bounding box in EPSG:4326.",
    )
    parser.add_argument("--cols", type=int, default=220, help="Grid columns.")
    parser.add_argument("--rows", type=int, default=170, help="Grid rows.")
    parser.add_argument("--title", default="San Francisco Bay AIS Traffic Density", help="SVG title.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    grid, total, used = build_density(args.input, tuple(args.bbox), args.cols, args.rows)
    write_svg(grid, args.output, tuple(args.bbox), args.title, total, used)
    print(f"wrote {args.output} with {used:,} points")
    return 0


if __name__ == "__main__":
    sys.exit(main())
