#!/usr/bin/env python3
"""Build trajectory segments and basic motion features from cleaned AIS data."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from collections import Counter
from pathlib import Path


EARTH_RADIUS_NM = 3440.065


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def haversine_nm(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    lon1_rad = math.radians(lon1)
    lat1_rad = math.radians(lat1)
    lon2_rad = math.radians(lon2)
    lat2_rad = math.radians(lat2)
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    lon1_rad = math.radians(lon1)
    lat1_rad = math.radians(lat1)
    lon2_rad = math.radians(lon2)
    lat2_rad = math.radians(lat2)
    dlon = lon2_rad - lon1_rad
    x = math.sin(dlon) * math.cos(lat2_rad)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angular_delta_deg(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return abs((b - a + 180) % 360 - 180)


def fmt(value: float | int | None, digits: int = 6) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def load_rows(input_path: Path) -> list[dict[str, str]]:
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    rows.sort(key=lambda row: (row.get("mmsi", ""), row.get("base_date_time", "")))
    return rows


def build_features(
    input_path: Path,
    output_path: Path,
    stats_path: Path,
    max_time_gap_s: int,
    max_distance_gap_nm: float,
    max_implied_speed_kn: float,
) -> dict[str, object]:
    rows = load_rows(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        raise RuntimeError(f"no rows found in {input_path}")

    base_fields = list(rows[0].keys())
    feature_fields = [
        "track_id",
        "point_index",
        "segment_break_reason",
        "time_gap_s",
        "distance_gap_nm",
        "implied_sog_kn",
        "bearing_deg",
        "bearing_delta_deg",
        "sog_delta_kn",
        "accel_kn_per_min",
        "turn_rate_deg_per_min",
    ]

    counters: Counter[str] = Counter()
    track_lengths: Counter[str] = Counter()
    current_mmsi: str | None = None
    segment_index = 0
    point_index = 0
    previous: dict[str, str] | None = None
    previous_time: dt.datetime | None = None
    previous_bearing: float | None = None

    with output_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=base_fields + feature_fields)
        writer.writeheader()

        for row in rows:
            counters["input_rows"] += 1
            mmsi = row["mmsi"]
            timestamp = parse_time(row["base_date_time"])
            lon = parse_float(row.get("longitude"))
            lat = parse_float(row.get("latitude"))
            sog = parse_float(row.get("sog"))
            if lon is None or lat is None or sog is None:
                counters["skipped_missing_required"] += 1
                continue

            break_reason = ""
            time_gap_s: int | None = None
            distance_gap_nm: float | None = None
            implied_sog_kn: float | None = None
            current_bearing: float | None = None
            bearing_delta: float | None = None
            sog_delta: float | None = None
            accel: float | None = None
            turn_rate: float | None = None

            if mmsi != current_mmsi:
                current_mmsi = mmsi
                segment_index = 1
                point_index = 0
                previous = None
                previous_time = None
                previous_bearing = None
                break_reason = "new_mmsi"
            elif previous is not None and previous_time is not None:
                prev_lon = parse_float(previous.get("longitude"))
                prev_lat = parse_float(previous.get("latitude"))
                prev_sog = parse_float(previous.get("sog"))
                time_gap_s = int((timestamp - previous_time).total_seconds())
                if prev_lon is not None and prev_lat is not None:
                    distance_gap_nm = haversine_nm(prev_lon, prev_lat, lon, lat)
                    if time_gap_s > 0:
                        implied_sog_kn = distance_gap_nm / (time_gap_s / 3600)
                        current_bearing = bearing_deg(prev_lon, prev_lat, lon, lat)

                if time_gap_s <= 0:
                    segment_index += 1
                    point_index = 0
                    previous_bearing = None
                    break_reason = "non_increasing_time"
                elif time_gap_s > max_time_gap_s:
                    segment_index += 1
                    point_index = 0
                    previous_bearing = None
                    break_reason = "time_gap"
                elif distance_gap_nm is not None and distance_gap_nm > max_distance_gap_nm:
                    segment_index += 1
                    point_index = 0
                    previous_bearing = None
                    break_reason = "distance_gap"
                elif implied_sog_kn is not None and implied_sog_kn > max_implied_speed_kn:
                    segment_index += 1
                    point_index = 0
                    previous_bearing = None
                    break_reason = "implied_speed_gap"

                # A segment break rejects the edge from the previous point to
                # this point.  The course computed from that rejected edge is
                # therefore not a valid within-segment ground-track course for
                # the first point of the new segment.
                if break_reason:
                    current_bearing = None

                if not break_reason and current_bearing is not None:
                    bearing_delta = angular_delta_deg(previous_bearing, current_bearing)
                    if prev_sog is not None:
                        sog_delta = sog - prev_sog
                    if time_gap_s and time_gap_s > 0:
                        minutes = time_gap_s / 60
                        if sog_delta is not None:
                            accel = sog_delta / minutes
                        if bearing_delta is not None:
                            turn_rate = bearing_delta / minutes

            track_id = f"{mmsi}_{segment_index:04d}"
            output_row = dict(row)
            output_row.update(
                {
                    "track_id": track_id,
                    "point_index": str(point_index),
                    "segment_break_reason": break_reason,
                    "time_gap_s": fmt(time_gap_s, 0),
                    "distance_gap_nm": fmt(distance_gap_nm),
                    "implied_sog_kn": fmt(implied_sog_kn),
                    "bearing_deg": fmt(current_bearing),
                    "bearing_delta_deg": fmt(bearing_delta),
                    "sog_delta_kn": fmt(sog_delta),
                    "accel_kn_per_min": fmt(accel),
                    "turn_rate_deg_per_min": fmt(turn_rate),
                }
            )
            writer.writerow(output_row)

            counters["output_rows"] += 1
            if break_reason:
                counters[f"break_{break_reason}"] += 1
            track_lengths[track_id] += 1
            point_index += 1
            previous = row
            previous_time = timestamp
            if current_bearing is not None and not break_reason:
                previous_bearing = current_bearing

    lengths = list(track_lengths.values())
    lengths_sorted = sorted(lengths)
    stats: dict[str, object] = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "input_rows": counters["input_rows"],
        "output_rows": counters["output_rows"],
        "unique_tracks": len(track_lengths),
        "segment_parameters": {
            "max_time_gap_s": max_time_gap_s,
            "max_distance_gap_nm": max_distance_gap_nm,
            "max_implied_speed_kn": max_implied_speed_kn,
        },
        "counters": dict(counters),
        "track_length": {
            "min": lengths_sorted[0] if lengths_sorted else None,
            "mean": sum(lengths) / len(lengths) if lengths else None,
            "p50": lengths_sorted[len(lengths_sorted) // 2] if lengths_sorted else None,
            "p95": lengths_sorted[int((len(lengths_sorted) - 1) * 0.95)] if lengths_sorted else None,
            "max": lengths_sorted[-1] if lengths_sorted else None,
        },
    }
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build AIS trajectory features.")
    parser.add_argument("--input", type=Path, required=True, help="Input cleaned AIS CSV.")
    parser.add_argument("--output", type=Path, required=True, help="Output feature CSV.")
    parser.add_argument("--stats-json", type=Path, required=True, help="Output feature stats JSON.")
    parser.add_argument("--max-time-gap-s", type=int, default=1800, help="Segment break time gap.")
    parser.add_argument("--max-distance-gap-nm", type=float, default=5.0, help="Segment break distance gap.")
    parser.add_argument("--max-implied-speed-kn", type=float, default=60.0, help="Segment break implied speed.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = build_features(
        input_path=args.input,
        output_path=args.output,
        stats_path=args.stats_json,
        max_time_gap_s=args.max_time_gap_s,
        max_distance_gap_nm=args.max_distance_gap_nm,
        max_implied_speed_kn=args.max_implied_speed_kn,
    )
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
