#!/usr/bin/env python3
"""Generate SVG audit figures for the AIS project without plotting dependencies."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import math
import sys
from collections import Counter
from pathlib import Path


VESSEL_TYPE_LABELS = {
    "30": "Fishing",
    "31": "Towing",
    "32": "Towing large",
    "33": "Dredging",
    "36": "Sailing",
    "37": "Pleasure",
    "40": "High-speed craft",
    "50": "Pilot",
    "51": "SAR",
    "52": "Tug",
    "54": "Anti-pollution",
    "55": "Law enforcement",
    "57": "Local vessel",
    "60": "Passenger",
    "69": "Passenger other",
    "70": "Cargo",
    "71": "Cargo haz A",
    "80": "Tanker",
    "90": "Other",
    "99": "Other",
}


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def svg_header(width: int, height: int, title: str, subtitle: str = "") -> list[str]:
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2:.1f}" y="34" text-anchor="middle" class="title">{html.escape(title)}</text>',
    ]
    if subtitle:
        lines.append(f'<text x="{width / 2:.1f}" y="56" text-anchor="middle" class="subtitle">{html.escape(subtitle)}</text>')
    return lines


def svg_defs() -> str:
    return """<style>
      text { font-family: Arial, Helvetica, sans-serif; fill: #1f2933; }
      .title { font-size: 22px; font-weight: 700; }
      .subtitle { font-size: 13px; fill: #52606d; }
      .axis { stroke: #9aa5b1; stroke-width: 1; }
      .grid { stroke: #e4e7eb; stroke-width: 1; }
      .label { font-size: 12px; fill: #3e4c59; }
      .small { font-size: 11px; fill: #52606d; }
      .bar-label { font-size: 11px; fill: #1f2933; }
    </style>"""


def nice_number(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    if value >= 10:
        return f"{value:.0f}"
    return f"{value:.2f}"


def write_grouped_bar_svg(
    output: Path,
    title: str,
    subtitle: str,
    labels: list[str],
    series: list[tuple[str, list[float], str]],
    y_label: str,
) -> None:
    width, height = 1080, 660
    margin = {"left": 90, "right": 40, "top": 90, "bottom": 90}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]
    max_value = max(max(values) for _, values, _ in series)
    max_value *= 1.12
    lines = svg_header(width, height, title, subtitle)
    lines.append(svg_defs())

    for i in range(6):
        y_value = max_value * i / 5
        y = margin["top"] + plot_h - (y_value / max_value) * plot_h
        lines.append(f'<line x1="{margin["left"]}" y1="{y:.1f}" x2="{width - margin["right"]}" y2="{y:.1f}" class="grid"/>')
        lines.append(f'<text x="{margin["left"] - 10}" y="{y + 4:.1f}" text-anchor="end" class="small">{nice_number(y_value)}</text>')

    group_w = plot_w / len(labels)
    bar_w = min(32, group_w / (len(series) + 1))
    for i, label in enumerate(labels):
        group_x = margin["left"] + i * group_w
        lines.append(f'<text x="{group_x + group_w / 2:.1f}" y="{height - 52}" text-anchor="middle" class="label">{html.escape(label)}</text>')
        for s_idx, (_, values, color) in enumerate(series):
            value = values[i]
            bar_h = (value / max_value) * plot_h
            x = group_x + group_w / 2 - (len(series) * bar_w) / 2 + s_idx * bar_w
            y = margin["top"] + plot_h - bar_h
            lines.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w - 3:.1f}" height="{bar_h:.1f}" fill="{color}" rx="2"/>')

    legend_x = margin["left"]
    for name, _, color in series:
        lines.append(f'<rect x="{legend_x}" y="{height - 28}" width="14" height="14" fill="{color}" rx="2"/>')
        lines.append(f'<text x="{legend_x + 20}" y="{height - 17}" class="label">{html.escape(name)}</text>')
        legend_x += 170

    lines.append(f'<line x1="{margin["left"]}" y1="{margin["top"] + plot_h}" x2="{width - margin["right"]}" y2="{margin["top"] + plot_h}" class="axis"/>')
    lines.append(f'<line x1="{margin["left"]}" y1="{margin["top"]}" x2="{margin["left"]}" y2="{margin["top"] + plot_h}" class="axis"/>')
    lines.append(f'<text transform="translate(24 {margin["top"] + plot_h / 2:.1f}) rotate(-90)" text-anchor="middle" class="label">{html.escape(y_label)}</text>')
    lines.append("</svg>")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_horizontal_bar_svg(
    output: Path,
    title: str,
    subtitle: str,
    items: list[tuple[str, float, str | None]],
    x_label: str,
    color: str = "#2f80ed",
) -> None:
    width = 1080
    row_h = 34
    height = max(420, 130 + row_h * len(items))
    margin_left, margin_right, margin_top, margin_bottom = 220, 80, 90, 60
    plot_w = width - margin_left - margin_right
    max_value = max((value for _, value, _ in items), default=1) * 1.08
    lines = svg_header(width, height, title, subtitle)
    lines.append(svg_defs())
    for idx, (label, value, note) in enumerate(items):
        y = margin_top + idx * row_h
        bar_w = (value / max_value) * plot_w
        lines.append(f'<text x="{margin_left - 12}" y="{y + 18}" text-anchor="end" class="label">{html.escape(label)}</text>')
        lines.append(f'<rect x="{margin_left}" y="{y + 3}" width="{bar_w:.1f}" height="20" fill="{color}" rx="2"/>')
        value_text = nice_number(value) if value > 1 else f"{value:.4f}"
        if note:
            value_text = f"{value_text} {note}"
        lines.append(f'<text x="{margin_left + bar_w + 8:.1f}" y="{y + 18}" class="bar-label">{html.escape(value_text)}</text>')
    lines.append(f'<line x1="{margin_left}" y1="{height - margin_bottom}" x2="{width - margin_right}" y2="{height - margin_bottom}" class="axis"/>')
    lines.append(f'<text x="{margin_left + plot_w / 2:.1f}" y="{height - 20}" text-anchor="middle" class="label">{html.escape(x_label)}</text>')
    lines.append("</svg>")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_pipeline_daily(stability_json: Path) -> list[dict[str, object]]:
    data = json.loads(stability_json.read_text(encoding="utf-8"))
    return data["daily"]


def load_clean_audits(start: dt.date, end: dt.date, tables_dir: Path) -> list[dict[str, object]]:
    audits = []
    for day in iter_dates(start, end):
        path = tables_dir / f"sf_bay_ais_{day.isoformat()}_clean_audit.json"
        audits.append(json.loads(path.read_text(encoding="utf-8")))
    return audits


def aggregate_missing(audits: list[dict[str, object]]) -> list[tuple[str, float, str | None]]:
    fields = ["cog", "heading", "status", "draft", "width", "length", "vessel_type", "sog"]
    items = []
    for field in fields:
        total_missing = 0
        total_rows = 0
        for audit in audits:
            total_rows += int(audit["rows"])
            missing = audit["missing"]
            assert isinstance(missing, dict)
            total_missing += int(missing[field]["count"])  # type: ignore[index]
        items.append((field, total_missing / total_rows * 100 if total_rows else 0.0, "%"))
    return items


def aggregate_sog_hist(start: dt.date, end: dt.date, processed_dir: Path) -> list[tuple[str, float, str | None]]:
    bins = [(0, 0.5), (0.5, 1), (1, 2), (2, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 60.1)]
    counts = [0 for _ in bins]
    for day in iter_dates(start, end):
        path = processed_dir / f"sf_bay_ais_{day.isoformat()}_clean.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                sog = parse_float(row.get("sog"))
                if sog is None:
                    continue
                for idx, (low, high) in enumerate(bins):
                    if low <= sog < high:
                        counts[idx] += 1
                        break
    labels = ["0-0.5", "0.5-1", "1-2", "2-5", "5-10", "10-15", "15-20", "20-30", "30-60"]
    return [(label, count, "rows") for label, count in zip(labels, counts)]


def aggregate_track_length_hist(start: dt.date, end: dt.date, processed_dir: Path) -> list[tuple[str, float, str | None]]:
    bins = [(1, 2), (2, 3), (3, 6), (6, 11), (11, 20), (20, 50), (50, 100), (100, 200), (200, 10**9)]
    labels = ["1", "2", "3-5", "6-10", "11-19", "20-49", "50-99", "100-199", "200+"]
    counts = [0 for _ in bins]
    for day in iter_dates(start, end):
        track_counts: Counter[str] = Counter()
        path = processed_dir / f"sf_bay_ais_{day.isoformat()}_features.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                track_counts[row["track_id"]] += 1
        for length in track_counts.values():
            for idx, (low, high) in enumerate(bins):
                if low <= length < high:
                    counts[idx] += 1
                    break
    return [(label, count, "tracks") for label, count in zip(labels, counts)]


def aggregate_vessel_types(start: dt.date, end: dt.date, processed_dir: Path) -> list[tuple[str, float, str | None]]:
    counts: Counter[str] = Counter()
    for day in iter_dates(start, end):
        path = processed_dir / f"sf_bay_ais_{day.isoformat()}_clean.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                key = row.get("vessel_type") or "missing"
                counts[key] += 1
    items = []
    for code, count in counts.most_common(12):
        label = f"{code} {VESSEL_TYPE_LABELS.get(code, '')}".strip()
        items.append((label, count, "rows"))
    return items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate SVG audit figures.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--tables-dir", type=Path, default=Path("outputs/tables"), help="Tables directory.")
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"), help="Processed directory.")
    parser.add_argument("--figures-dir", type=Path, default=Path("outputs/figures"), help="Figures directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.figures_dir.mkdir(parents=True, exist_ok=True)
    range_label = f"{args.start.isoformat()}_to_{args.end.isoformat()}"
    stability_json = args.tables_dir / f"sf_bay_ais_{range_label}_stability.json"
    daily = load_pipeline_daily(stability_json)
    labels = [str(item["date"])[5:] for item in daily]

    write_grouped_bar_svg(
        args.figures_dir / f"audit_daily_volume_{range_label}.svg",
        "Daily AIS Sample Volume",
        "San Francisco Bay crop, NOAA AIS 2025",
        labels,
        [
            ("Clean rows", [float(item["clean_rows"]) for item in daily], "#2f80ed"),
            ("MMSI x 300", [float(item["unique_mmsi"]) * 300 for item in daily], "#27ae60"),
        ],
        "Rows",
    )

    audits = load_clean_audits(args.start, args.end, args.tables_dir)
    write_horizontal_bar_svg(
        args.figures_dir / f"audit_missing_fields_{range_label}.svg",
        "Key Field Missing Rates After Cleaning",
        "Seven-day aggregate; fields with high missingness are not hard dependencies",
        aggregate_missing(audits),
        "Missing percent",
        color="#d64545",
    )

    write_horizontal_bar_svg(
        args.figures_dir / f"audit_sog_hist_{range_label}.svg",
        "SOG Distribution After Cleaning",
        "Seven-day aggregate; low-speed and stationary traffic dominate the port-water sample",
        aggregate_sog_hist(args.start, args.end, args.processed_dir),
        "Rows",
        color="#2f80ed",
    )

    write_horizontal_bar_svg(
        args.figures_dir / f"audit_track_length_hist_{range_label}.svg",
        "Trajectory Segment Length Distribution",
        "Seven-day aggregate after AIS gap-based segmentation",
        aggregate_track_length_hist(args.start, args.end, args.processed_dir),
        "Track segments",
        color="#9b51e0",
    )

    write_horizontal_bar_svg(
        args.figures_dir / f"audit_vessel_type_top_{range_label}.svg",
        "Top Vessel Type Codes",
        "Seven-day aggregate, row-weighted by AIS messages",
        aggregate_vessel_types(args.start, args.end, args.processed_dir),
        "Rows",
        color="#f2994a",
    )

    print(json.dumps({"figures_dir": str(args.figures_dir), "range": range_label}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
