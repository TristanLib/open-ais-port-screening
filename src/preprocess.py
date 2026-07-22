#!/usr/bin/env python3
"""Preprocess public AIS files.

The first supported operation is a streaming bounding-box crop. It keeps this
project reproducible without loading national-scale AIS files into memory.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterator, TextIO


@contextlib.contextmanager
def open_ais_text(path: Path) -> Iterator[TextIO]:
    """Open AIS CSV-like files as text.

    Supported inputs:
    - `.csv`
    - `.csv.zst` using the local `zstd` executable
    - `.zip` containing one CSV file
    """
    suffixes = "".join(path.suffixes).lower()

    if suffixes.endswith(".csv.zst"):
        process = subprocess.Popen(
            ["zstd", "-dc", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if process.stdout is None:
            raise RuntimeError("failed to open zstd stdout")
        try:
            yield process.stdout
        finally:
            process.stdout.close()
            _, stderr = process.communicate()
            # A caller may intentionally stop reading early during smoke tests,
            # which can make zstd exit through SIGPIPE/broken pipe.
            if process.returncode not in (0, None, -13, 141):
                raise RuntimeError(stderr.strip() or f"zstd failed for {path}")
        return

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not names:
                raise RuntimeError(f"no CSV file found in {path}")
            with archive.open(names[0]) as raw:
                wrapper = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                try:
                    yield wrapper
                finally:
                    wrapper.detach()
        return

    with path.open("r", encoding="utf-8", newline="") as handle:
        yield handle


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def in_bbox(lon: float, lat: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def crop_bbox(
    input_path: Path,
    output_path: Path,
    bbox: tuple[float, float, float, float],
    stats_path: Path | None = None,
    input_limit: int | None = None,
) -> dict[str, object]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if stats_path:
        stats_path.parent.mkdir(parents=True, exist_ok=True)

    stats: dict[str, object] = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "bbox": {
            "min_lon": bbox[0],
            "min_lat": bbox[1],
            "max_lon": bbox[2],
            "max_lat": bbox[3],
        },
        "total_rows": 0,
        "kept_rows": 0,
        "invalid_position_rows": 0,
        "outside_bbox_rows": 0,
        "unique_mmsi": 0,
        "min_time": None,
        "max_time": None,
    }
    mmsi_values: set[str] = set()

    with open_ais_text(input_path) as source, output_path.open("w", encoding="utf-8", newline="") as target:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise RuntimeError(f"no CSV header found in {input_path}")

        writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
        writer.writeheader()

        for row in reader:
            total_rows = int(stats["total_rows"]) + 1
            stats["total_rows"] = total_rows
            if input_limit is not None and total_rows > input_limit:
                break

            lon = parse_float(row.get("longitude") or row.get("LON") or row.get("Lon"))
            lat = parse_float(row.get("latitude") or row.get("LAT") or row.get("Lat"))
            if lon is None or lat is None:
                stats["invalid_position_rows"] = int(stats["invalid_position_rows"]) + 1
                continue

            if not in_bbox(lon, lat, bbox):
                stats["outside_bbox_rows"] = int(stats["outside_bbox_rows"]) + 1
                continue

            writer.writerow(row)
            stats["kept_rows"] = int(stats["kept_rows"]) + 1

            mmsi = row.get("mmsi") or row.get("MMSI")
            if mmsi:
                mmsi_values.add(mmsi)

            timestamp = row.get("base_date_time") or row.get("BaseDateTime") or row.get("timestamp")
            if timestamp:
                if stats["min_time"] is None or timestamp < str(stats["min_time"]):
                    stats["min_time"] = timestamp
                if stats["max_time"] is None or timestamp > str(stats["max_time"]):
                    stats["max_time"] = timestamp

    stats["unique_mmsi"] = len(mmsi_values)

    if stats_path:
        stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preprocess public AIS files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    crop = subparsers.add_parser("crop-bbox", help="Stream crop AIS rows by bounding box.")
    crop.add_argument("--input", type=Path, required=True, help="Input AIS CSV, CSV.ZST, or ZIP file.")
    crop.add_argument("--output", type=Path, required=True, help="Output cropped CSV file.")
    crop.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        required=True,
        help="Bounding box in EPSG:4326.",
    )
    crop.add_argument("--stats-json", type=Path, help="Optional output stats JSON path.")
    crop.add_argument("--input-limit", type=int, help="Optional input row limit for smoke tests.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "crop-bbox":
        stats = crop_bbox(
            input_path=args.input,
            output_path=args.output,
            bbox=tuple(args.bbox),
            stats_path=args.stats_json,
            input_limit=args.input_limit,
        )
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
