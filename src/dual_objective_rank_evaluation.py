#!/usr/bin/env python3
"""Dual-target and leave-one-day-out review-cell ranking evaluation.

The targets are geometric support and corroborated behavior evidence.  Neither
target is an accident, near-miss, enforcement, or navigation label.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from anomaly_score import score_row
from risk_hotspots import calculate_cell_components, cell_id_for_point
from traffic_patterns import learn_pattern_rows, patterns_from_rows


TOP_FRACTIONS = (0.05, 0.10, 0.20)


def ranking_scores(cells: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    scores = {
        "density_only": {},
        "behavior_only": {},
        "encounter_only": {},
        "fused": {},
    }
    for row in cells:
        cell_id = str(row["cell_id"])
        density = float(row.get("point_count", 0) or 0)
        behavior = float(row.get("anomaly_component", 0) or 0)
        encounter = float(row.get("encounter_component", 0) or 0)
        scores["density_only"][cell_id] = density
        scores["behavior_only"][cell_id] = behavior
        scores["encounter_only"][cell_id] = encounter
        scores["fused"][cell_id] = behavior + 0.75 * encounter
    return scores


def select_top_cells(scores: dict[str, float], count: int) -> set[str]:
    return set(sorted(scores, key=lambda cell_id: (-scores[cell_id], cell_id))[:count])


def capture_count(targets: list[dict[str, Any]], selected: set[str]) -> int:
    return sum(1 for target in targets if str(target["cell_id"]) in selected)


def pareto_methods(points: dict[str, tuple[float, float]]) -> list[str]:
    front: list[str] = []
    for method, point in points.items():
        dominated = False
        for other_method, other in points.items():
            if other_method == method:
                continue
            if other[0] >= point[0] and other[1] >= point[1] and other != point:
                dominated = True
                break
        if not dominated:
            front.append(method)
    return sorted(front)


def evaluate_rankings(
    cells: list[dict[str, Any]],
    encounter_targets: list[dict[str, Any]],
    behavior_targets: list[dict[str, Any]],
    top_fractions: tuple[float, ...] = TOP_FRACTIONS,
) -> dict[str, Any]:
    """Evaluate four rankings against two explicitly separate targets."""
    scores_by_method = ranking_scores(cells)
    eligible = {str(row["cell_id"]) for row in cells}
    encounter_in_eligible_cells = [
        row for row in encounter_targets if str(row["cell_id"]) in eligible
    ]
    behavior_in_eligible_cells = [
        row for row in behavior_targets if str(row["cell_id"]) in eligible
    ]
    evaluations: dict[str, list[dict[str, Any]]] = {}
    selected_by_budget: dict[str, dict[str, set[str]]] = defaultdict(dict)

    for method, scores in scores_by_method.items():
        method_rows: list[dict[str, Any]] = []
        for fraction in top_fractions:
            selected_count = max(1, math.ceil(len(cells) * fraction)) if cells else 0
            selected = select_top_cells(scores, selected_count)
            selected_by_budget[f"{fraction:.2f}"][method] = selected
            # Targets in cells that fail the pre-registered review-cell
            # eligibility gate remain in the denominator.  A ranking cannot
            # select those cells, so excluding them would overstate capture.
            encounter_count = capture_count(encounter_targets, selected)
            behavior_count = capture_count(behavior_targets, selected)
            method_rows.append(
                {
                    "top_fraction": fraction,
                    "selected_cells": selected_count,
                    "supported_encounter_capture_count": encounter_count,
                    "supported_encounter_capture_rate": (
                        round(encounter_count / len(encounter_targets), 6) if encounter_targets else None
                    ),
                    "corroborated_behavior_capture_count": behavior_count,
                    "corroborated_behavior_capture_rate": (
                        round(behavior_count / len(behavior_targets), 6) if behavior_targets else None
                    ),
                }
            )
        evaluations[method] = method_rows

    pareto_by_budget: dict[str, list[str]] = {}
    for budget_key in selected_by_budget:
        fraction = float(budget_key)
        points: dict[str, tuple[float, float]] = {}
        for method, rows in evaluations.items():
            row = next(item for item in rows if math.isclose(float(item["top_fraction"]), fraction))
            points[method] = (
                float(row["supported_encounter_capture_rate"] or 0.0),
                float(row["corroborated_behavior_capture_rate"] or 0.0),
            )
        pareto_by_budget[budget_key] = pareto_methods(points)

    return {
        "interpretation": "dual-target review prioritization; not accident or near-miss accuracy",
        "eligible_cells": len(cells),
        "supported_encounter_targets": len(encounter_targets),
        "supported_encounter_targets_in_eligible_cells": len(encounter_in_eligible_cells),
        "supported_encounter_target_eligibility_coverage": (
            round(len(encounter_in_eligible_cells) / len(encounter_targets), 6)
            if encounter_targets
            else None
        ),
        "corroborated_behavior_track_day_targets": len(behavior_targets),
        "corroborated_behavior_targets_in_eligible_cells": len(behavior_in_eligible_cells),
        "corroborated_behavior_target_eligibility_coverage": (
            round(len(behavior_in_eligible_cells) / len(behavior_targets), 6)
            if behavior_targets
            else None
        ),
        "rankings": evaluations,
        "pareto_front_by_budget": pareto_by_budget,
        "selected_cells_by_budget": {
            budget: {method: sorted(cells) for method, cells in methods.items()}
            for budget, methods in selected_by_budget.items()
        },
    }


def evaluate_fusion_claim_gate(fold_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the frozen internal multi-evidence coverage gate."""
    required_retention = 0.95
    budgets = sorted({float(row["top_fraction"]) for row in fold_rows})
    dates = sorted({str(row["held_out_date"]) for row in fold_rows})
    passing_budgets: list[float] = []
    fold_details: list[dict[str, Any]] = []
    for fraction in budgets:
        passing_dates = 0
        comparable_dates = 0
        for date in dates:
            lookup = {
                str(row["method"]): row
                for row in fold_rows
                if str(row["held_out_date"]) == date and math.isclose(float(row["top_fraction"]), fraction)
            }
            if "fused" not in lookup or "encounter_only" not in lookup:
                continue
            comparable_dates += 1
            fused = lookup["fused"]
            encounter = lookup["encounter_only"]
            fused_encounter = float(fused.get("supported_encounter_capture_rate") or 0.0)
            base_encounter = float(encounter.get("supported_encounter_capture_rate") or 0.0)
            fused_behavior = float(fused.get("corroborated_behavior_capture_rate") or 0.0)
            base_behavior = float(encounter.get("corroborated_behavior_capture_rate") or 0.0)
            passes = fused_behavior > base_behavior and (
                base_encounter == 0.0 or fused_encounter >= required_retention * base_encounter
            )
            passing_dates += int(passes)
            fold_details.append(
                {
                    "held_out_date": date,
                    "top_fraction": fraction,
                    "passes": passes,
                    "encounter_retention": fused_encounter / base_encounter if base_encounter else None,
                    "behavior_capture_delta": fused_behavior - base_behavior,
                }
            )
        majority = comparable_dates > 0 and passing_dates > comparable_dates / 2
        if majority:
            passing_budgets.append(fraction)

    passes_gate = len(passing_budgets) >= 2
    return {
        "required_encounter_retention": required_retention,
        "required_passing_budgets": 2,
        "passing_budgets": passing_budgets,
        "passes_incremental_value_gate": passes_gate,
        "paper_position": (
            "internally measured multi-evidence coverage trade-off"
            if passes_gate
            else "optional multi-evidence review view"
        ),
        "fold_details": fold_details,
    }


def date_range(start: dt.date, end: dt.date) -> list[str]:
    if end < start:
        raise ValueError("end must not precede start")
    return [(start + dt.timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]


def empty_daily_cell() -> dict[str, float]:
    return {
        "point_count": 0.0,
        "moving_count": 0.0,
        "anomaly_count": 0.0,
        "weighted_anomaly_count": 0.0,
        "weighted_anomaly_score_sum": 0.0,
        "encounter_count": 0.0,
        "encounter_score_sum": 0.0,
        "pair_opportunity_count": 0.0,
    }


def load_daily_context_evidence(
    dates: list[str],
    tables_dir: Path,
    dataset_prefix: str,
    encounter_csv: Path,
    pair_opportunity_csv: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> dict[str, dict[str, dict[str, float]]]:
    """Load exposure and encounter evidence that does not depend on behavior patterns."""
    daily: dict[str, dict[str, dict[str, float]]] = {date: {} for date in dates}

    def cell(day: str, cell_id: str) -> dict[str, float]:
        return daily.setdefault(day, {}).setdefault(cell_id, empty_daily_cell())

    for day in dates:
        path = tables_dir / f"{dataset_prefix}_{day}_grid_density.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                record = cell(day, row["cell_id"])
                record["point_count"] += float(row.get("point_count", 0) or 0)
                record["moving_count"] += float(row.get("moving_count", 0) or 0)

    with encounter_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = str(row.get("date") or str(row.get("reference_time", ""))[:10])
            if day not in daily:
                continue
            try:
                lon = (float(row["lon_a"]) + float(row["lon_b"])) / 2
                lat = (float(row["lat_a"]) + float(row["lat_b"])) / 2
            except (KeyError, TypeError, ValueError):
                continue
            cell_id = cell_id_for_point(lon, lat, bbox, cell_size_deg)
            if cell_id is None:
                continue
            record = cell(day, cell_id)
            record["encounter_count"] += 1
            record["encounter_score_sum"] += float(row.get("encounter_risk_score", 0) or 0)

    with pair_opportunity_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = str(row.get("date") or "")
            cell_id = str(row.get("cell_id") or "")
            if day in daily and cell_id:
                cell(day, cell_id)["pair_opportunity_count"] += float(row.get("pair_opportunity_count", 0) or 0)
    return daily


def add_behavior_csv_evidence(
    daily: dict[str, dict[str, dict[str, float]]],
    anomaly_csv: Path,
    low_support_weight: float = 0.25,
) -> list[dict[str, Any]]:
    """Add a precomputed behavior stream, used only for the full-window diagnostic."""
    behavior_by_track_day: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(dict)
    with anomaly_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = str(row.get("base_date_time", ""))[:10]
            cell_id = str(row.get("cell_id") or "")
            if day not in daily or not cell_id:
                continue
            record = daily.setdefault(day, {}).setdefault(cell_id, empty_daily_cell())
            score = float(row.get("anomaly_score", 0) or 0)
            corroborated = row.get("corroborated_candidate") == "1" or row.get("evidence_tier") == "corroborated_candidate"
            weight = 1.0 if corroborated else low_support_weight
            record["anomaly_count"] += 1
            record["weighted_anomaly_count"] += weight
            record["weighted_anomaly_score_sum"] += score * weight
            track_id = str(row.get("track_id") or "")
            if corroborated and track_id:
                track_cells = behavior_by_track_day[(day, track_id)]
                values = track_cells.setdefault(cell_id, [0.0, 0.0])
                values[0] += 1
                values[1] += score

    behavior_targets: list[dict[str, Any]] = []
    for (day, track_id), cells in behavior_by_track_day.items():
        representative = min(
            cells,
            key=lambda cell_id: (-cells[cell_id][0], -cells[cell_id][1], cell_id),
        )
        behavior_targets.append({"date": day, "track_id": track_id, "cell_id": representative})
    return behavior_targets


def load_daily_evidence(
    dates: list[str],
    tables_dir: Path,
    dataset_prefix: str,
    anomaly_csv: Path,
    encounter_csv: Path,
    pair_opportunity_csv: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    low_support_weight: float = 0.25,
) -> tuple[dict[str, dict[str, dict[str, float]]], list[dict[str, Any]]]:
    daily = load_daily_context_evidence(
        dates,
        tables_dir,
        dataset_prefix,
        encounter_csv,
        pair_opportunity_csv,
        bbox,
        cell_size_deg,
    )
    behavior_targets = add_behavior_csv_evidence(daily, anomaly_csv, low_support_weight)
    return daily, behavior_targets


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_fold_specific_behavior_evidence(
    *,
    training_dates: list[str],
    held_out_date: str,
    processed_dir: Path,
    dataset_prefix: str,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    pattern_min_sog: float = 1.0,
    pattern_min_points: int = 50,
    pattern_min_tracks: int = 5,
    track_min_points: int = 20,
    moving_sog: float = 1.0,
    min_anomaly_score: float = 0.35,
    low_support_weight: float = 0.25,
) -> dict[str, Any]:
    """Refit behavior support on training dates and score the complete fold.

    Training cells and scores use only the supplied training dates.  The
    held-out feature file is evaluated by that frozen fold model and is never
    added to training evidence.
    """
    if held_out_date in training_dates:
        raise ValueError("held_out_date must not be present in training_dates")
    if not training_dates:
        raise ValueError("at least one training date is required")

    training_days = [dt.date.fromisoformat(day) for day in training_dates]
    pattern_rows, pattern_stats = learn_pattern_rows(
        training_days,
        processed_dir,
        bbox,
        cell_size_deg,
        pattern_min_sog,
        pattern_min_points,
        pattern_min_tracks,
        dataset_prefix,
        track_min_points,
    )
    patterns = patterns_from_rows(pattern_rows)
    scored_dates = [*training_dates, held_out_date]
    daily_behavior: dict[str, dict[str, dict[str, float]]] = {
        day: {} for day in scored_dates
    }
    behavior_by_track_day: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(dict)
    selected_records: dict[str, list[dict[str, Any]]] = {day: [] for day in scored_dates}
    daily_scoring: list[dict[str, Any]] = []

    for day in scored_dates:
        input_rows = 0
        scorable_rows = 0
        selected_rows = 0
        corroborated_rows = 0
        feature_path = processed_dir / f"{dataset_prefix}_{day}_features.csv"
        with feature_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                input_rows += 1
                scored = score_row(row, patterns, bbox, cell_size_deg, moving_sog)
                if scored is None:
                    continue
                scorable_rows += 1
                score = float(scored["anomaly_score"])
                if score < min_anomaly_score:
                    continue
                selected_rows += 1
                cell_id = str(scored["cell_id"])
                corroborated = (
                    int(scored["corroborated_candidate"]) == 1
                    or scored["evidence_tier"] == "corroborated_candidate"
                )
                corroborated_rows += int(corroborated)
                weight = 1.0 if corroborated else low_support_weight
                record = daily_behavior[day].setdefault(cell_id, empty_daily_cell())
                record["anomaly_count"] += 1
                record["weighted_anomaly_count"] += weight
                record["weighted_anomaly_score_sum"] += score * weight

                track_id = str(scored.get("track_id") or "")
                if corroborated and track_id:
                    track_cells = behavior_by_track_day[(day, track_id)]
                    values = track_cells.setdefault(cell_id, [0.0, 0.0])
                    values[0] += 1
                    values[1] += score
                selected_records[day].append(
                    {
                        "mmsi": str(scored.get("mmsi") or ""),
                        "track_id": track_id,
                        "base_date_time": str(scored.get("base_date_time") or ""),
                        "cell_id": cell_id,
                        "anomaly_score": score,
                        "evidence_tier": str(scored["evidence_tier"]),
                        "indicator_count": int(scored["indicator_count"]),
                        "reasons": str(scored["reasons"]),
                    }
                )
        daily_scoring.append(
            {
                "date": day,
                "role": "held_out" if day == held_out_date else "training",
                "source_file": str(feature_path),
                "input_rows": input_rows,
                "scorable_rows": scorable_rows,
                "selected_behavior_rows": selected_rows,
                "corroborated_behavior_rows": corroborated_rows,
            }
        )

    targets_by_date: dict[str, list[dict[str, Any]]] = {
        day: [] for day in scored_dates
    }
    for (day, track_id), cells in behavior_by_track_day.items():
        representative = min(
            cells,
            key=lambda cell_id: (-cells[cell_id][0], -cells[cell_id][1], cell_id),
        )
        targets_by_date[day].append(
            {"date": day, "track_id": track_id, "cell_id": representative}
        )
    for rows in targets_by_date.values():
        rows.sort(key=lambda row: (str(row["track_id"]), str(row["cell_id"])))

    training_records = [
        row for day in training_dates for row in selected_records[day]
    ]
    held_out_records = selected_records[held_out_date]
    training_targets = [
        row for day in training_dates for row in targets_by_date[day]
    ]
    pattern_hash_rows = sorted(pattern_rows, key=lambda row: str(row["cell_id"]))
    provenance = {
        "evaluation_design": "fold-specific six-day traffic-pattern refit and scoring",
        "held_out_date": held_out_date,
        "training_dates": training_dates,
        "pattern_source_files": [
            str(processed_dir / f"{dataset_prefix}_{day}_tracks_min{track_min_points}.csv")
            for day in training_dates
        ],
        "feature_source_files": {
            "training": [
                str(processed_dir / f"{dataset_prefix}_{day}_features.csv")
                for day in training_dates
            ],
            "held_out": str(processed_dir / f"{dataset_prefix}_{held_out_date}_features.csv"),
        },
        "parameters": {
            "bbox": list(bbox),
            "cell_size_deg": cell_size_deg,
            "pattern_min_sog": pattern_min_sog,
            "pattern_min_points": pattern_min_points,
            "pattern_min_tracks": pattern_min_tracks,
            "track_min_points": track_min_points,
            "moving_sog": moving_sog,
            "min_anomaly_score": min_anomaly_score,
            "low_support_weight": low_support_weight,
        },
        "pattern_stats": pattern_stats,
        "pattern_model_sha256": canonical_sha256(
            {"rows": pattern_hash_rows, "stats": pattern_stats}
        ),
        "training_behavior_evidence_sha256": canonical_sha256(training_records),
        "held_out_behavior_evidence_sha256": canonical_sha256(held_out_records),
        "training_behavior_targets_sha256": canonical_sha256(training_targets),
        "held_out_behavior_targets_sha256": canonical_sha256(
            targets_by_date[held_out_date]
        ),
        "training_behavior_target_count": sum(
            len(targets_by_date[day]) for day in training_dates
        ),
        "held_out_behavior_target_count": len(targets_by_date[held_out_date]),
        "daily_scoring": daily_scoring,
    }
    return {
        "daily_behavior": daily_behavior,
        "held_out_behavior_targets": targets_by_date[held_out_date],
        "provenance": provenance,
    }


def combine_daily_evidence(
    context_daily: dict[str, dict[str, dict[str, float]]],
    behavior_daily: dict[str, dict[str, dict[str, float]]],
) -> dict[str, dict[str, dict[str, float]]]:
    combined: dict[str, dict[str, dict[str, float]]] = {}
    for day in sorted(set(context_daily) | set(behavior_daily)):
        combined[day] = {}
        for source in (context_daily.get(day, {}), behavior_daily.get(day, {})):
            for cell_id, values in source.items():
                target = combined[day].setdefault(cell_id, empty_daily_cell())
                for key in target:
                    target[key] += float(values.get(key, 0.0))
    return combined


def load_supported_encounter_targets(
    backtest_csv: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    with backtest_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row.get("observable_followup", 0) or 0) != 1 or int(row.get("supported_within_threshold", 0) or 0) != 1:
                continue
            try:
                lon = float(row["prediction_mid_lon"])
                lat = float(row["prediction_mid_lat"])
            except (KeyError, TypeError, ValueError):
                continue
            cell_id = cell_id_for_point(lon, lat, bbox, cell_size_deg)
            if cell_id is None:
                continue
            targets.append(
                {
                    "date": str(row.get("date") or str(row.get("prediction_time", ""))[:10]),
                    "episode_id": row.get("episode_id", ""),
                    "cell_id": cell_id,
                }
            )
    return targets


def aggregate_training_cells(
    daily: dict[str, dict[str, dict[str, float]]],
    training_dates: Iterable[str],
    min_exposure_points: int = 100,
    min_moving_points: int = 20,
    min_anomaly_count: int = 20,
    min_encounter_count: int = 20,
) -> list[dict[str, Any]]:
    aggregate: dict[str, dict[str, float]] = {}
    for day in training_dates:
        for cell_id, values in daily.get(day, {}).items():
            target = aggregate.setdefault(cell_id, empty_daily_cell())
            for key in target:
                target[key] += float(values.get(key, 0.0))

    cells: list[dict[str, Any]] = []
    for cell_id, values in aggregate.items():
        anomaly_count = int(values["anomaly_count"])
        encounter_count = int(values["encounter_count"])
        components = calculate_cell_components(
            anomaly_count=anomaly_count,
            weighted_anomaly_count=values["weighted_anomaly_count"],
            weighted_anomaly_score_sum=values["weighted_anomaly_score_sum"],
            point_count=values["point_count"],
            moving_count=values["moving_count"],
            encounter_count=encounter_count,
            encounter_score_sum=values["encounter_score_sum"],
            pair_opportunity_count=int(values["pair_opportunity_count"]),
        )
        if not (
            values["point_count"] >= min_exposure_points
            and values["moving_count"] >= min_moving_points
            and (anomaly_count >= min_anomaly_count or encounter_count >= min_encounter_count)
        ):
            continue
        cells.append(
            {
                "cell_id": cell_id,
                "point_count": values["point_count"],
                "anomaly_component": components["anomaly_component"],
                "encounter_component": components["encounter_component"],
            }
        )
    return cells


def pairwise_jaccard(sets: list[set[str]]) -> dict[str, float | None]:
    values: list[float] = []
    for left_index, left in enumerate(sets):
        for right in sets[left_index + 1 :]:
            union = left | right
            values.append(len(left & right) / len(union) if union else 1.0)
    return {
        "mean": round(statistics.mean(values), 6) if values else None,
        "min": round(min(values), 6) if values else None,
        "max": round(max(values), 6) if values else None,
    }


def run_leave_one_day_out(
    dates: list[str],
    context_daily: dict[str, dict[str, dict[str, float]]],
    encounter_targets: list[dict[str, Any]],
    *,
    processed_dir: Path,
    dataset_prefix: str,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    pattern_min_sog: float = 1.0,
    pattern_min_points: int = 50,
    pattern_min_tracks: int = 5,
    track_min_points: int = 20,
    moving_sog: float = 1.0,
    min_anomaly_score: float = 0.35,
    low_support_weight: float = 0.25,
) -> dict[str, Any]:
    """Run genuine fold-specific behavior refitting and held-out evaluation."""
    fold_rows: list[dict[str, Any]] = []
    selected_sets: dict[tuple[str, float], list[set[str]]] = defaultdict(list)
    fold_summaries: list[dict[str, Any]] = []
    for held_out in dates:
        training_dates = [date for date in dates if date != held_out]
        fold_behavior = build_fold_specific_behavior_evidence(
            training_dates=training_dates,
            held_out_date=held_out,
            processed_dir=processed_dir,
            dataset_prefix=dataset_prefix,
            bbox=bbox,
            cell_size_deg=cell_size_deg,
            pattern_min_sog=pattern_min_sog,
            pattern_min_points=pattern_min_points,
            pattern_min_tracks=pattern_min_tracks,
            track_min_points=track_min_points,
            moving_sog=moving_sog,
            min_anomaly_score=min_anomaly_score,
            low_support_weight=low_support_weight,
        )
        fold_daily = combine_daily_evidence(
            context_daily,
            fold_behavior["daily_behavior"],
        )
        cells = aggregate_training_cells(fold_daily, training_dates)
        held_encounter = [row for row in encounter_targets if row["date"] == held_out]
        held_behavior = fold_behavior["held_out_behavior_targets"]
        result = evaluate_rankings(cells, held_encounter, held_behavior)
        fold_summaries.append(
            {
                "held_out_date": held_out,
                "training_dates": training_dates,
                "eligible_training_cells": result["eligible_cells"],
                "supported_encounter_targets": result["supported_encounter_targets"],
                "corroborated_behavior_targets": result["corroborated_behavior_track_day_targets"],
                "behavior_refit_provenance": fold_behavior["provenance"],
            }
        )
        for method, rows in result["rankings"].items():
            for row in rows:
                fold_rows.append(
                    {
                        "held_out_date": held_out,
                        "method": method,
                        "supported_encounter_target_count": result["supported_encounter_targets"],
                        "corroborated_behavior_target_count": result["corroborated_behavior_track_day_targets"],
                        **row,
                    }
                )
        for budget, methods in result["selected_cells_by_budget"].items():
            for method, cell_ids in methods.items():
                selected_sets[(method, float(budget))].append(set(cell_ids))

    stability = [
        {
            "method": method,
            "top_fraction": fraction,
            **pairwise_jaccard(sets),
        }
        for (method, fraction), sets in sorted(selected_sets.items())
    ]
    return {
        "evaluation_design": (
            "For each held-out day, traffic-pattern cells and dominant directions "
            "are learned only from the other dates; the frozen fold model then "
            "rescored both training and held-out feature files."
        ),
        "held_out_isolation": (
            "Held-out rows do not contribute to pattern thresholds, regular cells, "
            "dominant directions, or training behavior scores."
        ),
        "folds": fold_summaries,
        "fold_rows": fold_rows,
        "ranking_stability": stability,
        "fusion_claim_gate": evaluate_fusion_claim_gate(fold_rows),
    }


def fused_only_cases(
    full_window: dict[str, Any],
    encounter_targets: list[dict[str, Any]],
    behavior_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected = full_window["selected_cells_by_budget"]["0.10"]
    cells = set(selected["fused"]) - set(selected["encounter_only"])
    encounter_counts = Counter(str(row["cell_id"]) for row in encounter_targets)
    behavior_counts = Counter(str(row["cell_id"]) for row in behavior_targets)
    rows = [
        {
            "cell_id": cell_id,
            "corroborated_behavior_track_days": behavior_counts[cell_id],
            "supported_encounter_episodes": encounter_counts[cell_id],
            "interpretation": "fused-only review cell; not an operational event label",
        }
        for cell_id in cells
        if behavior_counts[cell_id] > 0
    ]
    rows.sort(key=lambda row: (-int(row["corroborated_behavior_track_days"]), -int(row["supported_encounter_episodes"]), str(row["cell_id"])))
    return rows[:10]


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Fold-Specific Dual-Objective Ranking Evaluation",
        "",
        "Targets are strict-future geometric support and corroborated behavior track/day evidence. They are not accident or near-miss labels.",
        "Each held-out fold relearns traffic patterns from the remaining dates and rescored all fold dates with that frozen model.",
        "",
        "| Ranking | Budget | Encounter capture | Behavior capture |",
        "|---|---:|---:|---:|",
    ]
    for method, rows in result["full_window"]["rankings"].items():
        for row in rows:
            encounter = row["supported_encounter_capture_rate"]
            behavior = row["corroborated_behavior_capture_rate"]
            lines.append(
                f"| {method} | {100 * row['top_fraction']:.0f}% | "
                f"{100 * encounter:.1f}% | {100 * behavior:.1f}% |"
                if encounter is not None and behavior is not None
                else f"| {method} | {100 * row['top_fraction']:.0f}% | n/a | n/a |"
            )
    gate = result["leave_one_day_out"]["fusion_claim_gate"]
    lines.extend(
        [
            "",
            "## Pre-registered fusion claim gate",
            "",
            f"- Passes: {'yes' if gate['passes_incremental_value_gate'] else 'no'}",
            f"- Paper position: {gate['paper_position']}",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate review-cell rankings against two evidence targets.")
    parser.add_argument("--start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--end", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--dataset-prefix", default="sf_bay_ais")
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--tables-dir", type=Path, default=Path("outputs/tables"))
    parser.add_argument("--anomaly-csv", type=Path, required=True)
    parser.add_argument("--encounter-csv", type=Path, required=True)
    parser.add_argument("--pair-opportunity-csv", type=Path, required=True)
    parser.add_argument("--backtest-csv", type=Path, required=True)
    parser.add_argument("--bbox", type=float, nargs=4, required=True)
    parser.add_argument("--cell-size-deg", type=float, default=0.005)
    parser.add_argument("--pattern-min-sog", type=float, default=1.0)
    parser.add_argument("--pattern-min-points", type=int, default=50)
    parser.add_argument("--pattern-min-tracks", type=int, default=5)
    parser.add_argument("--track-min-points", type=int, default=20)
    parser.add_argument("--moving-sog", type=float, default=1.0)
    parser.add_argument("--min-anomaly-score", type=float, default=0.35)
    parser.add_argument("--low-support-weight", type=float, default=0.25)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dates = date_range(args.start, args.end)
    bbox = tuple(args.bbox)
    context_daily = load_daily_context_evidence(
        dates,
        args.tables_dir,
        args.dataset_prefix,
        args.encounter_csv,
        args.pair_opportunity_csv,
        bbox,
        args.cell_size_deg,
    )
    daily = combine_daily_evidence(context_daily, {})
    behavior_targets = add_behavior_csv_evidence(
        daily,
        args.anomaly_csv,
        args.low_support_weight,
    )
    encounter_targets = load_supported_encounter_targets(args.backtest_csv, bbox, args.cell_size_deg)
    full_cells = aggregate_training_cells(daily, dates)
    full_window = evaluate_rankings(full_cells, encounter_targets, behavior_targets)
    lodo = run_leave_one_day_out(
        dates,
        context_daily,
        encounter_targets,
        processed_dir=args.processed_dir,
        dataset_prefix=args.dataset_prefix,
        bbox=bbox,
        cell_size_deg=args.cell_size_deg,
        pattern_min_sog=args.pattern_min_sog,
        pattern_min_points=args.pattern_min_points,
        pattern_min_tracks=args.pattern_min_tracks,
        track_min_points=args.track_min_points,
        moving_sog=args.moving_sog,
        min_anomaly_score=args.min_anomaly_score,
        low_support_weight=args.low_support_weight,
    )
    result = {
        "protocol": "docs/METHOD_PROTOCOL.md",
        "interpretation": (
            "internal dual evidence-target review prioritization; not out-of-sample "
            "operational utility, safety prediction, or near-miss accuracy"
        ),
        "dates": dates,
        "full_window": full_window,
        "leave_one_day_out": lodo,
        "fused_only_cases": fused_only_cases(full_window, encounter_targets, behavior_targets),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(args.output_md, result)
    print(
        json.dumps(
            {
                "eligible_cells": full_window["eligible_cells"],
                "supported_encounter_targets": full_window["supported_encounter_targets"],
                "corroborated_behavior_targets": full_window["corroborated_behavior_track_day_targets"],
                "fusion_gate": lodo["fusion_claim_gate"]["passes_incremental_value_gate"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
