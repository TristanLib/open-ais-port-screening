#!/usr/bin/env python3
"""Run lightweight out-of-sample and threshold-sensitivity checks.

The workflow uses interpretable screening rules rather than a supervised
classifier. These checks report stability of candidate counts and rates, not
classification accuracy against incident labels.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from anomaly_score import direction_bin_delta, score_row
from traffic_patterns import DIRECTION_LABELS, cell_for, direction_bin, quantile


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


def learn_pattern_rows(
    dates: list[dt.date],
    processed_dir: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    min_sog: float,
    min_points: int,
    min_tracks: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    cells: dict[tuple[int, int], dict[str, object]] = {}
    counters: Counter[str] = Counter()

    for day in dates:
        path = processed_dir / f"sf_bay_ais_{day.isoformat()}_tracks_min20.csv"
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
                bearing = parse_float(row.get("bearing_deg")) or parse_float(row.get("cog"))
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
    min_lon, min_lat, _, _ = bbox
    for (col, row), value in cells.items():
        moving_points = int(value["moving_points"])
        tracks = len(value["track_id"])  # type: ignore[arg-type]
        mmsi = len(value["mmsi"])  # type: ignore[arg-type]
        direction_counts: Counter[int] = value["direction_bins"]  # type: ignore[assignment]
        dominant_bin, dominant_count = direction_counts.most_common(1)[0]
        dominance_ratio = dominant_count / moving_points if moving_points else 0.0
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
        "nonempty_moving_cells": len(rows),
        "normal_route_cells": sum(int(item["is_normal_route_cell"]) for item in rows),
        "high_confidence_route_cells": sum(int(item["is_high_confidence_route_cell"]) for item in rows),
        "cell_size_deg": cell_size_deg,
        "normal_threshold": normal_threshold,
        "high_threshold": high_threshold,
        "min_tracks": min_tracks,
    }
    return rows, stats


def patterns_from_rows(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
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


def score_dates(
    dates: list[dt.date],
    processed_dir: Path,
    patterns: dict[str, dict[str, object]],
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    moving_sog: float,
    thresholds: list[float],
) -> dict[str, Any]:
    counts = {f"{threshold:.2f}": 0 for threshold in thresholds}
    reason_counts: Counter[str] = Counter()
    scored_rows = 0
    skipped_rows = 0

    for day in dates:
        path = processed_dir / f"sf_bay_ais_{day.isoformat()}_features.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                scored = score_row(row, patterns, bbox, cell_size_deg, moving_sog)
                if scored is None:
                    skipped_rows += 1
                    continue
                scored_rows += 1
                score = float(scored["anomaly_score"])
                for threshold in thresholds:
                    if score >= threshold:
                        counts[f"{threshold:.2f}"] += 1
                if score >= 0.35:
                    for reason in str(scored["reasons"]).split(";"):
                        if reason:
                            reason_counts[reason] += 1

    rates = {
        key: round(value / scored_rows * 100, 6) if scored_rows else 0.0
        for key, value in counts.items()
    }
    return {
        "scored_rows": scored_rows,
        "skipped_rows": skipped_rows,
        "candidate_counts": counts,
        "candidate_rates_percent": rates,
        "reason_counts_at_0.35": dict(reason_counts),
    }


def nested_encounter_sensitivity(encounter_csv: Path) -> list[dict[str, Any]]:
    thresholds = [(0.25, 5.0), (0.25, 10.0), (0.5, 10.0), (0.5, 15.0)]
    counts = Counter({f"{dcpa:.2f}/{tcpa:.0f}": 0 for dcpa, tcpa in thresholds})
    with encounter_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            dcpa = float(row["dcpa_nm"])
            tcpa = float(row["tcpa_min"])
            for threshold_dcpa, threshold_tcpa in thresholds:
                if dcpa <= threshold_dcpa and tcpa <= threshold_tcpa:
                    counts[f"{threshold_dcpa:.2f}/{threshold_tcpa:.0f}"] += 1
    return [
        {
            "dcpa_threshold_nm": dcpa,
            "tcpa_threshold_min": tcpa,
            "encounter_candidates": counts[f"{dcpa:.2f}/{tcpa:.0f}"],
        }
        for dcpa, tcpa in thresholds
    ]


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    lodo = result["leave_one_day_out"]
    sensitivity = result["sensitivity"]
    lines = [
        "# Leave-one-day-out and threshold sensitivity checks",
        "",
        "## Leave-one-day-out traffic-pattern test",
        "",
        "| Held-out day | Train days | Normal cells | Anomaly candidates | Anomaly rate (%) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in lodo["daily"]:
        lines.append(
            f"| {row['test_date']} | {row['training_days']} | {row['normal_route_cells']:,} | "
            f"{row['anomaly_candidates']:,} | {row['anomaly_rate_percent']:.4f} |"
        )
    lines.extend(
        [
            "",
            f"Mean held-out anomaly rate: {lodo['summary']['mean_anomaly_rate_percent']:.4f}% "
            f"(range {lodo['summary']['min_anomaly_rate_percent']:.4f}-{lodo['summary']['max_anomaly_rate_percent']:.4f}%).",
            "",
            "## Anomaly threshold sensitivity",
            "",
            "| Threshold | Candidate points | Candidate rate (%) |",
            "| ---: | ---: | ---: |",
        ]
    )
    for row in sensitivity["anomaly_thresholds"]:
        lines.append(f"| {row['threshold']:.2f} | {row['candidate_points']:,} | {row['candidate_rate_percent']:.4f} |")
    lines.extend(
        [
            "",
            "## Grid-size sensitivity",
            "",
            "| Cell size (deg) | Normal cells | High-confidence cells | Candidate points | Candidate rate (%) |",
            "| ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in sensitivity["grid_sizes"]:
        lines.append(
            f"| {row['cell_size_deg']:.3f} | {row['normal_route_cells']:,} | {row['high_confidence_route_cells']:,} | "
            f"{row['anomaly_candidates']:,} | {row['anomaly_rate_percent']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Nested CPA/TCPA threshold sensitivity",
            "",
            "| DCPA threshold (nm) | TCPA threshold (min) | Encounter candidates |",
            "| ---: | ---: | ---: |",
        ]
    )
    for row in sensitivity["encounter_thresholds"]:
        lines.append(
            f"| {row['dcpa_threshold_nm']:.2f} | {row['tcpa_threshold_min']:.0f} | {row['encounter_candidates']:,} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_validation(
    start: dt.date,
    end: dt.date,
    processed_dir: Path,
    encounter_csv: Path,
    output_json: Path,
    output_md: Path,
    bbox: tuple[float, float, float, float],
    moving_sog: float,
    min_sog: float,
    min_points: int,
    min_tracks: int,
) -> dict[str, Any]:
    dates = iter_dates(start, end)
    lodo_rows = []
    for heldout in dates:
        train_dates = [day for day in dates if day != heldout]
        pattern_rows, pattern_stats = learn_pattern_rows(
            train_dates, processed_dir, bbox, 0.005, min_sog, min_points, min_tracks
        )
        scoring = score_dates(
            [heldout], processed_dir, patterns_from_rows(pattern_rows), bbox, 0.005, moving_sog, [0.35]
        )
        anomaly_candidates = scoring["candidate_counts"]["0.35"]
        anomaly_rate = scoring["candidate_rates_percent"]["0.35"]
        lodo_rows.append(
            {
                "test_date": heldout.isoformat(),
                "training_days": len(train_dates),
                "normal_route_cells": pattern_stats["normal_route_cells"],
                "high_confidence_route_cells": pattern_stats["high_confidence_route_cells"],
                "scored_rows": scoring["scored_rows"],
                "anomaly_candidates": anomaly_candidates,
                "anomaly_rate_percent": anomaly_rate,
            }
        )

    full_pattern_rows, full_pattern_stats = learn_pattern_rows(
        dates, processed_dir, bbox, 0.005, min_sog, min_points, min_tracks
    )
    threshold_scoring = score_dates(
        dates,
        processed_dir,
        patterns_from_rows(full_pattern_rows),
        bbox,
        0.005,
        moving_sog,
        [0.30, 0.35, 0.40, 0.45],
    )
    anomaly_thresholds = [
        {
            "threshold": float(key),
            "candidate_points": threshold_scoring["candidate_counts"][key],
            "candidate_rate_percent": threshold_scoring["candidate_rates_percent"][key],
        }
        for key in ["0.30", "0.35", "0.40", "0.45"]
    ]

    grid_rows = []
    for cell_size in [0.003, 0.005, 0.010]:
        pattern_rows, pattern_stats = learn_pattern_rows(
            dates, processed_dir, bbox, cell_size, min_sog, min_points, min_tracks
        )
        scoring = score_dates(
            dates, processed_dir, patterns_from_rows(pattern_rows), bbox, cell_size, moving_sog, [0.35]
        )
        grid_rows.append(
            {
                "cell_size_deg": cell_size,
                "normal_route_cells": pattern_stats["normal_route_cells"],
                "high_confidence_route_cells": pattern_stats["high_confidence_route_cells"],
                "anomaly_candidates": scoring["candidate_counts"]["0.35"],
                "anomaly_rate_percent": scoring["candidate_rates_percent"]["0.35"],
            }
        )

    lodo_rates = [float(row["anomaly_rate_percent"]) for row in lodo_rows]
    lodo_summary = {
        "mean_anomaly_rate_percent": round(mean(lodo_rates), 6),
        "min_anomaly_rate_percent": round(min(lodo_rates), 6),
        "max_anomaly_rate_percent": round(max(lodo_rates), 6),
        "mean_normal_route_cells": round(mean(float(row["normal_route_cells"]) for row in lodo_rows), 2),
    }
    result = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "leave_one_day_out": {
            "description": "For each held-out day, traffic patterns are learned from the other six days and anomaly scoring is run only on the held-out day.",
            "daily": lodo_rows,
            "summary": lodo_summary,
        },
        "sensitivity": {
            "full_pattern_stats": full_pattern_stats,
            "anomaly_thresholds": anomaly_thresholds,
            "grid_sizes": grid_rows,
            "encounter_thresholds": nested_encounter_sensitivity(encounter_csv),
            "encounter_note": "Encounter thresholds are nested counts from the baseline DCPA<=0.5 nm and TCPA<=15 min candidate file.",
        },
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(output_md, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run leave-one-day-out and threshold sensitivity checks.")
    parser.add_argument("--start", type=parse_date, required=True)
    parser.add_argument("--end", type=parse_date, required=True)
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    parser.add_argument(
        "--encounter-csv",
        type=Path,
        default=Path("outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounters.csv"),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_validation_sensitivity.json"),
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=Path("outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_validation_sensitivity.md"),
    )
    parser.add_argument("--bbox", nargs=4, type=float, required=True)
    parser.add_argument("--moving-sog", type=float, default=1.0)
    parser.add_argument("--min-sog", type=float, default=1.0)
    parser.add_argument("--min-points", type=int, default=50)
    parser.add_argument("--min-tracks", type=int, default=5)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_validation(
        start=args.start,
        end=args.end,
        processed_dir=args.processed_dir,
        encounter_csv=args.encounter_csv,
        output_json=args.output_json,
        output_md=args.output_md,
        bbox=tuple(args.bbox),
        moving_sog=args.moving_sog,
        min_sog=args.min_sog,
        min_points=args.min_points,
        min_tracks=args.min_tracks,
    )
    print(json.dumps(result["leave_one_day_out"]["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
