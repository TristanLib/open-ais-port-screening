#!/usr/bin/env python3
"""Sanitize web-ready GeoJSON for public research-demo deployment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {
    "mmsi",
    "mmsi_a",
    "mmsi_b",
    "track_id",
    "track_id_a",
    "track_id_b",
    "base_date_time",
    "time_bin",
    "start_time",
    "end_time",
    "reference_time",
    "timestamp_a",
    "timestamp_b",
    "lon_a",
    "lat_a",
    "lon_b",
    "lat_b",
    "bearing_a",
    "bearing_b",
    "sog_a",
    "sog_b",
}

KEY_RENAMES = {
    "risk_score": "screening_score",
    "anomaly_score": "screening_score",
    "encounter_risk_score": "screening_score",
    "mean_anomaly_score": "mean_screening_score",
    "max_anomaly_score": "max_screening_score",
    "mean_encounter_score": "mean_encounter_evidence_score",
    "max_encounter_score": "max_encounter_evidence_score",
    "unique_mmsi": "unique_vessels",
    "unique_tracks": "unique_track_count",
    "unique_mmsi_day_sum": "unique_vessel_day_sum",
    "unique_tracks_day_sum": "unique_track_day_sum",
    "unique_anomaly_mmsi": "unique_anomaly_vessels",
    "unique_anomaly_tracks": "unique_anomaly_track_count",
}

MANIFEST_VALUE_PROPERTIES = {
    "anomaly_points": "screening_score",
    "risk_hotspots": "screening_score",
    "encounter_points": "screening_score",
    "fused_risk_hotspots": "screening_score",
    "case_tracks": "mean_screening_score",
}

MANIFEST_LABELS = {
    "anomaly_points": "Anomaly candidate points",
    "risk_hotspots": "Anomaly-only evidence hotspot cells",
    "encounter_points": "CPA/TCPA future encounter candidate records",
    "fused_risk_hotspots": "Fused first-pass evidence hotspot cells",
    "case_tracks": "Representative de-identified case tracks",
}

DISCLAIMER = (
    "Historical AIS first-pass screening evidence demo. Not real-time, not for navigation, not for "
    "enforcement, and not for operational decision-making."
)


def round_coordinates(value: Any, digits: int) -> Any:
    if isinstance(value, list):
        return [round_coordinates(item, digits) for item in value]
    if isinstance(value, float):
        return round(value, digits)
    return value


def simplify_line_coordinates(coordinates: list[Any], max_points: int) -> list[Any]:
    if len(coordinates) <= max_points:
        return coordinates
    if max_points < 2:
        return coordinates[:max_points]
    last = len(coordinates) - 1
    indexes = sorted({round(i * last / (max_points - 1)) for i in range(max_points)})
    return [coordinates[index] for index in indexes]


def sanitize_properties(properties: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    date_value = properties.get("date")
    if not date_value:
        for key in ("base_date_time", "time_bin", "start_time"):
            raw = properties.get(key)
            if isinstance(raw, str) and len(raw) >= 10:
                date_value = raw[:10]
                break
    if date_value:
        sanitized["date"] = date_value

    for key, value in properties.items():
        if key in SENSITIVE_KEYS:
            continue
        target_key = KEY_RENAMES.get(key, key)
        if target_key in sanitized and target_key == "date":
            continue
        sanitized[target_key] = value
    return sanitized


def sanitize_geojson(path: Path, coordinate_digits: int, max_case_points: int) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for feature in data.get("features", []):
        geometry = feature.get("geometry") or {}
        properties = feature.get("properties") or {}
        if geometry.get("type") == "LineString":
            geometry["coordinates"] = simplify_line_coordinates(
                geometry.get("coordinates", []),
                max_case_points,
            )
            properties["coordinate_count"] = len(geometry.get("coordinates", []))
        geometry["coordinates"] = round_coordinates(geometry.get("coordinates"), coordinate_digits)
        feature["geometry"] = geometry
        feature["properties"] = sanitize_properties(properties)

    data["metadata"] = {
        "publication_mode": "sanitized_research_demo",
        "disclaimer": DISCLAIMER,
        "coordinate_precision_decimals": coordinate_digits,
    }
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")


def sanitize_manifest(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["publication_mode"] = "sanitized_research_demo"
    data["disclaimer"] = DISCLAIMER
    for layer in data.get("layers", []):
        layer_id = layer.get("id")
        if layer_id in MANIFEST_VALUE_PROPERTIES:
            layer["value_property"] = MANIFEST_VALUE_PROPERTIES[layer_id]
        if layer_id in MANIFEST_LABELS:
            layer["label"] = MANIFEST_LABELS[layer_id]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sanitize_summary(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["publication_mode"] = "sanitized_research_demo"
    data["disclaimer"] = DISCLAIMER
    for item in data.get("ablation", []):
        if item.get("variant") == "Encounter-only" and "result" in item:
            item["result"] = str(item["result"]).replace("CPA/TCPA candidates", "CPA/TCPA candidate records")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sanitize_dir(web_dir: Path, coordinate_digits: int, max_case_points: int) -> None:
    for path in sorted(web_dir.glob("*.geojson")):
        sanitize_geojson(path, coordinate_digits, max_case_points)
    manifest = web_dir / "manifest.json"
    if manifest.exists():
        sanitize_manifest(manifest)
    summary = web_dir / "summary.json"
    if summary.exists():
        sanitize_summary(summary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanitize public web GeoJSON/JSON outputs.")
    parser.add_argument("web_dirs", nargs="*", type=Path, default=[Path("web/data")])
    parser.add_argument("--coordinate-digits", type=int, default=4)
    parser.add_argument("--max-case-points", type=int, default=80)
    args = parser.parse_args()

    for web_dir in args.web_dirs:
        sanitize_dir(web_dir, args.coordinate_digits, args.max_case_points)
        print(web_dir)


if __name__ == "__main__":
    main()
