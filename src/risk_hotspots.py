#!/usr/bin/env python3
"""Build screening hotspot cells from anomaly and encounter-candidate evidence."""

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


def polygon_for_cell(cell_id: str, bbox: tuple[float, float, float, float], cell_size_deg: float) -> list[list[float]]:
    row_part, col_part = cell_id.split("_")
    row = int(row_part[1:])
    col = int(col_part[1:])
    min_lon, min_lat, _, _ = bbox
    west = min_lon + col * cell_size_deg
    south = min_lat + row * cell_size_deg
    east = west + cell_size_deg
    north = south + cell_size_deg
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


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


def load_exposure(
    start: dt.date,
    end: dt.date,
    tables_dir: Path,
    dataset_prefix: str = "sf_bay_ais",
) -> dict[str, dict[str, float]]:
    exposure: dict[str, dict[str, float]] = {}
    for day in iter_dates(start, end):
        path = tables_dir / f"{dataset_prefix}_{day.isoformat()}_grid_density.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                cell_id = row["cell_id"]
                record = exposure.setdefault(
                    cell_id,
                    {
                        "point_count": 0.0,
                        "unique_mmsi_sum": 0.0,
                        "unique_tracks_sum": 0.0,
                        "moving_count": 0.0,
                        "stationary_count": 0.0,
                        "high_speed_count": 0.0,
                    },
                )
                for key in record:
                    record[key] += float(row.get(key, 0) or 0)
    return exposure


def load_encounters(
    encounter_csv: Path | None,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> dict[str, dict[str, float]]:
    if encounter_csv is None:
        return {}
    encounters: dict[str, dict[str, float]] = {}
    with encounter_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lon_a = float(row["lon_a"])
            lat_a = float(row["lat_a"])
            lon_b = float(row["lon_b"])
            lat_b = float(row["lat_b"])
            lon_mid = (lon_a + lon_b) / 2
            lat_mid = (lat_a + lat_b) / 2
            cell_id = cell_id_for_point(lon_mid, lat_mid, bbox, cell_size_deg)
            if cell_id is None:
                continue
            record = encounters.setdefault(
                cell_id,
                {
                    "encounter_count": 0.0,
                    "encounter_score_sum": 0.0,
                    "max_encounter_score": 0.0,
                },
            )
            score = float(row.get("encounter_risk_score", 0) or 0)
            record["encounter_count"] += 1
            record["encounter_score_sum"] += score
            record["max_encounter_score"] = max(record["max_encounter_score"], score)
    return encounters


def load_pair_opportunities(path: Path | None) -> dict[str, int]:
    if path is None:
        return {}
    opportunities: dict[str, int] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            cell_id = row["cell_id"]
            opportunities[cell_id] = opportunities.get(cell_id, 0) + int(
                float(row.get("pair_opportunity_count", 0) or 0)
            )
    return opportunities


def calculate_cell_components(
    *,
    anomaly_count: int,
    weighted_anomaly_count: float,
    weighted_anomaly_score_sum: float,
    point_count: float,
    moving_count: float,
    encounter_count: int,
    encounter_score_sum: float,
    pair_opportunity_count: int,
) -> dict[str, float]:
    """Compute dimensionally matched anomaly-state and vessel-pair evidence rates."""
    mean_anomaly_score = (
        weighted_anomaly_score_sum / weighted_anomaly_count if weighted_anomaly_count else 0.0
    )
    anomaly_rate = weighted_anomaly_count / moving_count if moving_count else 0.0
    mean_encounter_score = encounter_score_sum / encounter_count if encounter_count else 0.0
    encounter_rate = encounter_count / pair_opportunity_count if pair_opportunity_count else 0.0
    anomaly_component = (
        math.log1p(weighted_anomaly_count) * mean_anomaly_score * math.sqrt(anomaly_rate)
        if anomaly_count and anomaly_rate
        else 0.0
    )
    encounter_component = (
        math.log1p(encounter_count) * mean_encounter_score * math.sqrt(encounter_rate)
        if encounter_count and encounter_rate
        else 0.0
    )
    return {
        "mean_anomaly_score": mean_anomaly_score,
        "anomaly_rate": anomaly_rate,
        "mean_encounter_score": mean_encounter_score,
        "encounter_rate": encounter_rate,
        "anomaly_component": anomaly_component,
        "encounter_component": encounter_component,
    }


def build_hotspots(
    start: dt.date,
    end: dt.date,
    tables_dir: Path,
    anomaly_csv: Path,
    encounter_csv: Path | None,
    output_csv: Path,
    output_geojson: Path,
    stats_json: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    top_percent: float,
    min_exposure_points: int,
    min_moving_points: int,
    min_anomaly_count: int,
    min_encounter_count: int,
    encounter_weight: float,
    dataset_prefix: str = "sf_bay_ais",
    pair_opportunity_csv: Path | None = None,
    low_support_weight: float = 0.25,
) -> dict[str, object]:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_geojson.parent.mkdir(parents=True, exist_ok=True)
    stats_json.parent.mkdir(parents=True, exist_ok=True)

    exposure = load_exposure(start, end, tables_dir, dataset_prefix=dataset_prefix)
    encounters = load_encounters(encounter_csv, bbox, cell_size_deg)
    pair_opportunities = load_pair_opportunities(pair_opportunity_csv)
    anomaly: dict[str, dict[str, object]] = {}

    with anomaly_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            cell_id = row["cell_id"]
            score = parse_float(row.get("anomaly_score")) or 0.0
            record = anomaly.setdefault(
                cell_id,
                {
                    "anomaly_count": 0,
                    "score_sum": 0.0,
                    "weighted_anomaly_count": 0.0,
                    "weighted_score_sum": 0.0,
                    "corroborated_count": 0,
                    "low_support_only_count": 0,
                    "mmsi": set(),
                    "track_id": set(),
                    "reasons": Counter(),
                },
            )
            record["anomaly_count"] = int(record["anomaly_count"]) + 1
            record["score_sum"] = float(record["score_sum"]) + score
            evidence_tier = row.get("evidence_tier", "")
            if evidence_tier == "low_support_only":
                evidence_weight = low_support_weight
                record["low_support_only_count"] = int(record["low_support_only_count"]) + 1
            else:
                evidence_weight = 1.0
                if row.get("corroborated_candidate") == "1" or evidence_tier == "corroborated_candidate":
                    record["corroborated_count"] = int(record["corroborated_count"]) + 1
            record["weighted_anomaly_count"] = float(record["weighted_anomaly_count"]) + evidence_weight
            record["weighted_score_sum"] = float(record["weighted_score_sum"]) + score * evidence_weight
            if row.get("mmsi"):
                record["mmsi"].add(row["mmsi"])  # type: ignore[union-attr]
            if row.get("track_id"):
                record["track_id"].add(row["track_id"])  # type: ignore[union-attr]
            for reason in row.get("reasons", "").split(";"):
                if reason:
                    record["reasons"][reason] += 1  # type: ignore[index]

    rows: list[dict[str, object]] = []
    all_cell_ids = set(anomaly) | set(encounters) | set(pair_opportunities)
    for cell_id in all_cell_ids:
        record = anomaly.get(
            cell_id,
            {
                "anomaly_count": 0,
                "score_sum": 0.0,
                "weighted_anomaly_count": 0.0,
                "weighted_score_sum": 0.0,
                "corroborated_count": 0,
                "low_support_only_count": 0,
                "mmsi": set(),
                "track_id": set(),
                "reasons": Counter(),
            },
        )
        encounter = encounters.get(
            cell_id,
            {
                "encounter_count": 0.0,
                "encounter_score_sum": 0.0,
                "max_encounter_score": 0.0,
            },
        )
        exp = exposure.get(cell_id, {})
        point_count = float(exp.get("point_count", 0.0))
        moving_count = float(exp.get("moving_count", 0.0))
        anomaly_count = int(record["anomaly_count"])
        weighted_anomaly_count = float(record["weighted_anomaly_count"])
        weighted_score_sum = float(record["weighted_score_sum"])
        mean_score_all = float(record["score_sum"]) / anomaly_count if anomaly_count else 0.0
        encounter_count = int(encounter["encounter_count"])
        pair_opportunity_count = pair_opportunities.get(cell_id, 0)
        components = calculate_cell_components(
            anomaly_count=anomaly_count,
            weighted_anomaly_count=weighted_anomaly_count,
            weighted_anomaly_score_sum=weighted_score_sum,
            point_count=point_count,
            moving_count=moving_count,
            encounter_count=encounter_count,
            encounter_score_sum=float(encounter["encounter_score_sum"]),
            pair_opportunity_count=pair_opportunity_count,
        )
        anomaly_rate = components["anomaly_rate"]
        encounter_rate = components["encounter_rate"]
        anomaly_component = components["anomaly_component"]
        encounter_component = components["encounter_component"]
        raw_score = anomaly_component + encounter_weight * encounter_component
        reasons: Counter[str] = record["reasons"]  # type: ignore[assignment]
        dominant_reason, dominant_reason_count = reasons.most_common(1)[0] if reasons else ("", 0)
        rows.append(
            {
                "cell_id": cell_id,
                "anomaly_count": anomaly_count,
                "corroborated_anomaly_count": int(record["corroborated_count"]),
                "low_support_only_count": int(record["low_support_only_count"]),
                "weighted_anomaly_count": round(weighted_anomaly_count, 6),
                "mean_anomaly_score": round(mean_score_all, 6),
                "weighted_mean_anomaly_score": round(components["mean_anomaly_score"], 6),
                "point_count": int(point_count),
                "moving_count": int(moving_count),
                "anomaly_rate": round(anomaly_rate, 8),
                "encounter_count": encounter_count,
                "pair_opportunity_count": pair_opportunity_count,
                "mean_encounter_score": round(components["mean_encounter_score"], 6),
                "max_encounter_score": round(float(encounter["max_encounter_score"]), 6),
                "encounter_rate": round(encounter_rate, 8),
                "unique_anomaly_mmsi": len(record["mmsi"]),  # type: ignore[arg-type]
                "unique_anomaly_tracks": len(record["track_id"]),  # type: ignore[arg-type]
                "dominant_reason": dominant_reason,
                "dominant_reason_count": dominant_reason_count,
                "anomaly_component": round(anomaly_component, 8),
                "encounter_component": round(encounter_component, 8),
                "eligible_hotspot_cell": int(
                    point_count >= min_exposure_points
                    and moving_count >= min_moving_points
                    and (anomaly_count >= min_anomaly_count or encounter_count >= min_encounter_count)
                ),
                "raw_risk_score": raw_score,
            }
        )

    eligible_raw_scores = [
        float(row["raw_risk_score"]) for row in rows if int(row["eligible_hotspot_cell"])
    ]
    max_raw_score = max(eligible_raw_scores, default=max((float(row["raw_risk_score"]) for row in rows), default=0.0))
    for row in rows:
        row["risk_score"] = round(float(row["raw_risk_score"]) / max_raw_score, 6) if max_raw_score else 0.0
        del row["raw_risk_score"]

    rows.sort(key=lambda item: float(item["risk_score"]), reverse=True)
    eligible_rows = [item for item in rows if int(item["eligible_hotspot_cell"])]
    hotspot_count = max(1, int(len(eligible_rows) * top_percent)) if eligible_rows else 0
    hotspot_cells = {str(item["cell_id"]) for item in eligible_rows[:hotspot_count]}
    for row in rows:
        row["is_hotspot"] = 1 if str(row["cell_id"]) in hotspot_cells else 0

    rows.sort(
        key=lambda item: (
            int(item["is_hotspot"]),
            int(item["eligible_hotspot_cell"]),
            float(item["risk_score"]),
        ),
        reverse=True,
    )

    fieldnames = [
        "cell_id",
        "risk_score",
        "is_hotspot",
        "anomaly_count",
        "corroborated_anomaly_count",
        "low_support_only_count",
        "weighted_anomaly_count",
        "mean_anomaly_score",
        "weighted_mean_anomaly_score",
        "point_count",
        "moving_count",
        "anomaly_rate",
        "encounter_count",
        "pair_opportunity_count",
        "mean_encounter_score",
        "max_encounter_score",
        "encounter_rate",
        "unique_anomaly_mmsi",
        "unique_anomaly_tracks",
        "dominant_reason",
        "dominant_reason_count",
        "anomaly_component",
        "encounter_component",
        "eligible_hotspot_cell",
    ]
    with output_csv.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    features = []
    for row in rows:
        if not int(row["is_hotspot"]):
            continue
        properties = {key: row[key] for key in fieldnames}
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [polygon_for_cell(str(row["cell_id"]), bbox, cell_size_deg)]},
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
        "risk_cells": len(rows),
        "eligible_hotspot_cells": len(eligible_rows),
        "hotspot_cells": hotspot_count,
        "top_percent": top_percent,
        "min_exposure_points": min_exposure_points,
        "min_moving_points": min_moving_points,
        "min_anomaly_count": min_anomaly_count,
        "min_encounter_count": min_encounter_count,
        "encounter_weight": encounter_weight,
        "low_support_weight": low_support_weight,
        "rate_definitions": {
            "anomaly_rate": "weighted anomaly evidence / moving AIS states",
            "encounter_rate": "CPA/TCPA candidate records / evaluated vessel-pair opportunities",
        },
        "dataset_prefix": dataset_prefix,
        "encounter_csv": str(encounter_csv) if encounter_csv else None,
        "pair_opportunity_csv": str(pair_opportunity_csv) if pair_opportunity_csv else None,
        "top_cells": rows[:20],
        "top_hotspots": [item for item in rows if int(item["is_hotspot"])][:20],
        "output_csv": str(output_csv),
        "output_geojson": str(output_geojson),
    }
    stats_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build anomaly/encounter screening hotspot cells.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--tables-dir", type=Path, default=Path("outputs/tables"), help="Tables directory.")
    parser.add_argument("--anomaly-csv", type=Path, required=True, help="Anomaly points CSV.")
    parser.add_argument("--encounter-csv", type=Path, help="Optional CPA/TCPA encounter CSV.")
    parser.add_argument("--pair-opportunity-csv", type=Path, help="Cell-level evaluated vessel-pair exposure CSV.")
    parser.add_argument("--output-csv", type=Path, required=True, help="Output screening hotspot CSV.")
    parser.add_argument("--output-geojson", type=Path, required=True, help="Output hotspot GeoJSON.")
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
    parser.add_argument("--top-percent", type=float, default=0.10, help="Top fraction of risk cells marked as hotspots.")
    parser.add_argument("--min-exposure-points", type=int, default=100, help="Minimum exposure points for hotspot eligibility.")
    parser.add_argument("--min-moving-points", type=int, default=20, help="Minimum moving points for hotspot eligibility.")
    parser.add_argument("--min-anomaly-count", type=int, default=20, help="Minimum anomaly points for hotspot eligibility.")
    parser.add_argument("--min-encounter-count", type=int, default=20, help="Minimum encounter points for hotspot eligibility.")
    parser.add_argument("--encounter-weight", type=float, default=0.75, help="Encounter component weight.")
    parser.add_argument(
        "--low-support-weight",
        type=float,
        default=0.25,
        help="Weight assigned to low-route-support-only audit candidates.",
    )
    parser.add_argument("--dataset-prefix", default="sf_bay_ais", help="Daily grid-density file prefix.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = build_hotspots(
        start=args.start,
        end=args.end,
        tables_dir=args.tables_dir,
        anomaly_csv=args.anomaly_csv,
        encounter_csv=args.encounter_csv,
        output_csv=args.output_csv,
        output_geojson=args.output_geojson,
        stats_json=args.stats_json,
        bbox=tuple(args.bbox),
        cell_size_deg=args.cell_size_deg,
        top_percent=args.top_percent,
        min_exposure_points=args.min_exposure_points,
        min_moving_points=args.min_moving_points,
        min_anomaly_count=args.min_anomaly_count,
        min_encounter_count=args.min_encounter_count,
        encounter_weight=args.encounter_weight,
        dataset_prefix=args.dataset_prefix,
        pair_opportunity_csv=args.pair_opportunity_csv,
        low_support_weight=args.low_support_weight,
    )
    print(
        json.dumps(
            {
                "risk_cells": stats["risk_cells"],
                "eligible_hotspot_cells": stats["eligible_hotspot_cells"],
                "hotspot_cells": stats["hotspot_cells"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
