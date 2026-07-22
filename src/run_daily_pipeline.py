#!/usr/bin/env python3
"""Run the daily AIS processing pipeline for a date range."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from clean import clean_csv
from download import download_file, noaa_daily_url, output_name
from features import build_features
from filter_tracks import filter_tracks
from grid_density import aggregate_grid
from preprocess import crop_bbox
from audit import audit_csv, write_markdown


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def date_label(day: dt.date) -> str:
    return day.isoformat()


def run_day(
    day: dt.date,
    bbox: tuple[float, float, float, float],
    raw_dir: Path,
    processed_dir: Path,
    tables_dir: Path,
    skip_download: bool,
    overwrite: bool,
    min_points: int,
    cell_size_deg: float,
) -> dict[str, object]:
    label = date_label(day)
    raw_path = raw_dir / "noaa" / str(day.year) / output_name(day)
    crop_path = processed_dir / f"sf_bay_ais_{label}.csv"
    clean_path = processed_dir / f"sf_bay_ais_{label}_clean.csv"
    feature_path = processed_dir / f"sf_bay_ais_{label}_features.csv"
    tracks_path = processed_dir / f"sf_bay_ais_{label}_tracks_min{min_points}.csv"

    crop_stats_path = tables_dir / f"sf_bay_ais_{label}_crop_stats.json"
    clean_stats_path = tables_dir / f"sf_bay_ais_{label}_clean_stats.json"
    clean_audit_json_path = tables_dir / f"sf_bay_ais_{label}_clean_audit.json"
    clean_audit_md_path = tables_dir / f"sf_bay_ais_{label}_clean_audit.md"
    feature_stats_path = tables_dir / f"sf_bay_ais_{label}_features_stats.json"
    grid_csv_path = tables_dir / f"sf_bay_ais_{label}_grid_density.csv"
    grid_stats_path = tables_dir / f"sf_bay_ais_{label}_grid_density_stats.json"
    tracks_stats_path = tables_dir / f"sf_bay_ais_{label}_tracks_min{min_points}_stats.json"

    print(f"== {label} ==", flush=True)
    if not skip_download and (overwrite or not raw_path.exists()):
        download_file(noaa_daily_url(day), raw_path, overwrite=overwrite)
    elif raw_path.exists():
        print(f"raw exists: {raw_path}", flush=True)
    else:
        raise RuntimeError(f"raw file missing and download skipped: {raw_path}")

    if overwrite or not crop_path.exists():
        crop_stats = crop_bbox(raw_path, crop_path, bbox, crop_stats_path)
    else:
        print(f"crop exists: {crop_path}", flush=True)
        crop_stats = json.loads(crop_stats_path.read_text(encoding="utf-8"))

    if overwrite or not clean_path.exists():
        clean_stats = clean_csv(crop_path, clean_path, clean_stats_path, bbox)
    else:
        print(f"clean exists: {clean_path}", flush=True)
        clean_stats = json.loads(clean_stats_path.read_text(encoding="utf-8"))

    if overwrite or not clean_audit_json_path.exists() or not clean_audit_md_path.exists():
        clean_audit = audit_csv(clean_path)
        clean_audit_json_path.write_text(
            json.dumps(clean_audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        write_markdown(clean_audit, clean_audit_md_path)
    else:
        print(f"clean audit exists: {clean_audit_json_path}", flush=True)

    if overwrite or not feature_path.exists():
        feature_stats = build_features(
            clean_path,
            feature_path,
            feature_stats_path,
            max_time_gap_s=1800,
            max_distance_gap_nm=5.0,
            max_implied_speed_kn=60.0,
        )
    else:
        print(f"features exist: {feature_path}", flush=True)
        feature_stats = json.loads(feature_stats_path.read_text(encoding="utf-8"))

    if overwrite or not grid_csv_path.exists():
        grid_stats = aggregate_grid(feature_path, grid_csv_path, grid_stats_path, bbox, cell_size_deg)
    else:
        print(f"grid exists: {grid_csv_path}", flush=True)
        grid_stats = json.loads(grid_stats_path.read_text(encoding="utf-8"))

    if overwrite or not tracks_path.exists():
        tracks_stats = filter_tracks(feature_path, tracks_path, tracks_stats_path, min_points)
    else:
        print(f"tracks exist: {tracks_path}", flush=True)
        tracks_stats = json.loads(tracks_stats_path.read_text(encoding="utf-8"))

    summary = {
        "date": label,
        "raw_path": str(raw_path),
        "crop_rows": crop_stats["kept_rows"],
        "unique_mmsi": crop_stats["unique_mmsi"],
        "clean_rows": clean_stats["output_rows"],
        "tracks": feature_stats["unique_tracks"],
        "grid_cells": grid_stats["nonempty_cells"],
        "max_grid_count": grid_stats["max_cell_count"],
        "min_track_points": min_points,
        "tracks_min_points": tracks_stats["output_tracks"],
        "tracks_min_points_rows": tracks_stats["output_rows"],
    }
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run daily AIS processing pipeline.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        required=True,
        help="Bounding box in EPSG:4326.",
    )
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"), help="Raw data root.")
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=Path("data/processed"),
        help="Processed data output directory.",
    )
    parser.add_argument(
        "--tables-dir",
        type=Path,
        default=Path("outputs/tables"),
        help="Tables/statistics output directory.",
    )
    parser.add_argument("--summary-json", type=Path, required=True, help="Output range summary JSON.")
    parser.add_argument("--skip-download", action="store_true", help="Require raw files to already exist.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs.")
    parser.add_argument("--min-points", type=int, default=20, help="Minimum points for track filtering.")
    parser.add_argument("--cell-size-deg", type=float, default=0.005, help="Grid cell size.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summaries = []
    for day in iter_dates(args.start, args.end):
        summaries.append(
            run_day(
                day=day,
                bbox=tuple(args.bbox),
                raw_dir=args.raw_dir,
                processed_dir=args.processed_dir,
                tables_dir=args.tables_dir,
                skip_download=args.skip_download,
                overwrite=args.overwrite,
                min_points=args.min_points,
                cell_size_deg=args.cell_size_deg,
            )
        )

    total = {
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "days": len(summaries),
        "daily": summaries,
        "totals": {
            "crop_rows": sum(int(item["crop_rows"]) for item in summaries),
            "clean_rows": sum(int(item["clean_rows"]) for item in summaries),
            "tracks": sum(int(item["tracks"]) for item in summaries),
            "tracks_min_points": sum(int(item["tracks_min_points"]) for item in summaries),
            "tracks_min_points_rows": sum(int(item["tracks_min_points_rows"]) for item in summaries),
        },
    }
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(total, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("== range summary ==", flush=True)
    print(json.dumps(total["totals"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
