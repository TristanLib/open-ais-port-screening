#!/usr/bin/env python3
"""Learn lightweight traffic pattern grid cells from AIS trajectory features."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from collections import Counter
from pathlib import Path


DIRECTION_LABELS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


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


def direction_bin(bearing: float) -> int:
    return int(((bearing + 22.5) % 360) // 45)


def quantile(values: list[int], q: float) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, max(0, int((len(sorted_values) - 1) * q)))
    return sorted_values[index]


def polygon_for_cell(
    row: int,
    col: int,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> list[list[float]]:
    min_lon, min_lat, _, _ = bbox
    west = min_lon + col * cell_size_deg
    south = min_lat + row * cell_size_deg
    east = west + cell_size_deg
    north = south + cell_size_deg
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def learn_pattern_rows(
    dates: list[dt.date],
    processed_dir: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    min_sog: float,
    min_points: int,
    min_tracks: int,
    dataset_prefix: str = "sf_bay_ais",
    track_min_points: int = 20,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Learn traffic-pattern rows from exactly the supplied dates.

    Keeping the date list explicit is important for fold-specific evaluation:
    a held-out date must never participate in route-cell thresholds or
    dominant-direction estimates.
    """
    cells: dict[tuple[int, int], dict[str, object]] = {}
    counters: Counter[str] = Counter()

    for day in dates:
        path = processed_dir / f"{dataset_prefix}_{day.isoformat()}_tracks_min{track_min_points}.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                counters["input_rows"] += 1
                sog = parse_float(row.get("sog"))
                if sog is None or sog < min_sog:
                    counters["skip_low_speed"] += 1
                    continue
                lon = parse_float(row.get("longitude"))
                lat = parse_float(row.get("latitude"))
                if lon is None or lat is None:
                    counters["skip_missing_position"] += 1
                    continue
                cell = cell_for(lon, lat, bbox, cell_size_deg)
                if cell is None:
                    counters["skip_outside_bbox"] += 1
                    continue
                bearing = parse_float(row.get("bearing_deg"))
                if bearing is None:
                    bearing = parse_float(row.get("cog"))
                if bearing is None:
                    counters["skip_missing_bearing"] += 1
                    continue

                record = cells.setdefault(
                    cell,
                    {
                        "moving_points": 0,
                        "sog_sum": 0.0,
                        "mmsi": set(),
                        "track_id": set(),
                        "direction_bins": Counter(),
                    },
                )
                record["moving_points"] = int(record["moving_points"]) + 1
                record["sog_sum"] = float(record["sog_sum"]) + sog
                if row.get("mmsi"):
                    record["mmsi"].add(row["mmsi"])  # type: ignore[union-attr]
                if row.get("track_id"):
                    record["track_id"].add(row["track_id"])  # type: ignore[union-attr]
                record["direction_bins"][direction_bin(bearing)] += 1  # type: ignore[index]
                counters["used_rows"] += 1

    moving_counts = [int(value["moving_points"]) for value in cells.values()]
    q75 = quantile(moving_counts, 0.75)
    q90 = quantile(moving_counts, 0.90)
    normal_threshold = max(min_points, q75)
    high_threshold = max(min_points, q90)

    rows: list[dict[str, object]] = []
    for (col, row), value in cells.items():
        moving_points = int(value["moving_points"])
        tracks = len(value["track_id"])  # type: ignore[arg-type]
        mmsi = len(value["mmsi"])  # type: ignore[arg-type]
        direction_counts: Counter[int] = value["direction_bins"]  # type: ignore[assignment]
        dominant_bin, dominant_count = direction_counts.most_common(1)[0]
        dominance_ratio = dominant_count / moving_points if moving_points else 0.0
        min_lon, min_lat, _, _ = bbox
        cell_min_lon = min_lon + col * cell_size_deg
        cell_min_lat = min_lat + row * cell_size_deg
        is_normal = moving_points >= normal_threshold and tracks >= min_tracks
        is_high_confidence = moving_points >= high_threshold and tracks >= min_tracks
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
                "moving_points": moving_points,
                "unique_mmsi": mmsi,
                "unique_tracks": tracks,
                "mean_sog": round(float(value["sog_sum"]) / moving_points, 6),
                "dominant_direction": DIRECTION_LABELS[dominant_bin],
                "dominant_direction_bin": dominant_bin,
                "dominance_ratio": round(dominance_ratio, 6),
                "is_normal_route_cell": int(is_normal),
                "is_high_confidence_route_cell": int(is_high_confidence),
            }
        )

    rows.sort(key=lambda item: int(item["moving_points"]), reverse=True)
    stats: dict[str, object] = {
        "dates": [day.isoformat() for day in dates],
        "input_rows": counters["input_rows"],
        "used_rows": counters["used_rows"],
        "skip_low_speed": counters["skip_low_speed"],
        "skip_missing_bearing": counters["skip_missing_bearing"],
        "skip_missing_position": counters["skip_missing_position"],
        "skip_outside_bbox": counters["skip_outside_bbox"],
        "nonempty_moving_cells": len(rows),
        "normal_route_cells": sum(int(item["is_normal_route_cell"]) for item in rows),
        "high_confidence_route_cells": sum(int(item["is_high_confidence_route_cell"]) for item in rows),
        "min_sog": min_sog,
        "cell_size_deg": cell_size_deg,
        "normal_threshold": normal_threshold,
        "high_threshold": high_threshold,
        "min_tracks": min_tracks,
        "dataset_prefix": dataset_prefix,
        "track_min_points": track_min_points,
    }
    return rows, stats


def patterns_from_rows(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Return the anomaly scorer's in-memory regular-cell model."""
    patterns: dict[str, dict[str, object]] = {}
    for row in rows:
        if int(row["is_normal_route_cell"]) != 1:
            continue
        patterns[str(row["cell_id"])] = {
            "dominant_direction_bin": int(row["dominant_direction_bin"]),
            "dominant_direction": row["dominant_direction"],
            "is_high_confidence_route_cell": int(row["is_high_confidence_route_cell"]) == 1,
            "mean_sog": float(row["mean_sog"]),
            "moving_points": int(row["moving_points"]),
        }
    return patterns


def learn_patterns(
    start: dt.date,
    end: dt.date,
    processed_dir: Path,
    output_csv: Path,
    output_geojson: Path,
    stats_json: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    min_sog: float,
    min_points: int,
    min_tracks: int,
    dataset_prefix: str = "sf_bay_ais",
    track_min_points: int = 20,
) -> dict[str, object]:
    rows, learned_stats = learn_pattern_rows(
        iter_dates(start, end),
        processed_dir,
        bbox,
        cell_size_deg,
        min_sog,
        min_points,
        min_tracks,
        dataset_prefix,
        track_min_points,
    )
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
        "moving_points",
        "unique_mmsi",
        "unique_tracks",
        "mean_sog",
        "dominant_direction",
        "dominant_direction_bin",
        "dominance_ratio",
        "is_normal_route_cell",
        "is_high_confidence_route_cell",
    ]

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_geojson.parent.mkdir(parents=True, exist_ok=True)
    stats_json.parent.mkdir(parents=True, exist_ok=True)

    with output_csv.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    features = []
    for item in rows:
        if not int(item["is_normal_route_cell"]):
            continue
        geometry = {
            "type": "Polygon",
            "coordinates": [polygon_for_cell(int(item["row"]), int(item["col"]), bbox, cell_size_deg)],
        }
        properties = {
            key: item[key]
            for key in [
                "cell_id",
                "moving_points",
                "unique_mmsi",
                "unique_tracks",
                "mean_sog",
                "dominant_direction",
                "dominance_ratio",
                "is_high_confidence_route_cell",
            ]
        }
        features.append({"type": "Feature", "geometry": geometry, "properties": properties})

    output_geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    stats: dict[str, object] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "input_rows": learned_stats["input_rows"],
        "used_rows": learned_stats["used_rows"],
        "skip_low_speed": learned_stats["skip_low_speed"],
        "skip_missing_bearing": learned_stats["skip_missing_bearing"],
        "nonempty_moving_cells": learned_stats["nonempty_moving_cells"],
        "normal_route_cells": learned_stats["normal_route_cells"],
        "high_confidence_route_cells": learned_stats["high_confidence_route_cells"],
        "min_sog": min_sog,
        "cell_size_deg": cell_size_deg,
        "normal_threshold": learned_stats["normal_threshold"],
        "high_threshold": learned_stats["high_threshold"],
        "min_tracks": min_tracks,
        "dataset_prefix": dataset_prefix,
        "track_min_points": track_min_points,
        "top_cells": rows[:20],
    }
    stats_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Learn lightweight AIS traffic pattern cells.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"), help="Processed data directory.")
    parser.add_argument("--output-csv", type=Path, required=True, help="Output pattern CSV.")
    parser.add_argument("--output-geojson", type=Path, required=True, help="Output normal route GeoJSON.")
    parser.add_argument("--stats-json", type=Path, required=True, help="Output stats JSON.")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        required=True,
        help="Bounding box in EPSG:4326.",
    )
    parser.add_argument("--cell-size-deg", type=float, default=0.005, help="Grid cell size.")
    parser.add_argument("--min-sog", type=float, default=1.0, help="Minimum SOG for moving traffic patterns.")
    parser.add_argument("--min-points", type=int, default=50, help="Minimum moving points for normal cells.")
    parser.add_argument("--min-tracks", type=int, default=5, help="Minimum unique tracks for normal cells.")
    parser.add_argument("--dataset-prefix", default="sf_bay_ais", help="Daily processed-file prefix.")
    parser.add_argument("--track-min-points", type=int, default=20, help="Track-file minimum-point suffix.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = learn_patterns(
        start=args.start,
        end=args.end,
        processed_dir=args.processed_dir,
        output_csv=args.output_csv,
        output_geojson=args.output_geojson,
        stats_json=args.stats_json,
        bbox=tuple(args.bbox),
        cell_size_deg=args.cell_size_deg,
        min_sog=args.min_sog,
        min_points=args.min_points,
        min_tracks=args.min_tracks,
        dataset_prefix=args.dataset_prefix,
        track_min_points=args.track_min_points,
    )
    print(
        json.dumps(
            {
                "used_rows": stats["used_rows"],
                "normal_route_cells": stats["normal_route_cells"],
                "high_confidence_route_cells": stats["high_confidence_route_cells"],
                "normal_threshold": stats["normal_threshold"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
