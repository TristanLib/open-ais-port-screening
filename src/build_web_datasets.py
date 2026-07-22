#!/usr/bin/env python3
"""Build the deployable two-waterway web dataset bundle."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from export_web_layers import BASEMAP, DISCLAIMER, export_density_geojson
from sanitize_web_data import SENSITIVE_KEYS, sanitize_geojson, sanitize_manifest, sanitize_summary


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SF_START = dt.date(2025, 5, 1)
SF_END = dt.date(2025, 5, 7)
SF_RANGE = "2025-05-01_to_2025-05-07"
TOKYO_START = dt.date(2024, 8, 1)
TOKYO_END = dt.date(2024, 8, 7)
TOKYO_RANGE = "2024-08-01_to_2024-08-07"
SF_BBOX = (-122.62, 37.42, -121.92, 38.18)
TOKYO_BBOX = (139.62, 34.90, 140.13, 35.69)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def copy_required(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"required web source is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def manifest(
    *,
    dataset_id: str,
    study_area: str,
    study_area_zh: str,
    source: str,
    source_zh: str,
    workflow_role: str,
    workflow_role_zh: str,
    start: dt.date,
    end: dt.date,
    center: tuple[float, float],
    zoom: int,
    summary: str,
    layers: list[dict[str, str]],
    evidence_cards: str | None = None,
) -> dict[str, Any]:
    companion_data = {"summary": summary}
    if evidence_cards:
        companion_data["evidence_cards"] = evidence_cards
    return {
        "project": "Open AIS Port Screening Explorer",
        "dataset_id": dataset_id,
        "study_area": study_area,
        "study_area_zh": study_area_zh,
        "source": source,
        "source_zh": source_zh,
        "workflow_role": workflow_role,
        "workflow_role_zh": workflow_role_zh,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "map_view": {"center": list(center), "zoom": zoom},
        "publication_mode": "sanitized_research_demo",
        "disclaimer": DISCLAIMER,
        "basemap": BASEMAP,
        "companion_data": companion_data,
        "layers": layers,
    }


def layer(layer_id: str, label: str, layer_type: str, path: str, value_property: str) -> dict[str, str]:
    return {
        "id": layer_id,
        "label": label,
        "type": layer_type,
        "path": path,
        "value_property": value_property,
    }


def build_tokyo_summary(tables_dir: Path) -> dict[str, Any]:
    prefix = f"tokyo_bay_ais_{TOKYO_RANGE}"
    validation = load_json(tables_dir / f"{prefix}_validation_summary.json")
    patterns = load_json(tables_dir / f"{prefix}_traffic_patterns_stats.json")
    anomaly = load_json(tables_dir / f"{prefix}_anomaly_stats.json")
    encounters = load_json(tables_dir / f"{prefix}_encounters_stats.json")
    backtest = load_json(tables_dir / f"{prefix}_encounter_backtest_stats.json")
    hotspots = load_json(tables_dir / f"{prefix}_fused_risk_hotspots_stats.json")
    rank_evaluation = load_json(tables_dir / f"{prefix}_screening_rank_evaluation.json")
    results = validation["headline_results"]
    clean_points = int(results["clean_ais_points"])
    fused_hotspots = int(results["fused_hotspot_cells"])
    counters = anomaly.get("counters", {})
    dominant_reasons = [
        {"reason": key.removeprefix("reason_"), "count": value}
        for key, value in sorted(counters.items(), key=lambda item: item[1], reverse=True)
        if key.startswith("reason_")
    ]
    return {
        "project": "Open AIS Port Screening Explorer",
        "dataset_id": "tokyo_bay",
        "study_area": "Tokyo Bay",
        "date_range": {"start": TOKYO_START.isoformat(), "end": TOKYO_END.isoformat()},
        "publication_mode": "sanitized_research_demo",
        "disclaimer": DISCLAIMER,
        "dataset": {
            "days": 7,
            "cropped_ais_points": int(results["converted_ais_points"]),
            "clean_ais_points": clean_points,
            "trajectory_segments": int(results["trajectory_segments"]),
            "min20_segments": int(results["min20_segments"]),
        },
        "traffic_patterns": {
            "normal_route_cells": int(results["normal_traffic_pattern_cells"]),
            "high_confidence_route_cells": int(results["high_confidence_pattern_cells"]),
            "moving_cells": patterns.get("nonempty_moving_cells"),
            "min_sog": patterns.get("min_sog"),
        },
        "anomaly_detection": {
            "anomaly_points": int(results["anomaly_candidate_points"]),
            "anomaly_rate_percent": round(100 * int(results["anomaly_candidate_points"]) / clean_points, 6),
            "normal_pattern_cells": int(results["normal_traffic_pattern_cells"]),
            "corroborated_candidate_points": counters.get("tier_corroborated_candidate"),
            "low_support_only_points": counters.get("tier_low_support_only"),
            "dominant_reasons": dominant_reasons,
        },
        "encounter_risk": {
            "encounters": int(results["encounter_candidate_records"]),
            "deduplicated_encounter_episodes": int(results["encounter_audit_episodes"]),
            "unique_vessel_pairs": encounters.get("unique_vessel_pairs"),
            "unique_vessel_pair_days": encounters.get("unique_vessel_pair_days"),
            "episode_gap_min": encounters.get("episode_gap_min"),
            "vessel_states": encounters.get("vessel_states"),
            "pair_checks_within_distance": encounters.get("pair_checks_within_distance"),
            "pair_opportunity_cells": encounters.get("pair_opportunity_cells"),
            "time_bin_seconds": encounters.get("time_bin_seconds"),
            "max_state_skew_s": encounters.get("max_state_skew_s"),
            "state_alignment": encounters.get("state_alignment"),
            "dcpa_threshold_nm": encounters.get("dcpa_threshold_nm"),
            "tcpa_threshold_min": encounters.get("tcpa_threshold_min"),
        },
        "encounter_backtest": {
            "method": backtest.get("method"),
            "future_only": backtest.get("future_only"),
            "episodes_with_aligned_followup": backtest.get("episodes_with_aligned_followup"),
            "supported_episodes_within_threshold": int(results["backtest_supported_episodes"]),
            "support_rate_observable": float(results["backtest_support_rate_observable"]),
            "near_supported_episodes_within_1nm": backtest.get("near_supported_episodes_within_1nm"),
            "near_support_rate_observable": backtest.get("near_support_rate_observable"),
            "median_actual_min_distance_nm": backtest.get("actual_min_distance_nm", {}).get("median"),
            "median_predicted_dcpa_abs_error_nm": backtest.get("predicted_dcpa_abs_error_nm", {}).get("median"),
            "observable_rate": backtest.get("observable_rate"),
        },
        "hotspots": {
            "fused_hotspots": fused_hotspots,
            "eligible_fused_cells": hotspots.get("eligible_hotspot_cells"),
            "encounter_weight": hotspots.get("encounter_weight"),
            "top_percent": hotspots.get("top_percent"),
        },
        "workload_compression": {
            "key_message": (
                f"The supplemental pipeline aggregates {clean_points:,} cleaned AIS observations "
                f"into {fused_hotspots:,} spatial review units; analyst review time was not measured."
            ),
            "method": "evidence_aggregation_summary",
            "analyst_time_measured": False,
        },
        "ranking_evaluation": rank_evaluation,
        "evidence_cards": {"card_count": None},
        "stability": {"mean_previous_day_jaccard": None},
        "ablation": [],
        "cases": {"case_count": 0, "case_ids": []},
        "transferability": {
            "role": "Asian port-water supplemental validation",
            "source_sog": "self-reported",
            "source_cog": "not available",
            "source_heading": "not available",
            "direction_used": "derived from consecutive positions",
            "interpretation": "cross-source executability and common output-schema portability; thresholds and operational relevance require regional validation",
        },
    }


def build_bundle(outputs_web: Path, tables_dir: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)

    sf_files = [
        f"sf_bay_grid_density_{SF_RANGE}.geojson",
        f"sf_bay_traffic_patterns_{SF_RANGE}.geojson",
        f"sf_bay_anomaly_points_{SF_RANGE}.geojson",
        f"sf_bay_risk_hotspots_{SF_RANGE}.geojson",
        f"sf_bay_encounters_{SF_RANGE}.geojson",
        f"sf_bay_fused_risk_hotspots_{SF_RANGE}.geojson",
        f"sf_bay_case_tracks_{SF_RANGE}.geojson",
    ]
    for filename in sf_files:
        copy_required(outputs_web / filename, target / filename)
    copy_required(outputs_web / f"sf_bay_ais_{SF_RANGE}_summary.json", target / "summary.json")
    copy_required(outputs_web / "evidence_cards.json", target / "evidence_cards.json")

    tokyo_density = f"tokyo_bay_grid_density_{TOKYO_RANGE}.geojson"
    export_density_geojson(
        TOKYO_START,
        TOKYO_END,
        tables_dir,
        target / tokyo_density,
        TOKYO_BBOX,
        0.005,
        "tokyo_bay_ais",
    )
    tokyo_files = [
        f"tokyo_bay_traffic_patterns_{TOKYO_RANGE}.geojson",
        f"tokyo_bay_anomaly_points_{TOKYO_RANGE}.geojson",
        f"tokyo_bay_encounters_{TOKYO_RANGE}.geojson",
        f"tokyo_bay_fused_risk_hotspots_{TOKYO_RANGE}.geojson",
    ]
    for filename in tokyo_files:
        copy_required(outputs_web / filename, target / filename)
    write_json(target / "summary_tokyo_bay.json", build_tokyo_summary(tables_dir))

    sf_manifest = manifest(
        dataset_id="sf_bay",
        study_area="San Francisco Bay and Port of Oakland Approaches",
        study_area_zh="旧金山湾与奥克兰港进近水域",
        source="NOAA MarineCadastre AIS 2025",
        source_zh="NOAA MarineCadastre AIS 2025",
        workflow_role="Primary open-data benchmark",
        workflow_role_zh="主要开放数据基准",
        start=SF_START,
        end=SF_END,
        center=(37.84, -122.36),
        zoom=10,
        summary="summary.json",
        evidence_cards="evidence_cards.json",
        layers=[
            layer("traffic_density", "Traffic density", "polygon-geojson", sf_files[0], "density_norm"),
            layer("traffic_patterns", "Regular traffic pattern cells", "polygon-geojson", sf_files[1], "moving_points"),
            layer("anomaly_points", "Anomaly candidate points", "point-geojson", sf_files[2], "screening_score"),
            layer("risk_hotspots", "Anomaly-only evidence hotspot cells", "polygon-geojson", sf_files[3], "screening_score"),
            layer("encounter_points", "CPA/TCPA future encounter candidate records", "point-geojson", sf_files[4], "screening_score"),
            layer("fused_risk_hotspots", "Fused review-priority cells", "polygon-geojson", sf_files[5], "screening_score"),
            layer("case_tracks", "Representative de-identified case tracks", "line-geojson", sf_files[6], "mean_screening_score"),
        ],
    )
    tokyo_manifest = manifest(
        dataset_id="tokyo_bay",
        study_area="Tokyo Bay",
        study_area_zh="东京湾",
        source="Hutten 2026 / Figshare v2 (CC BY 4.0)",
        source_zh="Hutten 2026 / Figshare v2（CC BY 4.0）",
        workflow_role="Asian port-water supplemental validation",
        workflow_role_zh="亚洲港口水域补充可执行性验证",
        start=TOKYO_START,
        end=TOKYO_END,
        center=(35.36, 139.86),
        zoom=9,
        summary="summary_tokyo_bay.json",
        layers=[
            layer("traffic_density", "Traffic density", "polygon-geojson", tokyo_density, "density_norm"),
            layer("traffic_patterns", "Regular traffic pattern cells", "polygon-geojson", tokyo_files[0], "moving_points"),
            layer("anomaly_points", "Anomaly candidate points", "point-geojson", tokyo_files[1], "screening_score"),
            layer("encounter_points", "CPA/TCPA future encounter candidate records", "point-geojson", tokyo_files[2], "screening_score"),
            layer("fused_risk_hotspots", "Fused review-priority cells", "polygon-geojson", tokyo_files[3], "screening_score"),
        ],
    )
    write_json(target / "manifest.json", sf_manifest)
    write_json(target / "manifest_tokyo_bay.json", tokyo_manifest)
    write_json(
        target / "datasets.json",
        {
            "default_dataset": "sf_bay",
            "datasets": [
                {
                    "id": "sf_bay",
                    "manifest": "manifest.json",
                    "label": {"en": "San Francisco Bay", "zh": "旧金山湾"},
                },
                {
                    "id": "tokyo_bay",
                    "manifest": "manifest_tokyo_bay.json",
                    "label": {"en": "Tokyo Bay", "zh": "东京湾"},
                },
            ],
        },
    )

    for path in sorted(target.glob("*.geojson")):
        sanitize_geojson(path, coordinate_digits=4, max_case_points=80)
    for path in sorted(target.glob("manifest*.json")):
        sanitize_manifest(path)
    for path in sorted(target.glob("summary*.json")):
        sanitize_summary(path)
    validate_bundle(target)


def validate_bundle(target: Path) -> None:
    catalog = load_json(target / "datasets.json")
    if catalog.get("default_dataset") != "sf_bay" or len(catalog.get("datasets", [])) != 2:
        raise RuntimeError("dataset catalog must expose San Francisco Bay and Tokyo Bay")

    for dataset in catalog["datasets"]:
        manifest_data = load_json(target / dataset["manifest"])
        summary_path = target / manifest_data["companion_data"]["summary"]
        if not summary_path.exists():
            raise FileNotFoundError(f"summary referenced by manifest is missing: {summary_path}")
        for layer_meta in manifest_data["layers"]:
            layer_path = target / layer_meta["path"]
            if not layer_path.exists():
                raise FileNotFoundError(f"layer referenced by manifest is missing: {layer_path}")
            data = load_json(layer_path)
            if not data.get("features"):
                raise RuntimeError(f"web layer has no features: {layer_path}")
            for feature in data["features"]:
                leaked = SENSITIVE_KEYS.intersection((feature.get("properties") or {}).keys())
                if leaked:
                    raise RuntimeError(f"sensitive fields remain in {layer_path}: {sorted(leaked)}")

    tokyo_summary = load_json(target / "summary_tokyo_bay.json")
    expected = {
        "clean_ais_points": 475530,
        "anomaly_points": 41702,
        "encounters": 26389,
        "deduplicated_encounter_episodes": 8929,
        "fused_hotspots": 39,
    }
    observed = {
        "clean_ais_points": tokyo_summary["dataset"]["clean_ais_points"],
        "anomaly_points": tokyo_summary["anomaly_detection"]["anomaly_points"],
        "encounters": tokyo_summary["encounter_risk"]["encounters"],
        "deduplicated_encounter_episodes": tokyo_summary["encounter_risk"]["deduplicated_encounter_episodes"],
        "fused_hotspots": tokyo_summary["hotspots"]["fused_hotspots"],
    }
    if observed != expected:
        raise RuntimeError(f"Tokyo Bay web summary mismatch: expected={expected}, observed={observed}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build sanitized San Francisco Bay and Tokyo Bay web datasets.")
    parser.add_argument("--outputs-web", type=Path, default=PROJECT_ROOT / "outputs" / "web")
    parser.add_argument("--tables-dir", type=Path, default=PROJECT_ROOT / "outputs" / "tables")
    parser.add_argument("--target", type=Path, default=PROJECT_ROOT / "web" / "data")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    build_bundle(args.outputs_web, args.tables_dir, args.target)
    print(args.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
