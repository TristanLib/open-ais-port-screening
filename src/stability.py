#!/usr/bin/env python3
"""Summarize multi-day AIS pipeline outputs and grid stability."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys
from pathlib import Path


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_top_cells(path: Path, top_k: int) -> set[str]:
    cells: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            if index >= top_k:
                break
            cells.add(row["cell_id"])
    return cells


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def describe(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "mean": None, "max": None, "cv": None}
    mean = statistics.mean(values)
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {
        "count": len(values),
        "min": min(values),
        "mean": mean,
        "max": max(values),
        "cv": stdev / mean if mean else None,
    }


def summarize(
    start: dt.date,
    end: dt.date,
    tables_dir: Path,
    output_json: Path,
    output_md: Path,
    top_k: int,
) -> dict[str, object]:
    daily: list[dict[str, object]] = []
    top_cells_by_day: dict[str, set[str]] = {}

    for day in iter_dates(start, end):
        label = day.isoformat()
        crop = load_json(tables_dir / f"sf_bay_ais_{label}_crop_stats.json")
        clean = load_json(tables_dir / f"sf_bay_ais_{label}_clean_stats.json")
        features = load_json(tables_dir / f"sf_bay_ais_{label}_features_stats.json")
        grid = load_json(tables_dir / f"sf_bay_ais_{label}_grid_density_stats.json")
        tracks = load_json(tables_dir / f"sf_bay_ais_{label}_tracks_min20_stats.json")
        grid_csv = tables_dir / f"sf_bay_ais_{label}_grid_density.csv"
        top_cells_by_day[label] = load_top_cells(grid_csv, top_k)

        daily.append(
            {
                "date": label,
                "crop_rows": crop["kept_rows"],
                "unique_mmsi": crop["unique_mmsi"],
                "clean_rows": clean["output_rows"],
                "tracks": features["unique_tracks"],
                "grid_cells": grid["nonempty_cells"],
                "max_grid_count": grid["max_cell_count"],
                "tracks_min20": tracks["output_tracks"],
                "tracks_min20_rows": tracks["output_rows"],
            }
        )

    first_day = daily[0]["date"]
    first_top = top_cells_by_day[str(first_day)]
    overlap_rows = []
    previous_label: str | None = None
    for item in daily:
        label = str(item["date"])
        cells = top_cells_by_day[label]
        overlap_rows.append(
            {
                "date": label,
                "top_k": top_k,
                "overlap_with_first": round(jaccard(first_top, cells), 6),
                "overlap_with_previous": round(jaccard(top_cells_by_day[previous_label], cells), 6)
                if previous_label
                else None,
            }
        )
        previous_label = label

    numeric_keys = [
        "crop_rows",
        "unique_mmsi",
        "clean_rows",
        "tracks",
        "grid_cells",
        "max_grid_count",
        "tracks_min20",
        "tracks_min20_rows",
    ]
    additive_keys = ["crop_rows", "clean_rows", "tracks", "tracks_min20", "tracks_min20_rows"]
    totals = {key: sum(int(item[key]) for item in daily) for key in additive_keys}
    descriptors = {key: describe([float(item[key]) for item in daily]) for key in numeric_keys}

    result: dict[str, object] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": len(daily),
        "daily": daily,
        "totals": totals,
        "descriptors": descriptors,
        "top_cell_overlap": overlap_rows,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_md.write_text(render_markdown(result), encoding="utf-8")
    return result


def render_markdown(result: dict[str, object]) -> str:
    daily = result["daily"]
    overlap = result["top_cell_overlap"]
    descriptors = result["descriptors"]
    assert isinstance(daily, list)
    assert isinstance(overlap, list)
    assert isinstance(descriptors, dict)

    lines = [
        "# Seven-Day AIS Pipeline and Stability Summary",
        "",
        f"Range: {result['start']} to {result['end']}",
        "",
        "## Daily Pipeline Summary",
        "",
        "| Date | Crop rows | Clean rows | MMSI | Tracks | Min20 tracks | Min20 rows | Grid cells | Max grid count |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in daily:
        assert isinstance(item, dict)
        lines.append(
            f"| {item['date']} | {int(item['crop_rows']):,} | {int(item['clean_rows']):,} | "
            f"{int(item['unique_mmsi']):,} | {int(item['tracks']):,} | "
            f"{int(item['tracks_min20']):,} | {int(item['tracks_min20_rows']):,} | "
            f"{int(item['grid_cells']):,} | {int(item['max_grid_count']):,} |"
        )

    lines.extend(
        [
            "",
            "## Daily Variability",
            "",
            "| Metric | Mean | Min | Max | CV |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for key, value in descriptors.items():
        assert isinstance(value, dict)
        cv = value["cv"]
        lines.append(
            f"| `{key}` | {float(value['mean']):,.2f} | {float(value['min']):,.0f} | "
            f"{float(value['max']):,.0f} | {float(cv):.4f} |"
        )

    lines.extend(
        [
            "",
            "## Top-Grid Stability",
            "",
            "| Date | Top-K | Jaccard vs first day | Jaccard vs previous day |",
            "|---|---:|---:|---:|",
        ]
    )
    for item in overlap:
        assert isinstance(item, dict)
        previous = "" if item["overlap_with_previous"] is None else f"{float(item['overlap_with_previous']):.4f}"
        lines.append(
            f"| {item['date']} | {item['top_k']} | {float(item['overlap_with_first']):.4f} | {previous} |"
        )

    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize daily AIS pipeline outputs.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--tables-dir", type=Path, default=Path("outputs/tables"), help="Tables directory.")
    parser.add_argument("--output-json", type=Path, required=True, help="Output JSON path.")
    parser.add_argument("--output-md", type=Path, required=True, help="Output Markdown path.")
    parser.add_argument("--top-k", type=int, default=50, help="Number of top grid cells for stability.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = summarize(args.start, args.end, args.tables_dir, args.output_json, args.output_md, args.top_k)
    print(
        json.dumps(
            {
                "days": result["days"],
                "total_clean_rows": result["totals"]["clean_rows"],  # type: ignore[index]
                "output_md": str(args.output_md),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
