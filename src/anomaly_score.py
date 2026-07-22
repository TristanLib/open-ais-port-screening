#!/usr/bin/env python3
"""Compute a first-pass interpretable AIS anomaly score."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from collections import Counter
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


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def direction_bin(bearing: float) -> int:
    return int(((bearing + 22.5) % 360) // 45)


def direction_bin_delta(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    delta = abs(a - b)
    return min(delta, 8 - delta)


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


def load_patterns(path: Path) -> dict[str, dict[str, object]]:
    patterns: dict[str, dict[str, object]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("is_normal_route_cell") != "1":
                continue
            patterns[row["cell_id"]] = {
                "dominant_direction_bin": int(row["dominant_direction_bin"]),
                "dominant_direction": row["dominant_direction"],
                "is_high_confidence_route_cell": row["is_high_confidence_route_cell"] == "1",
                "mean_sog": parse_float(row.get("mean_sog")) or 0.0,
                "moving_points": int(row["moving_points"]),
            }
    return patterns


def score_row(
    row: dict[str, str],
    patterns: dict[str, dict[str, object]],
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    moving_sog: float,
) -> dict[str, object] | None:
    lon = parse_float(row.get("longitude"))
    lat = parse_float(row.get("latitude"))
    sog = parse_float(row.get("sog"))
    if lon is None or lat is None or sog is None:
        return None

    cell = cell_for(lon, lat, bbox, cell_size_deg)
    if cell is None:
        return None
    col, grid_row = cell
    cell_id = f"r{grid_row}_c{col}"
    pattern = patterns.get(cell_id)
    is_moving = sog >= moving_sog

    bearing = parse_float(row.get("bearing_deg"))
    if bearing is None:
        bearing = parse_float(row.get("cog"))
    observed_bin = direction_bin(bearing) if bearing is not None else None
    dominant_bin = int(pattern["dominant_direction_bin"]) if pattern else None
    bin_delta = direction_bin_delta(observed_bin, dominant_bin)

    low_empirical_route_support = 1 if is_moving and pattern is None else 0
    direction_mismatch = 1 if is_moving and pattern is not None and bin_delta is not None and bin_delta >= 2 else 0

    turn_rate = parse_float(row.get("turn_rate_deg_per_min"))
    accel = parse_float(row.get("accel_kn_per_min"))
    implied_sog = parse_float(row.get("implied_sog_kn"))
    high_turn = 1 if turn_rate is not None and turn_rate >= 30 else 0
    high_accel = 1 if accel is not None and abs(accel) >= 3 else 0
    high_speed = 1 if sog >= 15 else 0
    suspicious_stop = 1 if pattern is not None and sog < 0.5 and bool(pattern["is_high_confidence_route_cell"]) else 0
    implied_speed_flag = 1 if implied_sog is not None and implied_sog >= 30 else 0

    score = (
        0.35 * low_empirical_route_support
        + 0.25 * direction_mismatch
        + 0.15 * high_turn
        + 0.10 * high_accel
        + 0.10 * high_speed
        + 0.15 * suspicious_stop
        + 0.10 * implied_speed_flag
    )
    score = min(score, 1.0)

    reason_parts = []
    if low_empirical_route_support:
        reason_parts.append("low_empirical_route_support")
    if direction_mismatch:
        reason_parts.append("direction_mismatch")
    if high_turn:
        reason_parts.append("high_turn")
    if high_accel:
        reason_parts.append("high_accel")
    if high_speed:
        reason_parts.append("high_speed")
    if suspicious_stop:
        reason_parts.append("suspicious_stop")
    if implied_speed_flag:
        reason_parts.append("implied_speed")

    indicator_count = sum(
        [
            low_empirical_route_support,
            direction_mismatch,
            high_turn,
            high_accel,
            high_speed,
            suspicious_stop,
            implied_speed_flag,
        ]
    )
    corroborated_candidate = int(indicator_count >= 2)
    if low_empirical_route_support and indicator_count == 1:
        evidence_tier = "low_support_only"
    elif corroborated_candidate:
        evidence_tier = "corroborated_candidate"
    elif indicator_count == 1:
        evidence_tier = "single_indicator_candidate"
    else:
        evidence_tier = "no_candidate_evidence"

    return {
        "mmsi": row.get("mmsi", ""),
        "base_date_time": row.get("base_date_time", ""),
        "longitude": lon,
        "latitude": lat,
        "sog": sog,
        "track_id": row.get("track_id", ""),
        "cell_id": cell_id,
        "anomaly_score": round(score, 6),
        "reasons": ";".join(reason_parts),
        "low_empirical_route_support": low_empirical_route_support,
        "direction_mismatch": direction_mismatch,
        "high_turn": high_turn,
        "high_accel": high_accel,
        "high_speed": high_speed,
        "suspicious_stop": suspicious_stop,
        "implied_speed_flag": implied_speed_flag,
        "indicator_count": indicator_count,
        "corroborated_candidate": corroborated_candidate,
        "evidence_tier": evidence_tier,
        "observed_direction_bin": "" if observed_bin is None else observed_bin,
        "normal_direction_bin": "" if dominant_bin is None else dominant_bin,
        "direction_bin_delta": "" if bin_delta is None else bin_delta,
    }


def compute_anomalies(
    start: dt.date,
    end: dt.date,
    processed_dir: Path,
    patterns_csv: Path,
    output_csv: Path,
    output_geojson: Path,
    stats_json: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    moving_sog: float,
    min_score: float,
    geojson_limit: int,
    dataset_prefix: str = "sf_bay_ais",
) -> dict[str, object]:
    patterns = load_patterns(patterns_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_geojson.parent.mkdir(parents=True, exist_ok=True)
    stats_json.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "mmsi",
        "base_date_time",
        "longitude",
        "latitude",
        "sog",
        "track_id",
        "cell_id",
        "anomaly_score",
        "reasons",
        "low_empirical_route_support",
        "direction_mismatch",
        "high_turn",
        "high_accel",
        "high_speed",
        "suspicious_stop",
        "implied_speed_flag",
        "indicator_count",
        "corroborated_candidate",
        "evidence_tier",
        "observed_direction_bin",
        "normal_direction_bin",
        "direction_bin_delta",
    ]
    counters: Counter[str] = Counter()
    top_for_geojson: list[dict[str, object]] = []

    with output_csv.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()

        for day in iter_dates(start, end):
            path = processed_dir / f"{dataset_prefix}_{day.isoformat()}_features.csv"
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    counters["input_rows"] += 1
                    scored = score_row(row, patterns, bbox, cell_size_deg, moving_sog)
                    if scored is None:
                        counters["skipped_rows"] += 1
                        continue
                    if float(scored["anomaly_score"]) < min_score:
                        continue
                    writer.writerow(scored)
                    counters["anomaly_rows"] += 1
                    counters[f"tier_{scored['evidence_tier']}"] += 1
                    for reason in str(scored["reasons"]).split(";"):
                        if reason:
                            counters[f"reason_{reason}"] += 1

                    top_for_geojson.append(scored)
                    top_for_geojson.sort(key=lambda item: float(item["anomaly_score"]), reverse=True)
                    if len(top_for_geojson) > geojson_limit:
                        top_for_geojson.pop()

    features = []
    for item in top_for_geojson:
        properties = {key: value for key, value in item.items() if key not in {"longitude", "latitude"}}
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [item["longitude"], item["latitude"]]},
                "properties": properties,
            }
        )
    output_geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    stats: dict[str, object] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "patterns_csv": str(patterns_csv),
        "normal_pattern_cells": len(patterns),
        "min_score": min_score,
        "geojson_limit": geojson_limit,
        "dataset_prefix": dataset_prefix,
        "candidate_tiers": {
            "low_support_only": "single low-empirical-route-support indicator; retained for audit",
            "corroborated_candidate": "two or more independent rule indicators",
        },
        "indicator_parameters": {
            "low_empirical_route_support_weight": 0.35,
            "direction_mismatch_min_bins": 2,
            "high_turn_deg_per_min": 30.0,
            "high_abs_accel_kn_per_min": 3.0,
            "high_speed_kn": 15.0,
            "unexpected_stop_kn": 0.5,
            "implied_speed_kn": 30.0,
        },
        "counters": dict(counters),
        "anomaly_rate_percent": round(counters["anomaly_rows"] / counters["input_rows"] * 100, 6)
        if counters["input_rows"]
        else 0.0,
    }
    stats_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute first-pass AIS anomaly scores.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"), help="Processed directory.")
    parser.add_argument("--patterns-csv", type=Path, required=True, help="Traffic patterns CSV.")
    parser.add_argument("--output-csv", type=Path, required=True, help="Output anomaly CSV.")
    parser.add_argument("--output-geojson", type=Path, required=True, help="Output anomaly GeoJSON.")
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
    parser.add_argument("--moving-sog", type=float, default=1.0, help="SOG threshold for moving behavior.")
    parser.add_argument("--min-score", type=float, default=0.35, help="Minimum anomaly score to export.")
    parser.add_argument("--geojson-limit", type=int, default=2000, help="Maximum points in GeoJSON output.")
    parser.add_argument("--dataset-prefix", default="sf_bay_ais", help="Daily feature-file prefix.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = compute_anomalies(
        start=args.start,
        end=args.end,
        processed_dir=args.processed_dir,
        patterns_csv=args.patterns_csv,
        output_csv=args.output_csv,
        output_geojson=args.output_geojson,
        stats_json=args.stats_json,
        bbox=tuple(args.bbox),
        cell_size_deg=args.cell_size_deg,
        moving_sog=args.moving_sog,
        min_score=args.min_score,
        geojson_limit=args.geojson_limit,
        dataset_prefix=args.dataset_prefix,
    )
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
