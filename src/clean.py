#!/usr/bin/env python3
"""Apply conservative AIS cleaning rules to cropped CSV files."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from collections import Counter
from pathlib import Path


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def valid_mmsi(value: str | None) -> bool:
    if value is None:
        return False
    stripped = value.strip()
    if not stripped.isdigit():
        return False
    number = int(stripped)
    return 100_000_000 <= number <= 999_999_999


def valid_timestamp(value: str | None) -> bool:
    if not value:
        return False
    try:
        dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return True


def in_bbox(lon: float, lat: float, bbox: tuple[float, float, float, float] | None) -> bool:
    if bbox is None:
        return -180 <= lon <= 180 and -90 <= lat <= 90
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def normalize_optional_range(
    row: dict[str, str],
    field: str,
    min_value: float,
    max_value: float,
    stats: Counter[str],
    invalid_name: str,
) -> None:
    value = parse_float(row.get(field))
    if value is None:
        return
    if not min_value <= value <= max_value:
        row[field] = ""
        stats[invalid_name] += 1


def clean_csv(
    input_path: Path,
    output_path: Path,
    stats_path: Path,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, object]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.parent.mkdir(parents=True, exist_ok=True)

    counters: Counter[str] = Counter()
    seen_keys: set[tuple[str, str, str, str]] = set()

    with input_path.open("r", encoding="utf-8", newline="") as source, output_path.open(
        "w", encoding="utf-8", newline=""
    ) as target:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise RuntimeError(f"no CSV header found in {input_path}")
        writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
        writer.writeheader()

        for row in reader:
            counters["input_rows"] += 1

            if not valid_mmsi(row.get("mmsi")):
                counters["drop_invalid_mmsi"] += 1
                continue

            if not valid_timestamp(row.get("base_date_time")):
                counters["drop_invalid_timestamp"] += 1
                continue

            lon = parse_float(row.get("longitude"))
            lat = parse_float(row.get("latitude"))
            if lon is None or lat is None or not in_bbox(lon, lat, bbox):
                counters["drop_invalid_position"] += 1
                continue

            sog = parse_float(row.get("sog"))
            if sog is None:
                counters["drop_missing_sog"] += 1
                continue
            if sog < 0 or sog > 60:
                counters["drop_invalid_sog"] += 1
                continue

            key = (
                row.get("mmsi", ""),
                row.get("base_date_time", ""),
                row.get("longitude", ""),
                row.get("latitude", ""),
            )
            if key in seen_keys:
                counters["drop_duplicate_point"] += 1
                continue
            seen_keys.add(key)

            # Keep rows with missing COG/heading, but normalize unusable sentinels.
            cog = parse_float(row.get("cog"))
            if cog is not None and not 0 <= cog < 360:
                row["cog"] = ""
                counters["blank_invalid_cog"] += 1

            heading = parse_float(row.get("heading"))
            if heading is not None and (heading == 511 or not 0 <= heading <= 360):
                row["heading"] = ""
                counters["blank_invalid_heading"] += 1

            normalize_optional_range(row, "length", 0, 500, counters, "blank_invalid_length")
            normalize_optional_range(row, "width", 0, 100, counters, "blank_invalid_width")
            normalize_optional_range(row, "draft", 0, 30, counters, "blank_invalid_draft")

            writer.writerow(row)
            counters["output_rows"] += 1

    input_rows = counters["input_rows"]
    output_rows = counters["output_rows"]
    summary: dict[str, object] = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "bbox": bbox,
        "input_rows": input_rows,
        "output_rows": output_rows,
        "dropped_rows": input_rows - output_rows,
        "kept_percent": round(output_rows / input_rows * 100, 4) if input_rows else 0.0,
        "counters": dict(counters),
    }
    stats_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean cropped AIS CSV data.")
    parser.add_argument("--input", type=Path, required=True, help="Input cropped AIS CSV.")
    parser.add_argument("--output", type=Path, required=True, help="Output cleaned AIS CSV.")
    parser.add_argument("--stats-json", type=Path, required=True, help="Output cleaning stats JSON.")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        help="Optional bbox check in EPSG:4326.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = clean_csv(args.input, args.output, args.stats_json, tuple(args.bbox) if args.bbox else None)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
