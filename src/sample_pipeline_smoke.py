#!/usr/bin/env python3
"""Run a tiny synthetic AIS pipeline smoke test.

This test exercises the basic local processing scripts without downloading
NOAA AIS files. It writes temporary outputs under tmp/repro_sample_smoke/.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BBOX = ["-122.62", "37.42", "-121.92", "38.18"]


def project_path(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def run_command(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return max(0, sum(1 for _ in csv.reader(handle)) - 1)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(args: argparse.Namespace) -> int:
    sample = project_path(args.sample)
    out_dir = project_path(args.output_dir)
    if args.clean_output and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    crop_csv = out_dir / "smoke_crop.csv"
    crop_stats = out_dir / "smoke_crop_stats.json"
    clean_csv = out_dir / "smoke_clean.csv"
    clean_stats = out_dir / "smoke_clean_stats.json"
    feature_csv = out_dir / "smoke_features.csv"
    feature_stats = out_dir / "smoke_features_stats.json"
    min_track_csv = out_dir / "smoke_tracks_min3.csv"
    min_track_stats = out_dir / "smoke_tracks_min3_stats.json"
    grid_csv = out_dir / "smoke_grid_density.csv"
    grid_stats = out_dir / "smoke_grid_density_stats.json"

    bbox = args.bbox or DEFAULT_BBOX
    python = sys.executable
    run_command(
        [
            python,
            "src/preprocess.py",
            "crop-bbox",
            "--input",
            str(sample),
            "--output",
            str(crop_csv),
            "--bbox",
            *bbox,
            "--stats-json",
            str(crop_stats),
        ]
    )
    run_command(
        [
            python,
            "src/clean.py",
            "--input",
            str(crop_csv),
            "--output",
            str(clean_csv),
            "--stats-json",
            str(clean_stats),
            "--bbox",
            *bbox,
        ]
    )
    run_command(
        [
            python,
            "src/features.py",
            "--input",
            str(clean_csv),
            "--output",
            str(feature_csv),
            "--stats-json",
            str(feature_stats),
        ]
    )
    run_command(
        [
            python,
            "src/filter_tracks.py",
            "--input",
            str(feature_csv),
            "--output",
            str(min_track_csv),
            "--stats-json",
            str(min_track_stats),
            "--min-points",
            "3",
        ]
    )
    run_command(
        [
            python,
            "src/grid_density.py",
            "--input",
            str(feature_csv),
            "--output-csv",
            str(grid_csv),
            "--stats-json",
            str(grid_stats),
            "--bbox",
            *bbox,
        ]
    )

    crop = read_json(crop_stats)
    clean = read_json(clean_stats)
    features = read_json(feature_stats)
    filtered = read_json(min_track_stats)
    grid = read_json(grid_stats)

    require(crop.get("kept_rows") == 12, f"expected 12 cropped sample rows, got {crop.get('kept_rows')}")
    require(csv_rows(clean_csv) == 12, f"expected 12 clean sample rows, got {csv_rows(clean_csv)}")
    require(csv_rows(feature_csv) == 12, f"expected 12 feature sample rows, got {csv_rows(feature_csv)}")
    require(filtered.get("output_tracks", 0) >= 2, "expected at least two retained sample tracks")
    require(filtered.get("output_rows", 0) == 12, f"expected 12 filtered rows, got {filtered.get('output_rows')}")
    require(grid.get("nonempty_cells", 0) > 0, "expected nonempty grid cells")

    summary = {
        "sample": str(sample.relative_to(ROOT)),
        "output_dir": str(out_dir.relative_to(ROOT)),
        "cropped_rows": crop.get("kept_rows"),
        "clean_rows": clean.get("output_rows", csv_rows(clean_csv)),
        "feature_rows": features.get("output_rows", csv_rows(feature_csv)),
        "retained_tracks": filtered.get("output_tracks"),
        "retained_rows": filtered.get("output_rows"),
        "grid_cells": grid.get("nonempty_cells"),
    }
    (out_dir / "sample_pipeline_smoke_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\nSample pipeline smoke: PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a tiny synthetic AIS pipeline smoke test.")
    parser.add_argument("--sample", default="data/sample/smoke_ais.csv", help="Synthetic AIS sample CSV.")
    parser.add_argument("--output-dir", default="tmp/repro_sample_smoke", help="Temporary output directory.")
    parser.add_argument("--bbox", nargs=4, default=None, help="Bounding box override.")
    parser.add_argument("--clean-output", action="store_true", help="Remove previous output directory first.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
