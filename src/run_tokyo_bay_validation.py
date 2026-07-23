#!/usr/bin/env python3
"""Run a reproducible Tokyo Bay supplemental-validation pipeline."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from anomaly_score import compute_anomalies
from audit import audit_csv, write_markdown as write_audit_markdown
from clean import clean_csv
from dual_objective_rank_evaluation import main as dual_objective_rank_main
from encounter_backtest import (
    backtest_episodes,
    group_episodes,
    load_encounter_rows,
    load_positions,
    summarize as summarize_backtest,
    write_csv as write_backtest_csv,
    write_markdown as write_backtest_markdown,
)
from encounter_risk import compute_encounters
from features import build_features
from filter_tracks import filter_tracks
from geometric_control_evaluation import main as geometric_control_main
from grid_density import aggregate_grid
from risk_hotspots import build_hotspots
from tokyo_bay_adapter import DEFAULT_DATASET_PREFIX, convert_parquet, parse_date
from traffic_patterns import learn_patterns


DEFAULT_INPUT = Path("data/raw/tokyo_bay/figshare_v2/ais_messages_tokyobay_2024.parquet")
DEFAULT_START = dt.date(2024, 8, 1)
DEFAULT_END = dt.date(2024, 8, 7)
DEFAULT_BBOX = (139.62, 34.90, 140.13, 35.69)

# Frozen before review-v9 headline recomputation.  Keep these explicit at every
# call site so a future library-default change cannot silently alter the paper.
STRICT_FUTURE_LOOKAHEAD_MIN = 15.0
STRICT_FUTURE_EVALUATION_STEP_S = 30
STRICT_FUTURE_MAX_INTERPOLATION_GAP_S = 180
STRICT_FUTURE_MIN_COMMON_FRACTION = 0.70
STRICT_FUTURE_MAX_UNCOVERED_GAP_S = 180
STRICT_FUTURE_PREDICTED_TIME_TOLERANCE_S = 60


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)]


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def can_reuse(paths: list[Path], overwrite: bool) -> bool:
    return not overwrite and all(path.exists() for path in paths)


def frozen_backtest_stats(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    stats = load_json(path)
    rule = stats.get("primary_observability", {})
    if not isinstance(rule, dict):
        return None
    expected = {
        "common_grid_fraction": STRICT_FUTURE_MIN_COMMON_FRACTION,
        "required_common_samples_30s_grid": 21,
        "required_common_coverage_duration_s": 630.0,
        "maximum_uncovered_gap_s": STRICT_FUTURE_MAX_UNCOVERED_GAP_S,
        "predicted_closest_time_tolerance_s": STRICT_FUTURE_PREDICTED_TIME_TOLERANCE_S,
    }
    if any(rule.get(key) != value for key, value in expected.items()):
        return None
    if not all(key in stats for key in ("future_coverage", "observability_sensitivity", "minimum_distance_grid_sensitivity")):
        return None
    return stats


def process_day(
    day: dt.date,
    dataset_prefix: str,
    processed_dir: Path,
    tables_dir: Path,
    bbox: tuple[float, float, float, float],
    min_points: int,
    cell_size_deg: float,
    overwrite: bool,
) -> dict[str, object]:
    label = day.isoformat()
    raw_csv = processed_dir / f"{dataset_prefix}_{label}.csv"
    clean_path = processed_dir / f"{dataset_prefix}_{label}_clean.csv"
    feature_path = processed_dir / f"{dataset_prefix}_{label}_features.csv"
    tracks_path = processed_dir / f"{dataset_prefix}_{label}_tracks_min{min_points}.csv"
    clean_stats_path = tables_dir / f"{dataset_prefix}_{label}_clean_stats.json"
    audit_json_path = tables_dir / f"{dataset_prefix}_{label}_clean_audit.json"
    audit_md_path = tables_dir / f"{dataset_prefix}_{label}_clean_audit.md"
    feature_stats_path = tables_dir / f"{dataset_prefix}_{label}_features_stats.json"
    grid_csv_path = tables_dir / f"{dataset_prefix}_{label}_grid_density.csv"
    grid_stats_path = tables_dir / f"{dataset_prefix}_{label}_grid_density_stats.json"
    tracks_stats_path = tables_dir / f"{dataset_prefix}_{label}_tracks_min{min_points}_stats.json"

    if not raw_csv.exists():
        raise FileNotFoundError(f"converted Tokyo Bay day is missing: {raw_csv}")

    if can_reuse([clean_path, clean_stats_path], overwrite):
        clean_stats = load_json(clean_stats_path)
    else:
        clean_stats = clean_csv(raw_csv, clean_path, clean_stats_path, bbox)

    if not can_reuse([audit_json_path, audit_md_path], overwrite):
        clean_audit = audit_csv(clean_path)
        audit_json_path.parent.mkdir(parents=True, exist_ok=True)
        audit_json_path.write_text(
            json.dumps(clean_audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        write_audit_markdown(clean_audit, audit_md_path)

    if can_reuse([feature_path, feature_stats_path], overwrite):
        feature_stats = load_json(feature_stats_path)
    else:
        feature_stats = build_features(
            clean_path,
            feature_path,
            feature_stats_path,
            max_time_gap_s=1800,
            max_distance_gap_nm=5.0,
            max_implied_speed_kn=60.0,
        )

    if can_reuse([grid_csv_path, grid_stats_path], overwrite):
        grid_stats = load_json(grid_stats_path)
    else:
        grid_stats = aggregate_grid(feature_path, grid_csv_path, grid_stats_path, bbox, cell_size_deg)

    if can_reuse([tracks_path, tracks_stats_path], overwrite):
        tracks_stats = load_json(tracks_stats_path)
    else:
        tracks_stats = filter_tracks(feature_path, tracks_path, tracks_stats_path, min_points)

    return {
        "date": label,
        "converted_rows": int(clean_stats["input_rows"]),
        "clean_rows": int(clean_stats["output_rows"]),
        "trajectory_segments": int(feature_stats["unique_tracks"]),
        "grid_cells": int(grid_stats["nonempty_cells"]),
        "min_points_tracks": int(tracks_stats["output_tracks"]),
        "min_points_rows": int(tracks_stats["output_rows"]),
    }


def build_backtest(
    start: dt.date,
    end: dt.date,
    dataset_prefix: str,
    processed_dir: Path,
    encounter_csv: Path,
    output_csv: Path,
    stats_json: Path,
    summary_md: Path,
    overwrite: bool,
) -> dict[str, object]:
    if can_reuse([output_csv, stats_json, summary_md], overwrite):
        reusable = frozen_backtest_stats(stats_json)
        if reusable is not None:
            return reusable

    encounter_rows = load_encounter_rows(encounter_csv)
    episodes = group_episodes(encounter_rows, episode_gap_min=15.0)
    wanted_mmsi = {str(row["mmsi_a"]) for row in episodes} | {str(row["mmsi_b"]) for row in episodes}
    positions = load_positions(
        processed_dir,
        start,
        end,
        lookahead_min=STRICT_FUTURE_LOOKAHEAD_MIN,
        wanted_mmsi=wanted_mmsi,
        dataset_prefix=dataset_prefix,
    )
    backtest_rows = backtest_episodes(
        episodes,
        positions,
        lookahead_min=STRICT_FUTURE_LOOKAHEAD_MIN,
        evaluation_step_s=STRICT_FUTURE_EVALUATION_STEP_S,
        max_interpolation_gap_s=STRICT_FUTURE_MAX_INTERPOLATION_GAP_S,
        support_distance_nm=0.5,
        near_distance_nm=1.0,
        min_common_fraction=STRICT_FUTURE_MIN_COMMON_FRACTION,
        max_uncovered_gap_s=STRICT_FUTURE_MAX_UNCOVERED_GAP_S,
        predicted_time_tolerance_s=STRICT_FUTURE_PREDICTED_TIME_TOLERANCE_S,
    )
    stats = summarize_backtest(
        encounter_rows,
        backtest_rows,
        support_distance_nm=0.5,
        near_distance_nm=1.0,
        lookahead_min=STRICT_FUTURE_LOOKAHEAD_MIN,
        evaluation_step_s=STRICT_FUTURE_EVALUATION_STEP_S,
        max_interpolation_gap_s=STRICT_FUTURE_MAX_INTERPOLATION_GAP_S,
        min_common_fraction=STRICT_FUTURE_MIN_COMMON_FRACTION,
        max_uncovered_gap_s=STRICT_FUTURE_MAX_UNCOVERED_GAP_S,
        predicted_time_tolerance_s=STRICT_FUTURE_PREDICTED_TIME_TOLERANCE_S,
    )
    stats["dataset_prefix"] = dataset_prefix
    write_backtest_csv(output_csv, backtest_rows)
    stats_json.parent.mkdir(parents=True, exist_ok=True)
    stats_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_backtest_markdown(summary_md, stats)
    return stats


def build_geometric_controls(
    *,
    start: dt.date,
    end: dt.date,
    dataset_prefix: str,
    processed_dir: Path,
    opportunity_records_csv: Path,
    output_json: Path,
    output_csv: Path,
    overwrite: bool,
) -> dict[str, object]:
    if can_reuse([output_json, output_csv], overwrite):
        return load_json(output_json)
    geometric_control_main(
        [
            "--opportunity-csv",
            str(opportunity_records_csv),
            "--processed-dir",
            str(processed_dir),
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--dataset-prefix",
            dataset_prefix,
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--lookahead-min",
            str(STRICT_FUTURE_LOOKAHEAD_MIN),
            "--evaluation-step-s",
            str(STRICT_FUTURE_EVALUATION_STEP_S),
            "--max-interpolation-gap-s",
            str(STRICT_FUTURE_MAX_INTERPOLATION_GAP_S),
            "--min-common-fraction",
            str(STRICT_FUTURE_MIN_COMMON_FRACTION),
            "--max-uncovered-gap-s",
            str(STRICT_FUTURE_MAX_UNCOVERED_GAP_S),
            "--predicted-time-tolerance-s",
            str(STRICT_FUTURE_PREDICTED_TIME_TOLERANCE_S),
        ]
    )
    return load_json(output_json)


def build_dual_objective_ranking(
    *,
    start: dt.date,
    end: dt.date,
    dataset_prefix: str,
    tables_dir: Path,
    anomaly_csv: Path,
    encounter_csv: Path,
    pair_opportunity_csv: Path,
    backtest_csv: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    output_json: Path,
    output_md: Path,
    overwrite: bool,
) -> dict[str, object]:
    if can_reuse([output_json, output_md], overwrite):
        return load_json(output_json)
    dual_objective_rank_main(
        [
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--dataset-prefix",
            dataset_prefix,
            "--tables-dir",
            str(tables_dir),
            "--anomaly-csv",
            str(anomaly_csv),
            "--encounter-csv",
            str(encounter_csv),
            "--pair-opportunity-csv",
            str(pair_opportunity_csv),
            "--backtest-csv",
            str(backtest_csv),
            "--bbox",
            *(str(value) for value in bbox),
            "--cell-size-deg",
            str(cell_size_deg),
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ]
    )
    return load_json(output_json)


def write_validation_markdown(path: Path, summary: dict[str, object]) -> None:
    results: dict[str, object] = summary["headline_results"]  # type: ignore[assignment]
    lines = [
        "# Tokyo Bay Supplemental Validation",
        "",
        "This run applies the same auditable screening workflow to an open Asian port-water dataset.",
        "It demonstrates cross-source executability and common output-schema portability, not threshold transfer, "
        "accident probability, a near-miss label, enforcement, or certified collision avoidance.",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    labels = [
        ("Converted AIS points", "converted_ais_points"),
        ("Clean AIS points", "clean_ais_points"),
        ("Trajectory segments", "trajectory_segments"),
        ("Segments with at least 20 points", "min20_segments"),
        ("Regular traffic-pattern cells", "normal_traffic_pattern_cells"),
        ("Anomaly-candidate points", "anomaly_candidate_points"),
        ("CPA/TCPA candidate records", "encounter_candidate_records"),
        ("De-duplicated encounter episodes", "encounter_audit_episodes"),
        ("Complete synchronized pair opportunities", "evaluated_pair_opportunities"),
        ("Primary-observable strict-future episodes", "backtest_observable_episodes"),
        ("Backtest-supported episodes within 0.5 nm", "backtest_supported_episodes"),
        ("Strict-future geometric support rate", "backtest_support_rate_observable"),
        ("Matched control pairs with both anchors observable", "control_matched_pairs_both_observable"),
        ("Candidate/control geometric lift within 0.5 nm", "control_0_5nm_lift"),
        ("Fused screening hotspot cells", "fused_hotspot_cells"),
        ("Fusion incremental-value claim gate", "fusion_incremental_value_gate_passed"),
        ("Fusion paper position", "fusion_paper_position"),
    ]
    for label, key in labels:
        value = results[key]
        if isinstance(value, float):
            rendered = f"{value:.4f}"
        elif isinstance(value, int):
            rendered = f"{value:,}"
        else:
            rendered = str(value)
        lines.append(f"| {label} | {rendered} |")

    lines.extend(
        [
            "",
            "## Comparability boundary",
            "",
            "- The source provides positions, POSIX timestamps, MMSI, self-reported SOG, vessel type, and destination.",
            "- COG and heading are absent; movement bearing is derived from consecutive positions before traffic-pattern and CPA/TCPA screening.",
            "- Missing SOG and invalid identifiers are handled by the same conservative cleaning rules used for the NOAA benchmark.",
            "- Counts support cross-source executability only and are not equalized port-safety performance metrics.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_validation(
    input_parquet: Path,
    start: dt.date,
    end: dt.date,
    bbox: tuple[float, float, float, float],
    dataset_prefix: str,
    processed_dir: Path,
    tables_dir: Path,
    web_dir: Path,
    summary_json: Path,
    summary_md: Path,
    min_points: int,
    cell_size_deg: float,
    overwrite: bool,
) -> dict[str, object]:
    days = iter_dates(start, end)
    range_label = f"{start.isoformat()}_to_{end.isoformat()}"
    range_prefix = f"{dataset_prefix}_{range_label}"
    layer_prefix = dataset_prefix.removesuffix("_ais")
    adapter_stats_path = tables_dir / f"{range_prefix}_adapter_stats.json"
    converted_paths = [processed_dir / f"{dataset_prefix}_{day.isoformat()}.csv" for day in days]

    print(f"[1/9] Normalize Tokyo Bay Parquet: {start} to {end}", flush=True)
    if can_reuse(converted_paths + [adapter_stats_path], overwrite):
        adapter_stats = load_json(adapter_stats_path)
    else:
        adapter_stats = convert_parquet(
            input_path=input_parquet,
            output_dir=processed_dir,
            start=start,
            end=end,
            bbox=bbox,
            stats_path=adapter_stats_path,
            dataset_prefix=dataset_prefix,
            overwrite=overwrite,
        )

    daily = []
    for index, day in enumerate(days, start=1):
        print(f"[2/9] Preprocess day {index}/{len(days)}: {day}", flush=True)
        daily.append(
            process_day(
                day,
                dataset_prefix,
                processed_dir,
                tables_dir,
                bbox,
                min_points,
                cell_size_deg,
                overwrite,
            )
        )

    print("[3/9] Learn regular traffic-pattern cells", flush=True)
    patterns_csv = tables_dir / f"{range_prefix}_traffic_patterns.csv"
    patterns_geojson = web_dir / f"{layer_prefix}_traffic_patterns_{range_label}.geojson"
    patterns_stats_json = tables_dir / f"{range_prefix}_traffic_patterns_stats.json"
    if can_reuse([patterns_csv, patterns_geojson, patterns_stats_json], overwrite):
        pattern_stats = load_json(patterns_stats_json)
    else:
        pattern_stats = learn_patterns(
            start,
            end,
            processed_dir,
            patterns_csv,
            patterns_geojson,
            patterns_stats_json,
            bbox,
            cell_size_deg,
            min_sog=1.0,
            min_points=50,
            min_tracks=5,
            dataset_prefix=dataset_prefix,
            track_min_points=min_points,
        )

    print("[4/9] Score anomaly-candidate points", flush=True)
    anomaly_csv = tables_dir / f"{range_prefix}_anomaly_points.csv"
    anomaly_geojson = web_dir / f"{layer_prefix}_anomaly_points_{range_label}.geojson"
    anomaly_stats_json = tables_dir / f"{range_prefix}_anomaly_stats.json"
    if can_reuse([anomaly_csv, anomaly_geojson, anomaly_stats_json], overwrite):
        anomaly_stats = load_json(anomaly_stats_json)
    else:
        anomaly_stats = compute_anomalies(
            start,
            end,
            processed_dir,
            patterns_csv,
            anomaly_csv,
            anomaly_geojson,
            anomaly_stats_json,
            bbox,
            cell_size_deg,
            moving_sog=1.0,
            min_score=0.35,
            geojson_limit=2000,
            dataset_prefix=dataset_prefix,
        )

    print("[5/9] Screen CPA/TCPA future encounter candidates", flush=True)
    encounter_csv = tables_dir / f"{range_prefix}_encounters.csv"
    pair_opportunity_csv = tables_dir / f"{range_prefix}_pair_opportunities.csv"
    opportunity_records_csv = tables_dir / f"{range_prefix}_pair_opportunity_records.csv"
    encounter_geojson = web_dir / f"{layer_prefix}_encounters_{range_label}.geojson"
    encounter_stats_json = tables_dir / f"{range_prefix}_encounters_stats.json"
    if can_reuse(
        [
            encounter_csv,
            pair_opportunity_csv,
            opportunity_records_csv,
            encounter_geojson,
            encounter_stats_json,
        ],
        overwrite,
    ):
        encounter_stats = load_json(encounter_stats_json)
    else:
        encounter_stats = compute_encounters(
            start,
            end,
            processed_dir,
            encounter_csv,
            encounter_geojson,
            encounter_stats_json,
            time_bin_seconds=60,
            spatial_cell_deg=0.02,
            min_sog=1.0,
            max_current_distance_nm=2.0,
            dcpa_threshold_nm=0.5,
            tcpa_threshold_min=15.0,
            geojson_limit=2000,
            dataset_prefix=dataset_prefix,
            max_state_skew_s=60,
            opportunity_csv=pair_opportunity_csv,
            opportunity_records_csv=opportunity_records_csv,
            analysis_bbox=bbox,
            analysis_cell_size_deg=cell_size_deg,
        )

    print("[6/9] Fuse anomaly and encounter evidence into hotspots", flush=True)
    hotspot_csv = tables_dir / f"{range_prefix}_fused_risk_hotspots.csv"
    hotspot_geojson = web_dir / f"{layer_prefix}_fused_risk_hotspots_{range_label}.geojson"
    hotspot_stats_json = tables_dir / f"{range_prefix}_fused_risk_hotspots_stats.json"
    if can_reuse([hotspot_csv, hotspot_geojson, hotspot_stats_json], overwrite):
        hotspot_stats = load_json(hotspot_stats_json)
    else:
        hotspot_stats = build_hotspots(
            start,
            end,
            tables_dir,
            anomaly_csv,
            encounter_csv,
            hotspot_csv,
            hotspot_geojson,
            hotspot_stats_json,
            bbox,
            cell_size_deg,
            top_percent=0.10,
            min_exposure_points=100,
            min_moving_points=20,
            min_anomaly_count=20,
            min_encounter_count=20,
            encounter_weight=0.75,
            dataset_prefix=dataset_prefix,
            pair_opportunity_csv=pair_opportunity_csv,
            low_support_weight=0.25,
        )

    print("[7/9] Run frozen strict-future synchronized geometric check", flush=True)
    backtest_csv = tables_dir / f"{range_prefix}_encounter_backtest.csv"
    backtest_stats_json = tables_dir / f"{range_prefix}_encounter_backtest_stats.json"
    backtest_md = tables_dir / f"{range_prefix}_encounter_backtest.md"
    backtest_stats = build_backtest(
        start,
        end,
        dataset_prefix,
        processed_dir,
        encounter_csv,
        backtest_csv,
        backtest_stats_json,
        backtest_md,
        overwrite,
    )

    print("[8/9] Evaluate matched non-candidate geometric controls", flush=True)
    control_json = tables_dir / f"{range_prefix}_geometric_control_evaluation.json"
    control_csv = tables_dir / f"{range_prefix}_geometric_control_anchors.csv"
    control_stats = build_geometric_controls(
        start=start,
        end=end,
        dataset_prefix=dataset_prefix,
        processed_dir=processed_dir,
        opportunity_records_csv=opportunity_records_csv,
        output_json=control_json,
        output_csv=control_csv,
        overwrite=overwrite,
    )

    print("[9/9] Evaluate dual-objective ranking and leave-one-day-out stability", flush=True)
    dual_rank_json = tables_dir / f"{range_prefix}_dual_objective_rank_evaluation.json"
    dual_rank_md = tables_dir / f"{range_prefix}_dual_objective_rank_evaluation.md"
    dual_rank_stats = build_dual_objective_ranking(
        start=start,
        end=end,
        dataset_prefix=dataset_prefix,
        tables_dir=tables_dir,
        anomaly_csv=anomaly_csv,
        encounter_csv=encounter_csv,
        pair_opportunity_csv=pair_opportunity_csv,
        backtest_csv=backtest_csv,
        bbox=bbox,
        cell_size_deg=cell_size_deg,
        output_json=dual_rank_json,
        output_md=dual_rank_md,
        overwrite=overwrite,
    )

    converted_rows = sum(int(item["converted_rows"]) for item in daily)
    clean_rows = sum(int(item["clean_rows"]) for item in daily)
    trajectory_segments = sum(int(item["trajectory_segments"]) for item in daily)
    min20_segments = sum(int(item["min_points_tracks"]) for item in daily)
    anomaly_counters: dict[str, object] = anomaly_stats.get("counters", {})  # type: ignore[assignment]
    qc_drop_counts = {"drop_missing_sog": 0, "drop_invalid_sog": 0}
    for day in days:
        clean_stats = load_json(tables_dir / f"{dataset_prefix}_{day.isoformat()}_clean_stats.json")
        counters = clean_stats.get("counters", {})
        for key in qc_drop_counts:
            qc_drop_counts[key] += int(counters.get(key, 0))
    future_coverage = backtest_stats.get("future_coverage", {})
    common_samples = future_coverage.get("common_sample_count_30s", {}) if isinstance(future_coverage, dict) else {}
    coverage_duration = future_coverage.get("common_coverage_duration_s", {}) if isinstance(future_coverage, dict) else {}
    observability_sensitivity = backtest_stats.get("observability_sensitivity", {})
    grid_sensitivity = backtest_stats.get("minimum_distance_grid_sensitivity", {})
    control_matching = control_stats.get("matching", {})
    control_outcomes = control_stats.get("matched_outcomes", {})
    control_half_nm = control_outcomes.get("within_0_5_nm", {}) if isinstance(control_outcomes, dict) else {}
    control_one_nm = control_outcomes.get("within_1_0_nm", {}) if isinstance(control_outcomes, dict) else {}
    leave_one_day_out = dual_rank_stats.get("leave_one_day_out", {})
    fusion_gate = leave_one_day_out.get("fusion_claim_gate", {}) if isinstance(leave_one_day_out, dict) else {}
    headline_results = {
        "converted_ais_points": converted_rows,
        "clean_ais_points": clean_rows,
        "trajectory_segments": trajectory_segments,
        "min20_segments": min20_segments,
        "normal_traffic_pattern_cells": int(pattern_stats["normal_route_cells"]),
        "high_confidence_pattern_cells": int(pattern_stats["high_confidence_route_cells"]),
        "anomaly_candidate_points": int(anomaly_counters.get("anomaly_rows", 0)),
        "encounter_candidate_records": int(encounter_stats["encounters"]),
        "encounter_audit_episodes": int(encounter_stats["deduplicated_encounter_episodes"]),
        "evaluated_pair_opportunities": int(encounter_stats["pair_opportunity_records"]),
        "backtest_observable_episodes": int(backtest_stats["episodes_with_aligned_followup"]),
        "backtest_observable_rate": backtest_stats.get("observable_rate"),
        "backtest_supported_episodes": int(backtest_stats["supported_episodes_within_threshold"]),
        "backtest_support_rate_observable": float(backtest_stats["support_rate_observable"]),
        "backtest_near_supported_episodes": int(backtest_stats["near_supported_episodes_within_1nm"]),
        "backtest_near_support_rate_observable": backtest_stats.get("near_support_rate_observable"),
        "backtest_common_samples_30s_median": common_samples.get("median"),
        "backtest_common_coverage_duration_s_median": coverage_duration.get("median"),
        "observability_sensitivity_50pct_episodes": observability_sensitivity.get("50pct", {}).get("observable_episodes") if isinstance(observability_sensitivity, dict) else None,
        "observability_sensitivity_70pct_episodes": observability_sensitivity.get("70pct_primary", {}).get("observable_episodes") if isinstance(observability_sensitivity, dict) else None,
        "observability_sensitivity_90pct_episodes": observability_sensitivity.get("90pct", {}).get("observable_episodes") if isinstance(observability_sensitivity, dict) else None,
        "grid_10s_supported_episodes": grid_sensitivity.get("10", {}).get("supported_within_threshold") if isinstance(grid_sensitivity, dict) else None,
        "grid_30s_supported_episodes": grid_sensitivity.get("30", {}).get("supported_within_threshold") if isinstance(grid_sensitivity, dict) else None,
        "grid_60s_supported_episodes": grid_sensitivity.get("60", {}).get("supported_within_threshold") if isinstance(grid_sensitivity, dict) else None,
        "control_matched_pairs_both_observable": control_matching.get("matched_pairs_with_both_strict_future_observable") if isinstance(control_matching, dict) else None,
        "control_0_5nm_candidate_rate": control_half_nm.get("candidate_rate") if isinstance(control_half_nm, dict) else None,
        "control_0_5nm_control_rate": control_half_nm.get("control_rate") if isinstance(control_half_nm, dict) else None,
        "control_0_5nm_lift": control_half_nm.get("lift") if isinstance(control_half_nm, dict) else None,
        "control_1_0nm_candidate_rate": control_one_nm.get("candidate_rate") if isinstance(control_one_nm, dict) else None,
        "control_1_0nm_control_rate": control_one_nm.get("control_rate") if isinstance(control_one_nm, dict) else None,
        "control_1_0nm_lift": control_one_nm.get("lift") if isinstance(control_one_nm, dict) else None,
        "fused_hotspot_cells": int(hotspot_stats["hotspot_cells"]),
        "fusion_incremental_value_gate_passed": fusion_gate.get("passes_incremental_value_gate") if isinstance(fusion_gate, dict) else None,
        "fusion_paper_position": fusion_gate.get("paper_position") if isinstance(fusion_gate, dict) else None,
    }
    summary: dict[str, object] = {
        "status": "completed",
        "purpose": "Asian port-water cross-source executability and common output-schema generation demonstration",
        "dataset_prefix": dataset_prefix,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bbox": list(bbox),
        "source_adapter": adapter_stats,
        "quality_control_drop_counts": qc_drop_counts,
        "daily_preprocessing": daily,
        "headline_results": headline_results,
        "strict_future_validation": {
            "primary_observability": backtest_stats.get("primary_observability"),
            "future_coverage": future_coverage,
            "observability_sensitivity": observability_sensitivity,
            "minimum_distance_grid_sensitivity": grid_sensitivity,
        },
        "geometric_controls": control_stats,
        "dual_objective_ranking": dual_rank_stats,
        "fusion_claim_gate": fusion_gate,
        "comparability": {
            "shared_stages": [
                "quality control",
                "trajectory segmentation",
                "traffic-pattern learning",
                "anomaly-candidate scoring",
                "CPA/TCPA future encounter-candidate screening",
                "strict-future synchronized geometric check",
                "fused hotspot screening",
            ],
            "source_sog": "self-reported",
            "source_cog": "not available",
            "source_heading": "not available",
            "direction_used": "derived from consecutive positions",
            "interpretation": "cross-source executability and common output-schema portability; not threshold transfer or equalized safety performance",
        },
        "key_outputs": {
            "patterns": str(patterns_csv),
            "anomalies": str(anomaly_csv),
            "encounters": str(encounter_csv),
            "pair_opportunities": str(pair_opportunity_csv),
            "pair_opportunity_records": str(opportunity_records_csv),
            "backtest_records": str(backtest_csv),
            "backtest_stats": str(backtest_stats_json),
            "geometric_control_evaluation": str(control_json),
            "geometric_control_anchors": str(control_csv),
            "dual_objective_rank_evaluation": str(dual_rank_json),
            "dual_objective_rank_summary": str(dual_rank_md),
            "hotspots": str(hotspot_csv),
            "summary_markdown": str(summary_md),
        },
    }
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_validation_markdown(summary_md, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Tokyo Bay supplemental-validation pipeline.")
    parser.add_argument("--input-parquet", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--start", type=parse_date, default=DEFAULT_START)
    parser.add_argument("--end", type=parse_date, default=DEFAULT_END)
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        default=DEFAULT_BBOX,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
    )
    parser.add_argument("--dataset-prefix", default=DEFAULT_DATASET_PREFIX)
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--tables-dir", type=Path, default=Path("outputs/tables"))
    parser.add_argument("--web-dir", type=Path, default=Path("outputs/web"))
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--summary-md", type=Path)
    parser.add_argument("--min-points", type=int, default=20)
    parser.add_argument("--cell-size-deg", type=float, default=0.005)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    range_label = f"{args.start.isoformat()}_to_{args.end.isoformat()}"
    range_prefix = f"{args.dataset_prefix}_{range_label}"
    summary_json = args.summary_json or args.tables_dir / f"{range_prefix}_validation_summary.json"
    summary_md = args.summary_md or args.tables_dir / f"{range_prefix}_validation_summary.md"
    summary = run_validation(
        input_parquet=args.input_parquet,
        start=args.start,
        end=args.end,
        bbox=tuple(args.bbox),
        dataset_prefix=args.dataset_prefix,
        processed_dir=args.processed_dir,
        tables_dir=args.tables_dir,
        web_dir=args.web_dir,
        summary_json=summary_json,
        summary_md=summary_md,
        min_points=args.min_points,
        cell_size_deg=args.cell_size_deg,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary["headline_results"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
