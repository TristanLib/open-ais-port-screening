#!/usr/bin/env python3
"""Export a compact web summary from pipeline statistics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


DISCLAIMER = (
    "Historical AIS first-pass screening evidence demo. Not real-time, not for "
    "navigation, not for enforcement, and not for operational decision-making."
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def rounded(value: float | int | None, digits: int = 4) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return round(float(value), digits)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a compact web summary JSON.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--tables-dir", default="outputs/tables")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    prefix = f"sf_bay_ais_{args.start}_to_{args.end}"
    tables_dir = Path(args.tables_dir)
    output = Path(args.output or f"outputs/web/{prefix}_summary.json")

    pipeline = load_json(tables_dir / f"{prefix}_pipeline_summary.json")
    stability = load_json(tables_dir / f"{prefix}_stability.json")
    patterns = load_json(tables_dir / f"{prefix}_traffic_patterns_stats.json")
    anomaly = load_json(tables_dir / f"{prefix}_anomaly_stats.json")
    encounters = load_json(tables_dir / f"{prefix}_encounters_stats.json")
    anomaly_hotspots = load_json(tables_dir / f"{prefix}_risk_hotspots_stats.json")
    fused_hotspots = load_json(tables_dir / f"{prefix}_fused_risk_hotspots_stats.json")
    cases = load_json(tables_dir / f"{prefix}_case_studies.json")
    backtest = load_optional_json(tables_dir / f"{prefix}_encounter_backtest_stats.json")
    compression = load_optional_json(tables_dir / f"{prefix}_workload_compression.json")
    typology = load_optional_json(tables_dir / f"{prefix}_hotspot_typology_stats.json")
    evidence_cards = load_optional_json(tables_dir / f"{prefix}_evidence_cards.json")
    rank_evaluation = load_optional_json(tables_dir / f"{prefix}_screening_rank_evaluation.json")
    encounter_records = encounters.get("encounters") or 0
    encounter_episodes = encounters.get("deduplicated_encounter_episodes") or 0

    previous_overlaps = [
        item["overlap_with_previous"]
        for item in stability.get("top_cell_overlap", [])
        if item.get("overlap_with_previous") is not None
    ]
    first_overlaps = [
        item["overlap_with_first"]
        for item in stability.get("top_cell_overlap", [])
        if item.get("overlap_with_first") is not None and item.get("date") != args.start
    ]

    summary = {
        "project": "Open AIS Port Screening Explorer",
        "study_area": "San Francisco Bay and Port of Oakland Approaches",
        "date_range": {"start": args.start, "end": args.end},
        "publication_mode": "sanitized_research_demo",
        "disclaimer": DISCLAIMER,
        "dataset": {
            "days": pipeline.get("days"),
            "cropped_ais_points": pipeline.get("totals", {}).get("crop_rows"),
            "clean_ais_points": pipeline.get("totals", {}).get("clean_rows"),
            "trajectory_segments": pipeline.get("totals", {}).get("tracks"),
            "min20_segments": pipeline.get("totals", {}).get("tracks_min_points"),
            "min20_points": pipeline.get("totals", {}).get("tracks_min_points_rows"),
            "mean_daily_clean_points": rounded(stability.get("descriptors", {}).get("clean_rows", {}).get("mean"), 0),
        },
        "traffic_patterns": {
            "normal_route_cells": patterns.get("normal_route_cells"),
            "high_confidence_route_cells": patterns.get("high_confidence_route_cells"),
            "moving_cells": patterns.get("nonempty_moving_cells"),
            "min_sog": patterns.get("min_sog"),
        },
        "anomaly_detection": {
            "anomaly_points": anomaly.get("counters", {}).get("anomaly_rows"),
            "anomaly_rate_percent": anomaly.get("anomaly_rate_percent"),
            "normal_pattern_cells": anomaly.get("normal_pattern_cells"),
            "corroborated_candidate_points": anomaly.get("counters", {}).get("tier_corroborated_candidate"),
            "low_support_only_points": anomaly.get("counters", {}).get("tier_low_support_only"),
            "dominant_reasons": [
                {"reason": key.replace("reason_", ""), "count": value}
                for key, value in sorted(
                    anomaly.get("counters", {}).items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
                if key.startswith("reason_")
            ],
        },
        "encounter_risk": {
            "encounters": encounters.get("encounters"),
            "deduplicated_encounter_episodes": encounters.get("deduplicated_encounter_episodes"),
            "unique_vessel_pairs": encounters.get("unique_vessel_pairs"),
            "unique_vessel_pair_days": encounters.get("unique_vessel_pair_days"),
            "episode_gap_min": encounters.get("episode_gap_min"),
            "vessel_states": encounters.get("vessel_states"),
            "pair_checks_within_distance": encounters.get("pair_checks_within_distance"),
            "pair_opportunity_cells": encounters.get("pair_opportunity_cells"),
            "time_bin_seconds": encounters.get("time_bin_seconds"),
            "max_state_skew_s": encounters.get("max_state_skew_s"),
            "state_alignment": encounters.get("state_alignment"),
            "accepted_state_skew_s": encounters.get("accepted_state_skew_s"),
            "dcpa_threshold_nm": encounters.get("dcpa_threshold_nm"),
            "tcpa_threshold_min": encounters.get("tcpa_threshold_min"),
        },
        "encounter_backtest": {
            "method": backtest.get("method"),
            "future_only": backtest.get("future_only"),
            "episodes_with_aligned_followup": backtest.get("episodes_with_aligned_followup"),
            "supported_episodes_within_threshold": backtest.get("supported_episodes_within_threshold"),
            "support_rate_observable": backtest.get("support_rate_observable"),
            "near_supported_episodes_within_1nm": backtest.get("near_supported_episodes_within_1nm"),
            "near_support_rate_observable": backtest.get("near_support_rate_observable"),
            "median_actual_min_distance_nm": backtest.get("actual_min_distance_nm", {}).get("median"),
            "median_predicted_observed_delta_min": backtest.get("predicted_to_observed_abs_delta_min", {}).get("median"),
            "median_predicted_dcpa_abs_error_nm": backtest.get("predicted_dcpa_abs_error_nm", {}).get("median"),
            "observable_rate": backtest.get("observable_rate"),
        },
        "hotspots": {
            "anomaly_only_hotspots": anomaly_hotspots.get("hotspot_cells"),
            "fused_hotspots": fused_hotspots.get("hotspot_cells"),
            "eligible_fused_cells": fused_hotspots.get("eligible_hotspot_cells"),
            "encounter_weight": fused_hotspots.get("encounter_weight"),
            "top_percent": fused_hotspots.get("top_percent"),
        },
        "workload_compression": {
            "key_message": compression.get("key_message"),
            "method": compression.get("method"),
            "analyst_time_measured": compression.get("analyst_time_measured"),
            "stages": compression.get("stages", []),
        },
        "ranking_evaluation": rank_evaluation,
        "hotspot_typology": {
            "counts_by_hotspot_type": typology.get("counts_by_hotspot_type", {}),
            "counts_by_dominant_evidence": typology.get("counts_by_dominant_evidence", {}),
            "counts_by_stability_class": typology.get("counts_by_stability_class", {}),
        },
        "evidence_cards": {
            "card_count": evidence_cards.get("card_count"),
            "total_encounter_episodes_on_cards": evidence_cards.get("summary", {}).get("total_encounter_episodes_on_cards"),
            "total_backtest_supported_episodes_on_cards": evidence_cards.get("summary", {}).get(
                "total_backtest_supported_episodes_on_cards"
            ),
        },
        "stability": {
            "top_k": 50,
            "mean_previous_day_jaccard": rounded(mean(previous_overlaps), 4) if previous_overlaps else None,
            "min_previous_day_jaccard": rounded(min(previous_overlaps), 4) if previous_overlaps else None,
            "max_previous_day_jaccard": rounded(max(previous_overlaps), 4) if previous_overlaps else None,
            "mean_vs_first_day_jaccard": rounded(mean(first_overlaps), 4) if first_overlaps else None,
        },
        "ablation": [
            {
                "variant": "Density-only",
                "result": "Descriptive baseline",
                "interpretation": "High-density cells include berth/anchorage behavior and are not sufficient as safety evidence.",
            },
            {
                "variant": "Anomaly-only",
                "result": f"{anomaly_hotspots.get('hotspot_cells')} hotspot cells",
                "interpretation": "Finds behavior deviations but misses dense crossing/meeting evidence.",
            },
            {
                "variant": "Encounter-only",
                "result": f"{encounter_records:,} records; {encounter_episodes:,} episodes",
                "interpretation": "Captures close-quarters candidates but lacks behavioral context.",
            },
            {
                "variant": "Fused screening",
                "result": f"{fused_hotspots.get('hotspot_cells')} hotspot cells",
                "interpretation": "Combines anomaly evidence and future encounter-candidate evidence.",
            },
        ],
        "cases": {
            "case_count": len(cases.get("cases", [])),
            "case_ids": [case.get("case_id") for case in cases.get("cases", [])],
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(output)


if __name__ == "__main__":
    main()
