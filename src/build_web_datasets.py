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

from evidence_cards import validate_public_card_bundle
from export_web_layers import BASEMAP, DISCLAIMER, export_density_geojson
from sanitize_web_data import SENSITIVE_KEYS, sanitize_geojson, sanitize_manifest, sanitize_summary


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENCOUNTER_CARD_SCHEMA_VERSION = "review-v10.encounter-evidence-card.v2"
PUBLIC_TERM_REPLACEMENTS = {
    "anomaly-dominated review hotspot": "behavior-component-dominated review hotspot",
    "balanced anomaly-encounter evidence": "balanced behavior-encounter evidence",
    "anomaly-dominated fused evidence": "behavior-component-dominated fused evidence",
    "Combines anomaly evidence and future encounter-candidate evidence.": (
        "Combines behavior evidence and future encounter-candidate evidence."
    ),
}
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


def normalize_public_terms(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            PUBLIC_TERM_REPLACEMENTS.get(key, key): normalize_public_terms(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_public_terms(item) for item in value]
    if isinstance(value, str):
        return PUBLIC_TERM_REPLACEMENTS.get(value, value)
    return value


def iter_public_values(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield path + ("<key>",), key
            yield from iter_public_values(item, path + (str(key),))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_public_values(item, path + (str(index),))
    else:
        yield path, value


def write_compact_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def copy_required(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"required web source is missing: {source}")
    if source.resolve() == target.resolve():
        return
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
    encounter_evidence_cards: str | None = None,
) -> dict[str, Any]:
    companion_data = {"summary": summary}
    if evidence_cards:
        companion_data["evidence_cards"] = evidence_cards
    if encounter_evidence_cards:
        companion_data["encounter_evidence_cards"] = encounter_evidence_cards
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
    controls = load_json(tables_dir / f"{prefix}_geometric_control_evaluation.json")
    rank_evaluation = load_json(tables_dir / f"{prefix}_dual_objective_rank_evaluation.json")
    encounter_cards = load_json(tables_dir / f"{prefix}_encounter_evidence_cards.json")
    results = validation["headline_results"]
    clean_points = int(results["clean_ais_points"])
    fused_hotspots = int(hotspots["hotspot_cells"])
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
            "normal_route_cells": int(patterns.get("normal_route_cells", results["normal_traffic_pattern_cells"])),
            "high_confidence_route_cells": int(patterns.get("high_confidence_route_cells", results["high_confidence_pattern_cells"])),
            "moving_cells": patterns.get("nonempty_moving_cells"),
            "min_sog": patterns.get("min_sog"),
        },
        "anomaly_detection": {
            "anomaly_points": int(counters.get("anomaly_rows", 0)),
            "anomaly_rate_percent": round(100 * int(counters.get("anomaly_rows", 0)) / clean_points, 6),
            "normal_pattern_cells": int(patterns.get("normal_route_cells", results["normal_traffic_pattern_cells"])),
            "corroborated_candidate_points": counters.get("tier_corroborated_candidate"),
            "low_support_only_points": counters.get("tier_low_support_only"),
            "dominant_reasons": dominant_reasons,
        },
        "encounter_risk": {
            "encounters": int(encounters["encounters"]),
            "deduplicated_encounter_episodes": int(encounters["deduplicated_encounter_episodes"]),
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
            "encounter_candidate_records": backtest.get("encounter_candidate_records"),
            "deduplicated_encounter_episodes": backtest.get("deduplicated_encounter_episodes"),
            "episodes_with_aligned_followup": backtest.get("episodes_with_aligned_followup"),
            "episodes_without_aligned_followup": backtest.get("episodes_without_aligned_followup"),
            "supported_episodes_within_threshold": int(backtest["supported_episodes_within_threshold"]),
            "support_rate_observable": backtest.get("support_rate_observable"),
            "near_supported_episodes_within_1nm": backtest.get("near_supported_episodes_within_1nm"),
            "near_support_rate_observable": backtest.get("near_support_rate_observable"),
            "median_actual_min_distance_nm": backtest.get("actual_min_distance_nm", {}).get("median"),
            "median_predicted_dcpa_abs_error_nm": backtest.get("predicted_dcpa_abs_error_nm", {}).get("median"),
            "observable_rate": backtest.get("observable_rate"),
            "primary_observability": backtest.get("primary_observability"),
            "future_coverage": backtest.get("future_coverage"),
            "observability_sensitivity": backtest.get("observability_sensitivity"),
            "minimum_distance_grid_sensitivity": backtest.get("minimum_distance_grid_sensitivity"),
            "predicted_to_observed_abs_delta_min": backtest.get("predicted_to_observed_abs_delta_min"),
        },
        "geometric_controls": controls,
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
        "fusion_claim_gate": rank_evaluation.get("leave_one_day_out", {}).get("fusion_claim_gate", {}),
        "evidence_cards": {"card_count": None},
        "encounter_evidence_cards": {
            "card_count": int(encounter_cards["card_count"]),
            "schema_version": encounter_cards.get("schema_version"),
            "publication_mode": encounter_cards.get("publication_mode"),
        },
        "stability": {"mean_previous_day_jaccard": None},
        "ablation": [],
        "cases": {"case_count": 0, "case_ids": []},
        "transferability": {
            "role": "Asian port-water supplemental validation",
            "source_sog": "self-reported",
            "source_cog": "not available",
            "source_heading": "not available",
            "direction_used": "same-segment derived ground-track course",
            "interpretation": (
                "cross-source executability and common output-schema generation only; not threshold transfer, "
                "cross-port performance generalization, or port-safety comparison"
            ),
        },
    }


def enrich_sf_summary(tables_dir: Path, summary_path: Path) -> None:
    """Replace every recomputed stage with current review-v10 output statistics."""
    prefix = f"sf_bay_ais_{SF_RANGE}"
    summary = load_json(summary_path)
    pipeline = load_json(tables_dir / f"{prefix}_pipeline_summary.json")
    patterns = load_json(tables_dir / f"{prefix}_traffic_patterns_stats.json")
    anomaly = load_json(tables_dir / f"{prefix}_anomaly_stats.json")
    encounters = load_json(tables_dir / f"{prefix}_encounters_stats.json")
    backtest = load_json(tables_dir / f"{prefix}_encounter_backtest_stats.json")
    controls = load_json(tables_dir / f"{prefix}_geometric_control_evaluation.json")
    anomaly_hotspots = load_json(tables_dir / f"{prefix}_risk_hotspots_stats.json")
    fused_hotspots = load_json(tables_dir / f"{prefix}_fused_risk_hotspots_stats.json")
    workload = load_json(tables_dir / f"{prefix}_workload_compression.json")
    typology = load_json(tables_dir / f"{prefix}_hotspot_typology_stats.json")
    ranking = load_json(tables_dir / f"{prefix}_dual_objective_rank_evaluation.json")
    legacy_cards = load_json(tables_dir / f"{prefix}_evidence_cards.json")
    encounter_cards = load_json(tables_dir / f"{prefix}_encounter_evidence_cards.json")
    totals = pipeline["totals"]
    counters = anomaly.get("counters", {})
    clean_points = int(totals["clean_rows"])
    dominant_reasons = [
        {"reason": key.removeprefix("reason_"), "count": value}
        for key, value in sorted(counters.items(), key=lambda item: item[1], reverse=True)
        if key.startswith("reason_")
    ]

    summary["dataset"] = {
        "days": int(pipeline["days"]),
        "cropped_ais_points": int(totals["crop_rows"]),
        "clean_ais_points": clean_points,
        "trajectory_segments": int(totals["tracks"]),
        "min20_segments": int(totals["tracks_min_points"]),
        "min20_points": int(totals["tracks_min_points_rows"]),
        "mean_daily_clean_points": round(clean_points / int(pipeline["days"]), 6),
    }
    summary["traffic_patterns"] = {
        "normal_route_cells": int(patterns["normal_route_cells"]),
        "high_confidence_route_cells": int(patterns["high_confidence_route_cells"]),
        "moving_cells": patterns.get("nonempty_moving_cells"),
        "min_sog": patterns.get("min_sog"),
    }
    summary["anomaly_detection"] = {
        "anomaly_points": int(counters.get("anomaly_rows", 0)),
        "anomaly_rate_percent": round(100 * int(counters.get("anomaly_rows", 0)) / clean_points, 6),
        "normal_pattern_cells": int(patterns["normal_route_cells"]),
        "corroborated_candidate_points": counters.get("tier_corroborated_candidate"),
        "low_support_only_points": counters.get("tier_low_support_only"),
        "dominant_reasons": dominant_reasons,
    }
    summary["encounter_risk"] = {
        "encounters": int(encounters["encounters"]),
        "deduplicated_encounter_episodes": int(encounters["deduplicated_encounter_episodes"]),
        "unique_vessel_pairs": encounters.get("unique_vessel_pairs"),
        "unique_vessel_pair_days": encounters.get("unique_vessel_pair_days"),
        "episode_gap_min": encounters.get("episode_gap_min"),
        "vessel_states": encounters.get("vessel_states"),
        "synchronized_grid_vessel_states": encounters.get("synchronized_grid_vessel_states"),
        "pair_checks_within_distance": encounters.get("pair_checks_within_distance"),
        "pair_opportunity_records": encounters.get("pair_opportunity_records"),
        "pair_opportunity_cells": encounters.get("pair_opportunity_cells"),
        "time_bin_seconds": encounters.get("time_bin_seconds"),
        "max_state_skew_s": encounters.get("max_state_skew_s"),
        "state_alignment": encounters.get("state_alignment"),
        "spatial_index": encounters.get("spatial_index"),
        "spatial_index_completeness_audit": encounters.get("spatial_index_completeness_audit"),
        "dcpa_threshold_nm": encounters.get("dcpa_threshold_nm"),
        "tcpa_threshold_min": encounters.get("tcpa_threshold_min"),
    }
    summary["encounter_backtest"] = {
        "method": backtest.get("method"),
        "future_only": backtest.get("future_only"),
        "encounter_candidate_records": backtest.get("encounter_candidate_records"),
        "deduplicated_encounter_episodes": backtest.get("deduplicated_encounter_episodes"),
        "episodes_with_aligned_followup": backtest.get("episodes_with_aligned_followup"),
        "episodes_without_aligned_followup": backtest.get("episodes_without_aligned_followup"),
        "supported_episodes_within_threshold": backtest.get("supported_episodes_within_threshold"),
        "support_rate_observable": backtest.get("support_rate_observable"),
        "near_supported_episodes_within_1nm": backtest.get("near_supported_episodes_within_1nm"),
        "near_support_rate_observable": backtest.get("near_support_rate_observable"),
        "observable_rate": backtest.get("observable_rate"),
        "primary_observability": backtest.get("primary_observability"),
        "future_coverage": backtest.get("future_coverage"),
        "observability_sensitivity": backtest.get("observability_sensitivity"),
        "minimum_distance_grid_sensitivity": backtest.get("minimum_distance_grid_sensitivity"),
        "actual_min_distance_nm": backtest.get("actual_min_distance_nm"),
        "predicted_dcpa_abs_error_nm": backtest.get("predicted_dcpa_abs_error_nm"),
        "predicted_to_observed_abs_delta_min": backtest.get("predicted_to_observed_abs_delta_min"),
    }
    summary["geometric_controls"] = controls
    summary["hotspots"] = {
        "anomaly_only_hotspots": int(anomaly_hotspots["hotspot_cells"]),
        "fused_hotspots": int(fused_hotspots["hotspot_cells"]),
        "eligible_fused_cells": fused_hotspots.get("eligible_hotspot_cells"),
        "encounter_weight": fused_hotspots.get("encounter_weight"),
        "top_percent": fused_hotspots.get("top_percent"),
    }
    summary["workload_compression"] = workload
    summary["hotspot_typology"] = typology
    summary["ranking_evaluation"] = ranking
    summary["fusion_claim_gate"] = ranking.get("leave_one_day_out", {}).get("fusion_claim_gate", {})
    summary["evidence_cards"] = {
        "card_count": int(legacy_cards["card_count"]),
        **legacy_cards.get("summary", {}),
    }
    summary["encounter_evidence_cards"] = {
        "card_count": int(encounter_cards["card_count"]),
        "schema_version": encounter_cards.get("schema_version"),
        "publication_mode": encounter_cards.get("publication_mode"),
    }
    summary["ablation"] = [
        {
            "variant": "Density-only",
            "result": "Descriptive baseline",
            "interpretation": (
                "High-density cells include berth/anchorage behavior and are not sufficient as safety evidence."
            ),
        },
        {
            "variant": "Behavior-component-only",
            "result": f"{int(anomaly_hotspots['hotspot_cells']):,} hotspot cells",
            "interpretation": "Finds behavior-evidence deviations but misses dense crossing/meeting evidence.",
        },
        {
            "variant": "Encounter-only",
            "result": (
                f"{int(encounters['encounters']):,} records; "
                f"{int(encounters['deduplicated_encounter_episodes']):,} episodes"
            ),
            "interpretation": "Captures close-quarters candidates but lacks behavioral context.",
        },
        {
            "variant": "Fused screening",
            "result": f"{int(fused_hotspots['hotspot_cells']):,} hotspot cells",
            "interpretation": "Combines behavior evidence and future encounter-candidate evidence.",
        },
    ]
    write_json(summary_path, summary)


def annotate_point_layer_sampling(
    path: Path,
    *,
    source_count: int,
    selection_rule: str,
    geojson_limit: int = 2000,
) -> None:
    """Make the lightweight display cap explicit so map features are not read as totals."""
    data = load_json(path)
    published_count = len(data.get("features", []))
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    metadata.update(
        {
            "source_count": int(source_count),
            "published_count": published_count,
            "geojson_limit": int(geojson_limit),
            "selection_rule": selection_rule,
        }
    )
    data["metadata"] = metadata
    write_json(path, data)


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
    copy_required(
        outputs_web / "encounter_evidence_cards.json",
        target / "encounter_evidence_cards.json",
    )
    enrich_sf_summary(tables_dir, target / "summary.json")

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
    copy_required(
        outputs_web / "encounter_evidence_cards_tokyo_bay.json",
        target / "encounter_evidence_cards_tokyo_bay.json",
    )
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
        encounter_evidence_cards="encounter_evidence_cards.json",
        layers=[
            layer("traffic_density", "Traffic density", "polygon-geojson", sf_files[0], "density_norm"),
            layer("traffic_patterns", "Regular traffic pattern cells", "polygon-geojson", sf_files[1], "moving_points"),
            layer("anomaly_points", "Anomaly candidate points", "point-geojson", sf_files[2], "screening_score"),
            layer(
                "risk_hotspots",
                "Behavior-component-only comparison cells",
                "polygon-geojson",
                sf_files[3],
                "screening_score",
            ),
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
        workflow_role="Asian port-water cross-source supplemental execution",
        workflow_role_zh="亚洲港口水域补充可执行性验证",
        start=TOKYO_START,
        end=TOKYO_END,
        center=(35.36, 139.86),
        zoom=9,
        summary="summary_tokyo_bay.json",
        encounter_evidence_cards="encounter_evidence_cards_tokyo_bay.json",
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

    sf_summary = load_json(target / "summary.json")
    tokyo_summary = load_json(target / "summary_tokyo_bay.json")
    annotate_point_layer_sampling(
        target / f"sf_bay_anomaly_points_{SF_RANGE}.geojson",
        source_count=int(sf_summary["anomaly_detection"]["anomaly_points"]),
        selection_rule="highest anomaly score; stable source order for ties",
    )
    annotate_point_layer_sampling(
        target / f"sf_bay_encounters_{SF_RANGE}.geojson",
        source_count=int(sf_summary["encounter_risk"]["encounters"]),
        selection_rule="highest encounter screening score; stable source order for ties",
    )
    annotate_point_layer_sampling(
        target / f"tokyo_bay_anomaly_points_{TOKYO_RANGE}.geojson",
        source_count=int(tokyo_summary["anomaly_detection"]["anomaly_points"]),
        selection_rule="highest anomaly score; stable source order for ties",
    )
    annotate_point_layer_sampling(
        target / f"tokyo_bay_encounters_{TOKYO_RANGE}.geojson",
        source_count=int(tokyo_summary["encounter_risk"]["encounters"]),
        selection_rule="highest encounter screening score; stable source order for ties",
    )

    for path in sorted(target.glob("*.geojson")):
        sanitize_geojson(path, coordinate_digits=4, max_case_points=80)
        write_compact_json(path, normalize_public_terms(load_json(path)))
    for path in sorted(target.glob("manifest*.json")):
        sanitize_manifest(path)
        manifest_data = load_json(path)
        for layer_meta in manifest_data.get("layers", []):
            if layer_meta.get("id") == "risk_hotspots":
                layer_meta["label"] = "Behavior-component-only comparison cells"
        write_json(path, manifest_data)
    for path in sorted(target.glob("summary*.json")):
        sanitize_summary(path)
    for path in sorted(target.glob("*.json")):
        write_json(path, normalize_public_terms(load_json(path)))
    validate_bundle(target)


def validate_bundle(target: Path) -> None:
    catalog = load_json(target / "datasets.json")
    if catalog.get("default_dataset") != "sf_bay" or len(catalog.get("datasets", [])) != 2:
        raise RuntimeError("dataset catalog must expose San Francisco Bay and Tokyo Bay")

    for dataset in catalog["datasets"]:
        manifest_data = load_json(target / dataset["manifest"])
        if manifest_data.get("dataset_id") != dataset.get("id"):
            raise RuntimeError(f"catalog/manifest dataset mismatch: {dataset}")
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
            if layer_meta.get("id") in {"anomaly_points", "encounter_points"}:
                metadata = data.get("metadata", {})
                if metadata.get("published_count") != len(data["features"]):
                    raise RuntimeError(f"published point count is missing or stale: {layer_path}")
                if not isinstance(metadata.get("source_count"), int):
                    raise RuntimeError(f"source point count is missing: {layer_path}")
                if metadata.get("geojson_limit") != 2000 or not metadata.get("selection_rule"):
                    raise RuntimeError(f"point-layer sampling rule is incomplete: {layer_path}")

        card_name = manifest_data.get("companion_data", {}).get("encounter_evidence_cards")
        if card_name:
            card_path = target / card_name
            if not card_path.exists():
                raise FileNotFoundError(f"encounter evidence cards referenced by manifest are missing: {card_path}")
            card_bundle = load_json(card_path)
            cards = card_bundle.get("cards")
            if card_bundle.get("dataset_id") != manifest_data.get("dataset_id"):
                raise RuntimeError(f"encounter evidence-card dataset mismatch: {card_path}")
            if card_bundle.get("publication_mode") != "sanitized_research_demo":
                raise RuntimeError(f"encounter evidence cards are not sanitized: {card_path}")
            if card_bundle.get("schema_version") != ENCOUNTER_CARD_SCHEMA_VERSION:
                raise RuntimeError(f"encounter evidence cards use a stale schema: {card_path}")
            if not isinstance(cards, list) or card_bundle.get("card_count") != len(cards):
                raise RuntimeError(f"encounter evidence-card count mismatch: {card_path}")
            validate_public_card_bundle(
                card_bundle,
                expected_dataset_id=str(manifest_data.get("dataset_id")),
            )
            summary = load_json(summary_path)
            summary_cards = summary.get("encounter_evidence_cards", {})
            if summary_cards.get("schema_version") != card_bundle.get("schema_version"):
                raise RuntimeError(f"encounter evidence-card schema disagrees with summary: {card_path}")
            if summary_cards.get("card_count") != card_bundle.get("card_count"):
                raise RuntimeError(f"encounter evidence-card count disagrees with summary: {card_path}")

        if manifest_data.get("dataset_id") == "sf_bay":
            risk_layer = next(
                (item for item in manifest_data.get("layers", []) if item.get("id") == "risk_hotspots"),
                None,
            )
            if risk_layer is not None and risk_layer.get("label") != "Behavior-component-only comparison cells":
                raise RuntimeError("San Francisco behavior-component-only layer label is stale")
            summary = load_json(summary_path)
            variants = [row.get("variant") for row in summary.get("ablation", [])]
            if variants and ("Behavior-component-only" not in variants or "Anomaly-only" in variants):
                raise RuntimeError("San Francisco ablation terminology is stale")

    for dataset_label, summary_name in (
        ("San Francisco Bay", "summary.json"),
        ("Tokyo Bay", "summary_tokyo_bay.json"),
    ):
        dataset_summary = load_json(target / summary_name)
        encounter = dataset_summary.get("encounter_risk", {})
        backtest = dataset_summary.get("encounter_backtest", {})
        candidate_records = backtest.get("encounter_candidate_records")
        if candidate_records is not None and candidate_records != encounter.get("encounters"):
            raise RuntimeError(
                f"{dataset_label} candidate-record count disagrees between encounter and backtest outputs"
            )
        episode_count = backtest.get("deduplicated_encounter_episodes")
        if episode_count is not None and episode_count != encounter.get("deduplicated_encounter_episodes"):
            raise RuntimeError(
                f"{dataset_label} episode count disagrees between encounter and backtest outputs"
            )
        observable = backtest.get("episodes_with_aligned_followup")
        unobservable = backtest.get("episodes_without_aligned_followup")
        if episode_count is not None and observable is not None and unobservable is not None:
            if int(observable) + int(unobservable) != int(episode_count):
                raise RuntimeError(f"{dataset_label} strict-future observable episode accounting is inconsistent")
        supported = backtest.get("supported_episodes_within_threshold")
        if supported is not None and observable is not None and int(supported) > int(observable):
            raise RuntimeError(f"{dataset_label} supported episodes exceed observable episodes")

    for dataset_label, manifest_name, summary_name in (
        ("San Francisco Bay", "manifest.json", "summary.json"),
        ("Tokyo Bay", "manifest_tokyo_bay.json", "summary_tokyo_bay.json"),
    ):
        dataset_manifest = load_json(target / manifest_name)
        dataset_summary = load_json(target / summary_name)
        fused_layer = next(
            (
                layer
                for layer in dataset_manifest.get("layers", [])
                if layer.get("id") == "fused_risk_hotspots"
            ),
            None,
        )
        if fused_layer:
            layer_count = len(load_json(target / fused_layer["path"]).get("features", []))
            expected_count = dataset_summary.get("hotspots", {}).get("fused_hotspots")
            if expected_count is not None and layer_count != expected_count:
                raise RuntimeError(
                    f"{dataset_label} fused-hotspot summary disagrees with its published GeoJSON"
                )

    stale_public_terms = set(PUBLIC_TERM_REPLACEMENTS)
    for path in [*sorted(target.glob("*.json")), *sorted(target.glob("*.geojson"))]:
        string_values = {
            value
            for _path, value in iter_public_values(load_json(path))
            if isinstance(value, str)
        }
        stale = stale_public_terms.intersection(string_values)
        if stale:
            raise RuntimeError(f"stale public behavior terminology remains in {path}: {sorted(stale)}")


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
