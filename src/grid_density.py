#!/usr/bin/env python3
"""Aggregate AIS points into regular spatial grid cells."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def cell_for(
    lon: float,
    lat: float,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> tuple[int, int] | None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
        return None
    col = int((lon - min_lon) / cell_size_deg)
    row = int((lat - min_lat) / cell_size_deg)
    max_col = int((max_lon - min_lon) / cell_size_deg)
    max_row = int((max_lat - min_lat) / cell_size_deg)
    return min(col, max_col), min(row, max_row)


def aggregate_grid(
    input_path: Path,
    output_csv: Path,
    stats_json: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> dict[str, object]:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    stats_json.parent.mkdir(parents=True, exist_ok=True)

    cells: dict[tuple[int, int], dict[str, object]] = {}
    total_rows = 0
    used_rows = 0

    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            total_rows += 1
            lon = parse_float(row.get("longitude"))
            lat = parse_float(row.get("latitude"))
            sog = parse_float(row.get("sog"))
            if lon is None or lat is None:
                continue
            cell = cell_for(lon, lat, bbox, cell_size_deg)
            if cell is None:
                continue
            used_rows += 1
            record = cells.setdefault(
                cell,
                {
                    "point_count": 0,
                    "mmsi": set(),
                    "track_id": set(),
                    "sog_sum": 0.0,
                    "sog_count": 0,
                    "stationary_count": 0,
                    "moving_count": 0,
                    "high_speed_count": 0,
                },
            )
            record["point_count"] = int(record["point_count"]) + 1
            if row.get("mmsi"):
                record["mmsi"].add(row["mmsi"])  # type: ignore[union-attr]
            if row.get("track_id"):
                record["track_id"].add(row["track_id"])  # type: ignore[union-attr]
            if sog is not None:
                record["sog_sum"] = float(record["sog_sum"]) + sog
                record["sog_count"] = int(record["sog_count"]) + 1
                if sog < 0.5:
                    record["stationary_count"] = int(record["stationary_count"]) + 1
                else:
                    record["moving_count"] = int(record["moving_count"]) + 1
                if sog >= 12:
                    record["high_speed_count"] = int(record["high_speed_count"]) + 1

    min_lon, min_lat, _, _ = bbox
    rows: list[dict[str, object]] = []
    for (col, row), value in cells.items():
        point_count = int(value["point_count"])
        sog_count = int(value["sog_count"])
        mean_sog = float(value["sog_sum"]) / sog_count if sog_count else None
        cell_min_lon = min_lon + col * cell_size_deg
        cell_min_lat = min_lat + row * cell_size_deg
        rows.append(
            {
                "cell_id": f"r{row}_c{col}",
                "row": row,
                "col": col,
                "min_lon": round(cell_min_lon, 6),
                "min_lat": round(cell_min_lat, 6),
                "max_lon": round(cell_min_lon + cell_size_deg, 6),
                "max_lat": round(cell_min_lat + cell_size_deg, 6),
                "center_lon": round(cell_min_lon + cell_size_deg / 2, 6),
                "center_lat": round(cell_min_lat + cell_size_deg / 2, 6),
                "point_count": point_count,
                "unique_mmsi": len(value["mmsi"]),  # type: ignore[arg-type]
                "unique_tracks": len(value["track_id"]),  # type: ignore[arg-type]
                "mean_sog": round(mean_sog, 6) if mean_sog is not None else "",
                "stationary_count": value["stationary_count"],
                "moving_count": value["moving_count"],
                "high_speed_count": value["high_speed_count"],
            }
        )

    rows.sort(key=lambda item: int(item["point_count"]), reverse=True)
    fieldnames = [
        "cell_id",
        "row",
        "col",
        "min_lon",
        "min_lat",
        "max_lon",
        "max_lat",
        "center_lon",
        "center_lat",
        "point_count",
        "unique_mmsi",
        "unique_tracks",
        "mean_sog",
        "stationary_count",
        "moving_count",
        "high_speed_count",
    ]
    with output_csv.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    point_counts = [int(row["point_count"]) for row in rows]
    stats: dict[str, object] = {
        "input_path": str(input_path),
        "output_csv": str(output_csv),
        "total_rows": total_rows,
        "used_rows": used_rows,
        "cell_size_deg": cell_size_deg,
        "nonempty_cells": len(rows),
        "max_cell_count": max(point_counts) if point_counts else 0,
        "top_cells": rows[:20],
    }
    stats_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate AIS points into a grid.")
    parser.add_argument("--input", type=Path, required=True, help="Input AIS feature CSV.")
    parser.add_argument("--output-csv", type=Path, required=True, help="Output grid density CSV.")
    parser.add_argument("--stats-json", type=Path, required=True, help="Output grid density stats JSON.")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        required=True,
        help="Bounding box in EPSG:4326.",
    )
    parser.add_argument("--cell-size-deg", type=float, default=0.005, help="Grid cell size in degrees.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = aggregate_grid(args.input, args.output_csv, args.stats_json, tuple(args.bbox), args.cell_size_deg)
    print(
        json.dumps(
            {
                "used_rows": stats["used_rows"],
                "nonempty_cells": stats["nonempty_cells"],
                "max_cell_count": stats["max_cell_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
