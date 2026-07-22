#!/usr/bin/env python3
"""Compute daily hotspot stability for density, anomaly, encounter, and fused risk."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from risk_hotspots import calculate_cell_components


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def parse_float(value: str | None) -> float:
    if value is None or value == "":
        return 0.0
    try:
        parsed = float(value)
    except ValueError:
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def cell_id_for_point(
    lon: float,
    lat: float,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> str | None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
        return None
    col = int((lon - min_lon) / cell_size_deg)
    row = int((lat - min_lat) / cell_size_deg)
    max_col = int((max_lon - min_lon) / cell_size_deg)
    max_row = int((max_lat - min_lat) / cell_size_deg)
    return f"r{min(row, max_row)}_c{min(col, max_col)}"


def load_daily_exposure(
    start: dt.date,
    end: dt.date,
    tables_dir: Path,
    dataset_prefix: str,
) -> dict[str, dict[str, dict[str, float]]]:
    exposure: dict[str, dict[str, dict[str, float]]] = {}
    for day in iter_dates(start, end):
        date_key = day.isoformat()
        exposure[date_key] = {}
        path = tables_dir / f"{dataset_prefix}_{date_key}_grid_density.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                exposure[date_key][row["cell_id"]] = {
                    "point_count": parse_float(row.get("point_count")),
                    "moving_count": parse_float(row.get("moving_count")),
                    "stationary_count": parse_float(row.get("stationary_count")),
                }
    return exposure


def load_daily_anomalies(
    anomaly_csv: Path,
    low_support_weight: float,
) -> dict[str, dict[str, dict[str, float]]]:
    data: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    with anomaly_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            date_key = row["base_date_time"][:10]
            cell_id = row["cell_id"]
            record = data[date_key].setdefault(
                cell_id,
                {"count": 0.0, "score_sum": 0.0, "weighted_count": 0.0, "weighted_score_sum": 0.0},
            )
            weight = low_support_weight if row.get("evidence_tier") == "low_support_only" else 1.0
            record["count"] += 1
            record["score_sum"] += parse_float(row.get("anomaly_score"))
            record["weighted_count"] += weight
            record["weighted_score_sum"] += parse_float(row.get("anomaly_score")) * weight
    return data


def load_daily_pair_opportunities(path: Path) -> dict[str, dict[str, int]]:
    data: dict[str, dict[str, int]] = defaultdict(dict)
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date_key = row.get("date", "")
            if not date_key:
                continue
            cell_id = row["cell_id"]
            data[date_key][cell_id] = data[date_key].get(cell_id, 0) + int(
                parse_float(row.get("pair_opportunity_count"))
            )
    return data


def load_daily_encounters(
    encounter_csv: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> dict[str, dict[str, dict[str, float]]]:
    data: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    with encounter_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lon_mid = (parse_float(row.get("lon_a")) + parse_float(row.get("lon_b"))) / 2
            lat_mid = (parse_float(row.get("lat_a")) + parse_float(row.get("lat_b"))) / 2
            cell_id = cell_id_for_point(lon_mid, lat_mid, bbox, cell_size_deg)
            if cell_id is None:
                continue
            date_key = row["date"]
            record = data[date_key].setdefault(cell_id, {"count": 0.0, "score_sum": 0.0})
            record["count"] += 1
            record["score_sum"] += parse_float(row.get("encounter_risk_score"))
    return data


def jaccard(a: set[str], b: set[str]) -> float | None:
    if not a and not b:
        return None
    union = a | b
    if not union:
        return None
    return len(a & b) / len(union)


def score_cells_for_day(
    date_key: str,
    exposure: dict[str, dict[str, dict[str, float]]],
    anomalies: dict[str, dict[str, dict[str, float]]],
    encounters: dict[str, dict[str, dict[str, float]]],
    pair_opportunities: dict[str, dict[str, int]],
    encounter_weight: float,
    min_exposure_points: int,
    min_moving_points: int,
    min_anomaly_count: int,
    min_encounter_count: int,
) -> dict[str, list[tuple[str, float]]]:
    day_exposure = exposure.get(date_key, {})
    day_anomaly = anomalies.get(date_key, {})
    day_encounter = encounters.get(date_key, {})
    day_opportunities = pair_opportunities.get(date_key, {})
    cell_ids = set(day_exposure) | set(day_anomaly) | set(day_encounter) | set(day_opportunities)
    variants: dict[str, list[tuple[str, float]]] = {
        "density_only": [],
        "anomaly_only": [],
        "encounter_only": [],
        "fused_risk": [],
    }

    for cell_id in cell_ids:
        exp = day_exposure.get(cell_id, {})
        point_count = exp.get("point_count", 0.0)
        moving_count = exp.get("moving_count", 0.0)
        anomaly = day_anomaly.get(cell_id, {})
        encounter = day_encounter.get(cell_id, {})
        anomaly_count = anomaly.get("count", 0.0)
        encounter_count = encounter.get("count", 0.0)
        components = calculate_cell_components(
            anomaly_count=int(anomaly_count),
            weighted_anomaly_count=anomaly.get("weighted_count", 0.0),
            weighted_anomaly_score_sum=anomaly.get("weighted_score_sum", 0.0),
            point_count=point_count,
            moving_count=moving_count,
            encounter_count=int(encounter_count),
            encounter_score_sum=encounter.get("score_sum", 0.0),
            pair_opportunity_count=day_opportunities.get(cell_id, 0),
        )
        anomaly_component = components["anomaly_component"]
        encounter_component = components["encounter_component"]

        if point_count >= min_exposure_points:
            variants["density_only"].append((cell_id, point_count))

        if point_count >= min_exposure_points and moving_count >= min_moving_points:
            if anomaly_count >= min_anomaly_count:
                variants["anomaly_only"].append((cell_id, anomaly_component))
            if encounter_count >= min_encounter_count:
                variants["encounter_only"].append((cell_id, encounter_component))
            if anomaly_count >= min_anomaly_count or encounter_count >= min_encounter_count:
                variants["fused_risk"].append((cell_id, anomaly_component + encounter_weight * encounter_component))

    for key, values in variants.items():
        values.sort(key=lambda item: item[1], reverse=True)
    return variants


def summarize_variant(
    variant: str,
    daily_sets: list[dict[str, Any]],
) -> dict[str, Any]:
    previous = [item["overlap_with_previous"] for item in daily_sets if item["overlap_with_previous"] is not None]
    first = [item["overlap_with_first"] for item in daily_sets[1:] if item["overlap_with_first"] is not None]
    return {
        "variant": variant,
        "mean_previous_day_jaccard": round(mean(previous), 6) if previous else None,
        "min_previous_day_jaccard": round(min(previous), 6) if previous else None,
        "max_previous_day_jaccard": round(max(previous), 6) if previous else None,
        "mean_vs_first_day_jaccard": round(mean(first), 6) if first else None,
    }


def compute_stability(
    start: dt.date,
    end: dt.date,
    tables_dir: Path,
    anomaly_csv: Path,
    encounter_csv: Path,
    pair_opportunity_csv: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    top_k: int,
    encounter_weight: float,
    min_exposure_points: int,
    min_moving_points: int,
    min_anomaly_count: int,
    min_encounter_count: int,
    low_support_weight: float = 0.25,
    dataset_prefix: str = "sf_bay_ais",
) -> dict[str, Any]:
    exposure = load_daily_exposure(start, end, tables_dir, dataset_prefix)
    anomalies = load_daily_anomalies(anomaly_csv, low_support_weight)
    encounters = load_daily_encounters(encounter_csv, bbox, cell_size_deg)
    pair_opportunities = load_daily_pair_opportunities(pair_opportunity_csv)

    by_variant: dict[str, list[dict[str, Any]]] = {
        "density_only": [],
        "anomaly_only": [],
        "encounter_only": [],
        "fused_risk": [],
    }
    first_sets: dict[str, set[str]] = {}
    previous_sets: dict[str, set[str]] = {}

    for day in iter_dates(start, end):
        date_key = day.isoformat()
        scored = score_cells_for_day(
            date_key,
            exposure,
            anomalies,
            encounters,
            pair_opportunities,
            encounter_weight,
            min_exposure_points,
            min_moving_points,
            min_anomaly_count,
            min_encounter_count,
        )
        for variant, values in scored.items():
            top_values = values[:top_k]
            top_cells = {cell_id for cell_id, _ in top_values}
            if variant not in first_sets:
                first_sets[variant] = set(top_cells)
            daily_record = {
                "date": date_key,
                "variant": variant,
                "top_k": top_k,
                "available_cells": len(values),
                "selected_cells": len(top_cells),
                "overlap_with_first": round(jaccard(top_cells, first_sets[variant]) or 0.0, 6),
                "overlap_with_previous": None
                if variant not in previous_sets
                else round(jaccard(top_cells, previous_sets[variant]) or 0.0, 6),
                "top_cells": [
                    {"cell_id": cell_id, "score": round(score, 8)}
                    for cell_id, score in top_values[:10]
                ],
            }
            by_variant[variant].append(daily_record)
            previous_sets[variant] = set(top_cells)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "top_k": top_k,
        "parameters": {
            "cell_size_deg": cell_size_deg,
            "encounter_weight": encounter_weight,
            "min_exposure_points": min_exposure_points,
            "min_moving_points": min_moving_points,
            "min_anomaly_count": min_anomaly_count,
            "min_encounter_count": min_encounter_count,
            "low_support_weight": low_support_weight,
            "encounter_rate_denominator": "evaluated vessel-pair opportunities",
        },
        "summary": [summarize_variant(variant, rows) for variant, rows in by_variant.items()],
        "daily": by_variant,
    }


def write_markdown(path: Path, stats: dict[str, Any]) -> None:
    lines = [
        "# Hotspot Stability",
        "",
        f"Period: {stats['start']} to {stats['end']}",
        f"Top-K: {stats['top_k']}",
        "",
        "| Variant | Mean adjacent-day Jaccard | Min | Max | Mean vs first day |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in stats["summary"]:
        def fmt(value: Any) -> str:
            return "-" if value is None else f"{value:.4f}"
        lines.append(
            f"| {row['variant']} | {fmt(row['mean_previous_day_jaccard'])} | "
            f"{fmt(row['min_previous_day_jaccard'])} | {fmt(row['max_previous_day_jaccard'])} | "
            f"{fmt(row['mean_vs_first_day_jaccard'])} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute daily hotspot stability across risk variants.")
    parser.add_argument("--start", type=parse_date, required=True)
    parser.add_argument("--end", type=parse_date, required=True)
    parser.add_argument("--tables-dir", type=Path, default=Path("outputs/tables"))
    parser.add_argument("--anomaly-csv", type=Path, required=True)
    parser.add_argument("--encounter-csv", type=Path, required=True)
    parser.add_argument("--pair-opportunity-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"), required=True)
    parser.add_argument("--cell-size-deg", type=float, default=0.005)
    parser.add_argument("--top-k", type=int, default=25)
    parser.add_argument("--encounter-weight", type=float, default=0.75)
    parser.add_argument("--min-exposure-points", type=int, default=20)
    parser.add_argument("--min-moving-points", type=int, default=5)
    parser.add_argument("--min-anomaly-count", type=int, default=3)
    parser.add_argument("--min-encounter-count", type=int, default=3)
    parser.add_argument("--low-support-weight", type=float, default=0.25)
    parser.add_argument("--dataset-prefix", default="sf_bay_ais")
    args = parser.parse_args()

    stats = compute_stability(
        args.start,
        args.end,
        args.tables_dir,
        args.anomaly_csv,
        args.encounter_csv,
        args.pair_opportunity_csv,
        tuple(args.bbox),  # type: ignore[arg-type]
        args.cell_size_deg,
        args.top_k,
        args.encounter_weight,
        args.min_exposure_points,
        args.min_moving_points,
        args.min_anomaly_count,
        args.min_encounter_count,
        args.low_support_weight,
        args.dataset_prefix,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(args.output_md, stats)
    print(json.dumps(stats["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
