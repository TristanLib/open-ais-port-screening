#!/usr/bin/env python3
"""Extract representative anomaly track case studies for audit and Web use."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_CASE_REASONS = [
    "low_empirical_route_support",
    "direction_mismatch",
    "high_turn",
    "high_speed",
    "high_accel",
    "implied_speed",
    "suspicious_stop",
]


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


def group_anomalies(anomaly_csv: Path) -> dict[tuple[str, str], dict[str, object]]:
    groups: dict[tuple[str, str], dict[str, object]] = {}
    with anomaly_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            date = row["base_date_time"][:10]
            track_id = row["track_id"]
            key = (date, track_id)
            score = parse_float(row.get("anomaly_score")) or 0.0
            group = groups.setdefault(
                key,
                {
                    "date": date,
                    "track_id": track_id,
                    "mmsi": row.get("mmsi", ""),
                    "anomaly_count": 0,
                    "score_sum": 0.0,
                    "max_score": 0.0,
                    "reason_counts": Counter(),
                    "cell_counts": Counter(),
                },
            )
            group["anomaly_count"] = int(group["anomaly_count"]) + 1
            group["score_sum"] = float(group["score_sum"]) + score
            group["max_score"] = max(float(group["max_score"]), score)
            if row.get("cell_id"):
                group["cell_counts"][row["cell_id"]] += 1  # type: ignore[index]
            for reason in row.get("reasons", "").split(";"):
                if reason:
                    group["reason_counts"][reason] += 1  # type: ignore[index]
    return groups


def select_cases(
    groups: dict[tuple[str, str], dict[str, object]],
    reasons: list[str],
    max_cases: int,
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    selected_keys: set[tuple[str, str]] = set()
    selected_mmsi: set[str] = set()

    for reason in reasons:
        candidates = []
        for key, group in groups.items():
            reason_counts: Counter[str] = group["reason_counts"]  # type: ignore[assignment]
            count = reason_counts.get(reason, 0)
            if count <= 0:
                continue
            mean_score = float(group["score_sum"]) / int(group["anomaly_count"])
            candidates.append((count, mean_score, int(group["anomaly_count"]), key, group))
        if not candidates:
            continue
        candidates.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
        chosen: tuple[str, str] | None = None
        chosen_group: dict[str, object] | None = None
        for _, _, _, key, group in candidates:
            if key not in selected_keys and str(group.get("mmsi", "")) not in selected_mmsi:
                chosen = key
                chosen_group = group
                break
        if chosen is None:
            for _, _, _, key, group in candidates:
                if key not in selected_keys:
                    chosen = key
                    chosen_group = group
                    break
        if chosen is not None and chosen_group is not None:
            selected_keys.add(chosen)
            selected_mmsi.add(str(chosen_group.get("mmsi", "")))
            selected.append({"case_reason": reason, **chosen_group})
        if len(selected) >= max_cases:
            break

    if len(selected) < max_cases:
        remaining = []
        for key, group in groups.items():
            if key in selected_keys:
                continue
            mean_score = float(group["score_sum"]) / int(group["anomaly_count"])
            remaining.append((int(group["anomaly_count"]), mean_score, key, group))
        remaining.sort(reverse=True, key=lambda item: (item[0], item[1]))
        for _, _, key, group in remaining:
            if len(selected) >= max_cases:
                break
            if str(group.get("mmsi", "")) in selected_mmsi:
                continue
            selected_keys.add(key)
            selected_mmsi.add(str(group.get("mmsi", "")))
            reason_counts: Counter[str] = group["reason_counts"]  # type: ignore[assignment]
            fallback_reason = reason_counts.most_common(1)[0][0] if reason_counts else "mixed"
            selected.append({"case_reason": fallback_reason, **group})

    return selected[:max_cases]


def load_feature_rows(processed_dir: Path, selected: list[dict[str, object]]) -> dict[tuple[str, str], list[dict[str, str]]]:
    wanted_by_date: dict[str, set[str]] = defaultdict(set)
    for case in selected:
        wanted_by_date[str(case["date"])].add(str(case["track_id"]))

    rows_by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for date, track_ids in wanted_by_date.items():
        path = processed_dir / f"sf_bay_ais_{date}_features.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                track_id = row.get("track_id", "")
                if track_id in track_ids:
                    rows_by_key[(date, track_id)].append(row)
    for rows in rows_by_key.values():
        rows.sort(key=lambda item: item.get("base_date_time", ""))
    return rows_by_key


def summarize_track(rows: list[dict[str, str]]) -> dict[str, object]:
    sog_values = [value for value in (parse_float(row.get("sog")) for row in rows) if value is not None]
    coordinates = []
    for row in rows:
        lon = parse_float(row.get("longitude"))
        lat = parse_float(row.get("latitude"))
        if lon is not None and lat is not None:
            coordinates.append([lon, lat])
    return {
        "track_point_count": len(rows),
        "coordinate_count": len(coordinates),
        "start_time": rows[0].get("base_date_time") if rows else None,
        "end_time": rows[-1].get("base_date_time") if rows else None,
        "min_sog": min(sog_values) if sog_values else None,
        "mean_sog": sum(sog_values) / len(sog_values) if sog_values else None,
        "max_sog": max(sog_values) if sog_values else None,
        "coordinates": coordinates,
    }


def export_cases(
    selected: list[dict[str, object]],
    rows_by_key: dict[tuple[str, str], list[dict[str, str]]],
    output_geojson: Path,
    output_json: Path,
) -> dict[str, object]:
    output_geojson.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    features = []
    metadata_cases = []
    for index, case in enumerate(selected, start=1):
        date = str(case["date"])
        track_id = str(case["track_id"])
        rows = rows_by_key.get((date, track_id), [])
        summary = summarize_track(rows)
        coordinates = summary.pop("coordinates")
        reason_counts: Counter[str] = case["reason_counts"]  # type: ignore[assignment]
        cell_counts: Counter[str] = case["cell_counts"]  # type: ignore[assignment]
        mean_score = float(case["score_sum"]) / int(case["anomaly_count"])
        case_id = f"case_{index:02d}_{case['case_reason']}"
        properties = {
            "case_id": case_id,
            "case_reason": case["case_reason"],
            "date": date,
            "track_id": track_id,
            "mmsi": case["mmsi"],
            "anomaly_count": case["anomaly_count"],
            "mean_anomaly_score": round(mean_score, 6),
            "max_anomaly_score": round(float(case["max_score"]), 6),
            "reason_counts": dict(reason_counts),
            "top_cells": cell_counts.most_common(5),
            **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in summary.items()},
        }
        metadata_cases.append(properties)
        if len(coordinates) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                    "properties": properties,
                }
            )

    output_geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    output_json.write_text(json.dumps({"cases": metadata_cases}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"cases": len(metadata_cases), "geojson_features": len(features)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract representative anomaly case tracks.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"), help="Processed data directory.")
    parser.add_argument("--anomaly-csv", type=Path, required=True, help="Anomaly points CSV.")
    parser.add_argument("--output-geojson", type=Path, required=True, help="Output case track GeoJSON.")
    parser.add_argument("--output-json", type=Path, required=True, help="Output case metadata JSON.")
    parser.add_argument("--max-cases", type=int, default=7, help="Maximum number of cases.")
    parser.add_argument("--reasons", nargs="*", default=DEFAULT_CASE_REASONS, help="Preferred case reasons.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    groups = group_anomalies(args.anomaly_csv)
    selected = select_cases(groups, args.reasons, args.max_cases)
    rows_by_key = load_feature_rows(args.processed_dir, selected)
    result = export_cases(selected, rows_by_key, args.output_geojson, args.output_json)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
