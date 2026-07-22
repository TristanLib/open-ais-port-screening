#!/usr/bin/env python3
"""Export lightweight web-ready GeoJSON layers and a manifest."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path


DISCLAIMER = (
    "Historical AIS first-pass screening evidence demo. Not real-time, not for "
    "navigation, not for enforcement, and not for operational decision-making."
)

BASEMAP = {
    "provider": "OpenStreetMap Standard",
    "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "attribution": "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors",
    "policy": "https://operations.osmfoundation.org/policies/tiles/",
    "deployment_note": (
        "For public deployment, keep visible attribution and valid browser Referer/caching behavior, "
        "or replace this URL with a hosted/commercial/self-hosted tile service."
    ),
}


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    parts = value.replace(",", " ").split()
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must contain four values: MIN_LON MIN_LAT MAX_LON MAX_LAT")
    try:
        min_lon, min_lat, max_lon, max_lat = [float(part) for part in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("bbox values must be numeric") from exc
    return min_lon, min_lat, max_lon, max_lat


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


def export_density_geojson(
    start: dt.date,
    end: dt.date,
    tables_dir: Path,
    output_geojson: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    dataset_prefix: str = "sf_bay_ais",
) -> dict[str, object]:
    cells: dict[str, dict[str, float]] = {}
    for day in iter_dates(start, end):
        path = tables_dir / f"{dataset_prefix}_{day.isoformat()}_grid_density.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                cell_id = row["cell_id"]
                record = cells.setdefault(
                    cell_id,
                    {
                        "point_count": 0.0,
                        "moving_count": 0.0,
                        "stationary_count": 0.0,
                        "high_speed_count": 0.0,
                        "unique_vessel_day_sum": 0.0,
                        "unique_track_day_sum": 0.0,
                    },
                )
                record["point_count"] += float(row["point_count"])
                record["moving_count"] += float(row["moving_count"])
                record["stationary_count"] += float(row["stationary_count"])
                record["high_speed_count"] += float(row["high_speed_count"])
                record["unique_vessel_day_sum"] += float(row["unique_mmsi"])
                record["unique_track_day_sum"] += float(row["unique_tracks"])

    max_count = max((value["point_count"] for value in cells.values()), default=1.0)
    features = []
    for cell_id, value in cells.items():
        properties = {
            "cell_id": cell_id,
            "point_count": int(value["point_count"]),
            "moving_count": int(value["moving_count"]),
            "stationary_count": int(value["stationary_count"]),
            "high_speed_count": int(value["high_speed_count"]),
            "unique_vessel_day_sum": int(value["unique_vessel_day_sum"]),
            "unique_track_day_sum": int(value["unique_track_day_sum"]),
            "density_norm": round(value["point_count"] / max_count, 6),
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [polygon_for_cell(cell_id, bbox, cell_size_deg)]},
                "properties": properties,
            }
        )

    features.sort(key=lambda item: item["properties"]["point_count"], reverse=True)
    output_geojson.parent.mkdir(parents=True, exist_ok=True)
    output_geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {"output_geojson": str(output_geojson), "features": len(features), "max_count": int(max_count)}


def write_manifest(
    output_path: Path,
    start: dt.date,
    end: dt.date,
    layers: list[dict[str, object]],
    *,
    dataset_id: str = "sf_bay",
    study_area: str = "San Francisco Bay and Port of Oakland Approaches",
    source: str = "NOAA MarineCadastre AIS 2025",
    workflow_role: str = "Primary open-data benchmark",
    map_center: tuple[float, float] = (37.84, -122.36),
    map_zoom: int = 10,
) -> None:
    manifest = {
        "project": "Open AIS Port Screening Explorer",
        "dataset_id": dataset_id,
        "study_area": study_area,
        "source": source,
        "workflow_role": workflow_role,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "map_view": {"center": list(map_center), "zoom": map_zoom},
        "publication_mode": "sanitized_research_demo",
        "disclaimer": DISCLAIMER,
        "basemap": BASEMAP,
        "companion_data": {
            "summary": "summary.json",
            "evidence_cards": "evidence_cards.json",
        },
        "layers": layers,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export web-ready layers.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--tables-dir", type=Path, default=Path("outputs/tables"), help="Tables directory.")
    parser.add_argument("--web-dir", type=Path, default=Path("outputs/web"), help="Web output directory.")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        required=True,
        help="Bounding box in EPSG:4326.",
    )
    parser.add_argument("--cell-size-deg", type=float, default=0.005, help="Grid cell size.")
    parser.add_argument("--dataset-prefix", default="sf_bay_ais", help="Daily table filename prefix.")
    parser.add_argument("--layer-prefix", default="sf_bay", help="Web layer filename prefix.")
    parser.add_argument("--dataset-id", default="sf_bay", help="Stable UI dataset identifier.")
    parser.add_argument("--study-area", default="San Francisco Bay and Port of Oakland Approaches")
    parser.add_argument("--source", default="NOAA MarineCadastre AIS 2025")
    parser.add_argument("--workflow-role", default="Primary open-data benchmark")
    parser.add_argument("--map-center", type=float, nargs=2, metavar=("LAT", "LON"), default=(37.84, -122.36))
    parser.add_argument("--map-zoom", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    range_label = f"{args.start.isoformat()}_to_{args.end.isoformat()}"
    density_name = f"{args.layer_prefix}_grid_density_{range_label}.geojson"
    density_stats = export_density_geojson(
        args.start,
        args.end,
        args.tables_dir,
        args.web_dir / density_name,
        tuple(args.bbox),
        args.cell_size_deg,
        args.dataset_prefix,
    )
    layers = [
        {
            "id": "traffic_density",
            "label": "Traffic density",
            "type": "polygon-geojson",
            "path": density_name,
            "value_property": "density_norm",
        },
        {
            "id": "traffic_patterns",
            "label": "Regular traffic pattern cells",
            "type": "polygon-geojson",
            "path": f"{args.layer_prefix}_traffic_patterns_{range_label}.geojson",
            "value_property": "moving_points",
        },
        {
            "id": "anomaly_points",
            "label": "Anomaly candidate points",
            "type": "point-geojson",
            "path": f"{args.layer_prefix}_anomaly_points_{range_label}.geojson",
            "value_property": "screening_score",
        },
        {
            "id": "risk_hotspots",
            "label": "Anomaly-only evidence hotspot cells",
            "type": "polygon-geojson",
            "path": f"{args.layer_prefix}_risk_hotspots_{range_label}.geojson",
            "value_property": "screening_score",
        },
        {
            "id": "encounter_points",
            "label": "CPA/TCPA future encounter candidate records",
            "type": "point-geojson",
            "path": f"{args.layer_prefix}_encounters_{range_label}.geojson",
            "value_property": "screening_score",
        },
        {
            "id": "fused_risk_hotspots",
            "label": "Fused first-pass evidence hotspot cells",
            "type": "polygon-geojson",
            "path": f"{args.layer_prefix}_fused_risk_hotspots_{range_label}.geojson",
            "value_property": "screening_score",
        },
        {
            "id": "case_tracks",
            "label": "Representative de-identified case tracks",
            "type": "line-geojson",
            "path": f"{args.layer_prefix}_case_tracks_{range_label}.geojson",
            "value_property": "mean_screening_score",
        },
    ]
    write_manifest(
        args.web_dir / "manifest.json",
        args.start,
        args.end,
        layers,
        dataset_id=args.dataset_id,
        study_area=args.study_area,
        source=args.source,
        workflow_role=args.workflow_role,
        map_center=tuple(args.map_center),
        map_zoom=args.map_zoom,
    )
    print(json.dumps({"density": density_stats, "manifest": str(args.web_dir / "manifest.json")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
