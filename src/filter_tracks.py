#!/usr/bin/env python3
"""Filter feature rows by trajectory segment length."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


def summarize(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "mean": None, "p50": None, "p95": None, "max": None}
    sorted_values = sorted(values)
    return {
        "count": len(values),
        "min": sorted_values[0],
        "mean": sum(sorted_values) / len(sorted_values),
        "p50": sorted_values[len(sorted_values) // 2],
        "p95": sorted_values[int((len(sorted_values) - 1) * 0.95)],
        "max": sorted_values[-1],
    }


def filter_tracks(input_path: Path, output_path: Path, stats_json: Path, min_points: int) -> dict[str, object]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats_json.parent.mkdir(parents=True, exist_ok=True)

    track_counts: Counter[str] = Counter()
    with input_path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames
        if fields is None:
            raise RuntimeError(f"no CSV header found in {input_path}")
        for row in reader:
            track_id = row.get("track_id", "")
            if track_id:
                track_counts[track_id] += 1

    keep_tracks = {track_id for track_id, count in track_counts.items() if count >= min_points}
    counters: Counter[str] = Counter()

    with input_path.open("r", encoding="utf-8", newline="") as source, output_path.open(
        "w", encoding="utf-8", newline=""
    ) as target:
        reader = csv.DictReader(source)
        writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            counters["input_rows"] += 1
            track_id = row.get("track_id", "")
            if track_id in keep_tracks:
                writer.writerow(row)
                counters["output_rows"] += 1
            else:
                counters["dropped_rows"] += 1

    kept_lengths = [track_counts[track_id] for track_id in keep_tracks]
    dropped_lengths = [count for track_id, count in track_counts.items() if track_id not in keep_tracks]
    stats: dict[str, object] = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "min_points": min_points,
        "input_tracks": len(track_counts),
        "output_tracks": len(keep_tracks),
        "input_rows": counters["input_rows"],
        "output_rows": counters["output_rows"],
        "dropped_rows": counters["dropped_rows"],
        "kept_row_percent": round(counters["output_rows"] / counters["input_rows"] * 100, 4)
        if counters["input_rows"]
        else 0.0,
        "kept_track_percent": round(len(keep_tracks) / len(track_counts) * 100, 4) if track_counts else 0.0,
        "kept_track_length": summarize(kept_lengths),
        "dropped_track_length": summarize(dropped_lengths),
    }
    stats_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Filter AIS trajectory feature rows by track length.")
    parser.add_argument("--input", type=Path, required=True, help="Input feature CSV.")
    parser.add_argument("--output", type=Path, required=True, help="Output filtered feature CSV.")
    parser.add_argument("--stats-json", type=Path, required=True, help="Output filter stats JSON.")
    parser.add_argument("--min-points", type=int, default=20, help="Minimum points per track segment.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = filter_tracks(args.input, args.output, args.stats_json, args.min_points)
    print(
        json.dumps(
            {
                "input_tracks": stats["input_tracks"],
                "output_tracks": stats["output_tracks"],
                "output_rows": stats["output_rows"],
                "kept_row_percent": stats["kept_row_percent"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
