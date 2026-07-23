#!/usr/bin/env python3
"""Matched non-candidate geometric controls for encounter screening.

This module implements the frozen review-v9 control protocol.  It compares
candidate and non-candidate vessel-pair opportunity anchors using the same
strict-future trajectory reconstruction as :mod:`encounter_backtest`.

The outcomes are future geometric separation only.  They are not accident,
near-miss, enforcement, collision-avoidance, or navigation labels.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import hashlib
import json
import math
import random
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

import encounter_backtest


PAIR_EPISODE_GAP_MIN = 15.0
CONTROL_EXCLUSION_MIN = 15.0
CONTROL_THINNING_MIN = 15.0
SPATIAL_BLOCK_DEG = 0.05
FALLBACK_LOCAL_EXPOSURE_CELL_DEG = 0.005
DISTANCE_BAND_NM = 0.5
UTC_BLOCK_HOURS = 4
DEFAULT_BOOTSTRAP_SEED = 20260722
DEFAULT_BOOTSTRAP_ITERATIONS = 2000


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def parse_datetime(value: object) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    rendered = str(value or "").strip()
    if not rendered:
        raise ValueError("missing timestamp")
    try:
        parsed = dt.datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {rendered}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def optional_float(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def required_float(value: object, name: str) -> float:
    parsed = optional_float(value)
    if parsed is None:
        raise ValueError(f"missing or non-finite {name}")
    return parsed


def parse_candidate_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    rendered = str(value or "").strip().lower()
    if rendered in {"1", "true", "yes", "y"}:
        return True
    if rendered in {"0", "false", "no", "n", ""}:
        return False
    raise ValueError(f"invalid is_candidate value: {value}")


def _midpoint(row: Mapping[str, object], axis: str) -> float:
    direct = optional_float(row.get(f"{axis}_mid"))
    if direct is not None:
        return direct
    left = optional_float(row.get(f"{axis}_a"))
    right = optional_float(row.get(f"{axis}_b"))
    if left is None or right is None:
        raise ValueError(f"missing {axis}_mid and {axis}_a/{axis}_b")
    return (left + right) / 2


def normalize_opportunity_rows(
    rows: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Validate and normalize detailed opportunity rows.

    The current encounter extractor writes ``cell_id`` and
    ``source_state_skew_s``.  The longer descriptive aliases
    ``analysis_cell_id`` and ``state_skew_s`` are accepted as well.
    """
    normalized: list[dict[str, object]] = []
    for source_index, source in enumerate(rows, start=1):
        try:
            reference_time = parse_datetime(source.get("reference_time") or source.get("time_bin"))
            source_date = str(source.get("date") or reference_time.date().isoformat())
            if source_date != reference_time.date().isoformat():
                raise ValueError(
                    f"date {source_date} does not match reference_time {reference_time}"
                )
            mmsi_a, mmsi_b = sorted(
                (str(source.get("mmsi_a") or "").strip(), str(source.get("mmsi_b") or "").strip())
            )
            if not mmsi_a or not mmsi_b or mmsi_a == mmsi_b:
                raise ValueError("opportunity requires two distinct MMSIs")
            current_distance = required_float(source.get("current_distance_nm"), "current_distance_nm")
            if current_distance < 0:
                raise ValueError("current_distance_nm must be non-negative")
            lon_mid = _midpoint(source, "lon")
            lat_mid = _midpoint(source, "lat")
            if not (-180 <= lon_mid <= 180 and -90 <= lat_mid <= 90):
                raise ValueError("opportunity midpoint is outside valid longitude/latitude bounds")
            analysis_cell_id = str(
                source.get("analysis_cell_id") or source.get("cell_id") or ""
            ).strip()
            state_skew = optional_float(
                source.get("state_skew_s")
                if source.get("state_skew_s") not in (None, "")
                else source.get("source_state_skew_s")
            )
            normalized.append(
                {
                    "date": source_date,
                    "reference_time": reference_time,
                    "mmsi_a": mmsi_a,
                    "mmsi_b": mmsi_b,
                    "current_distance_nm": current_distance,
                    "dcpa_nm": optional_float(source.get("dcpa_nm")),
                    "tcpa_min": optional_float(source.get("tcpa_min")),
                    "is_candidate": parse_candidate_flag(source.get("is_candidate")),
                    "analysis_cell_id": analysis_cell_id,
                    "lon_mid": lon_mid,
                    "lat_mid": lat_mid,
                    "state_skew_s": state_skew,
                    "relative_speed_kn": optional_float(source.get("relative_speed_kn")),
                    "closing_speed_kn": optional_float(source.get("closing_speed_kn")),
                    "encounter_risk_score": optional_float(source.get("encounter_risk_score")),
                    "source_row_number": source_index,
                }
            )
        except ValueError as exc:
            raise ValueError(f"Invalid opportunity row {source_index}: {exc}") from exc

    normalized.sort(
        key=lambda row: (
            row["reference_time"],
            str(row["mmsi_a"]),
            str(row["mmsi_b"]),
            float(row["current_distance_nm"]),
            int(row["source_row_number"]),
        )
    )
    for index, row in enumerate(normalized, start=1):
        row["opportunity_id"] = f"pair_opportunity_{index:07d}"
    return normalized


def load_opportunities(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return normalize_opportunity_rows(csv.DictReader(handle))


def spatial_block(lon: float, lat: float) -> tuple[int, int]:
    return math.floor(lon / SPATIAL_BLOCK_DEG), math.floor(lat / SPATIAL_BLOCK_DEG)


def _exposure_tertiles(
    cell_counts: Mapping[tuple[str, str], int],
) -> dict[tuple[str, str], int]:
    """Assign deterministic within-day tertiles using tied mid-ranks.

    Local exposure is the number of complete opportunity records in the
    extractor's analysis cell on that date.  Cells with equal exposure always
    receive the same tertile.  A day containing one exposure cell receives
    tertile 2.
    """
    by_date: dict[str, list[tuple[tuple[str, str], int]]] = {}
    for key, count in cell_counts.items():
        by_date.setdefault(key[0], []).append((key, count))

    result: dict[tuple[str, str], int] = {}
    for entries in by_date.values():
        ordered_counts = sorted(count for _, count in entries)
        total = len(ordered_counts)
        for key, count in entries:
            lower = bisect.bisect_left(ordered_counts, count)
            upper = bisect.bisect_right(ordered_counts, count) - 1
            midrank_percentile = (((lower + upper) / 2) + 0.5) / total
            result[key] = min(3, int(midrank_percentile * 3) + 1)
    return result


def assign_matching_strata(
    rows: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    prepared = [dict(row) for row in rows]
    cell_counts: Counter[tuple[str, str]] = Counter()
    row_blocks: list[tuple[int, int]] = []
    row_local_cells: list[str] = []
    for row in prepared:
        lon = float(row["lon_mid"])
        lat = float(row["lat_mid"])
        block = spatial_block(lon, lat)
        row_blocks.append(block)
        local_cell = str(row.get("analysis_cell_id") or "").strip()
        if not local_cell:
            local_cell = (
                f"fallback_x{math.floor(lon / FALLBACK_LOCAL_EXPOSURE_CELL_DEG)}_"
                f"y{math.floor(lat / FALLBACK_LOCAL_EXPOSURE_CELL_DEG)}"
            )
        row_local_cells.append(local_cell)
        cell_counts[(str(row["date"]), local_cell)] += 1
    tertiles = _exposure_tertiles(cell_counts)

    for row, block, local_cell in zip(prepared, row_blocks, row_local_cells, strict=True):
        reference_time: dt.datetime = row["reference_time"]  # type: ignore[assignment]
        exposure_key = (str(row["date"]), local_cell)
        distance_band = math.floor(float(row["current_distance_nm"]) / DISTANCE_BAND_NM)
        stratum = (
            str(row["date"]),
            reference_time.hour // UTC_BLOCK_HOURS,
            block[0],
            block[1],
            distance_band,
            tertiles[exposure_key],
        )
        row.update(
            {
                "utc_4h_block": stratum[1],
                "spatial_block_lon": block[0],
                "spatial_block_lat": block[1],
                "local_exposure_cell_id": local_cell,
                "current_distance_band": distance_band,
                "local_opportunity_exposure": cell_counts[exposure_key],
                "local_exposure_tertile": tertiles[exposure_key],
                "matching_stratum": stratum,
                "matching_stratum_id": (
                    f"{stratum[0]}|h{stratum[1]}|x{stratum[2]}|y{stratum[3]}|"
                    f"d{stratum[4]}|e{stratum[5]}"
                ),
            }
        )
    return prepared


def _pair_day(row: Mapping[str, object]) -> tuple[str, str, str]:
    return str(row["date"]), str(row["mmsi_a"]), str(row["mmsi_b"])


def _candidate_episodes(
    rows: list[dict[str, object]],
    gap_min: float,
) -> list[list[dict[str, object]]]:
    if not rows:
        return []
    gap = dt.timedelta(minutes=gap_min)
    episodes: list[list[dict[str, object]]] = []
    current = [rows[0]]
    previous: dt.datetime = rows[0]["reference_time"]  # type: ignore[assignment]
    for row in rows[1:]:
        reference_time: dt.datetime = row["reference_time"]  # type: ignore[assignment]
        if reference_time - previous <= gap:
            current.append(row)
        else:
            episodes.append(current)
            current = [row]
        previous = reference_time
    episodes.append(current)
    return episodes


def _within_any_time(
    timestamp: dt.datetime,
    ordered_times: list[dt.datetime],
    tolerance: dt.timedelta,
) -> bool:
    index = bisect.bisect_left(ordered_times, timestamp)
    for candidate_index in (index - 1, index):
        if 0 <= candidate_index < len(ordered_times):
            if abs(ordered_times[candidate_index] - timestamp) <= tolerance:
                return True
    return False


def build_anchor_cohorts(
    opportunities: list[dict[str, object]],
    episode_gap_min: float = PAIR_EPISODE_GAP_MIN,
    candidate_exclusion_min: float = CONTROL_EXCLUSION_MIN,
    control_thinning_min: float = CONTROL_THINNING_MIN,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    """Construct frozen candidate and potential-control anchor cohorts."""
    if min(episode_gap_min, candidate_exclusion_min, control_thinning_min) < 0:
        raise ValueError("episode, exclusion, and thinning intervals must be non-negative")
    prepared = assign_matching_strata(opportunities)
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in prepared:
        grouped.setdefault(_pair_day(row), []).append(row)

    candidate_anchors: list[dict[str, object]] = []
    control_anchors: list[dict[str, object]] = []
    excluded_near_candidate = 0
    removed_by_thinning = 0
    candidate_records = 0
    noncandidate_records = 0
    exclusion = dt.timedelta(minutes=candidate_exclusion_min)
    thinning = dt.timedelta(minutes=control_thinning_min)

    for pair_day in sorted(grouped):
        rows = sorted(
            grouped[pair_day],
            key=lambda row: (row["reference_time"], str(row["opportunity_id"])),
        )
        candidates = [row for row in rows if bool(row["is_candidate"])]
        noncandidates = [row for row in rows if not bool(row["is_candidate"])]
        candidate_records += len(candidates)
        noncandidate_records += len(noncandidates)
        candidate_times: list[dt.datetime] = [row["reference_time"] for row in candidates]  # type: ignore[misc]

        for episode in _candidate_episodes(candidates, episode_gap_min):
            anchor = dict(episode[0])
            anchor.update(
                {
                    "anchor_type": "candidate",
                    "episode_start_time": episode[0]["reference_time"],
                    "episode_end_time": episode[-1]["reference_time"],
                    "episode_record_count": len(episode),
                }
            )
            candidate_anchors.append(anchor)

        last_selected_time: dt.datetime | None = None
        for row in noncandidates:
            reference_time: dt.datetime = row["reference_time"]  # type: ignore[assignment]
            if _within_any_time(reference_time, candidate_times, exclusion):
                excluded_near_candidate += 1
                continue
            if last_selected_time is not None and reference_time - last_selected_time < thinning:
                removed_by_thinning += 1
                continue
            anchor = dict(row)
            anchor.update(
                {
                    "anchor_type": "control",
                    "episode_start_time": reference_time,
                    "episode_end_time": reference_time,
                    "episode_record_count": 1,
                }
            )
            control_anchors.append(anchor)
            last_selected_time = reference_time

    anchor_sort = lambda row: (  # noqa: E731 - compact shared deterministic key
        row["reference_time"],
        str(row["mmsi_a"]),
        str(row["mmsi_b"]),
        str(row["opportunity_id"]),
    )
    candidate_anchors.sort(key=anchor_sort)
    control_anchors.sort(key=anchor_sort)
    for index, row in enumerate(candidate_anchors, start=1):
        row["anchor_id"] = f"candidate_anchor_{index:06d}"
    for index, row in enumerate(control_anchors, start=1):
        row["anchor_id"] = f"control_anchor_{index:06d}"

    audit: dict[str, object] = {
        "source_opportunity_records": len(prepared),
        "source_candidate_records": candidate_records,
        "source_noncandidate_records": noncandidate_records,
        "candidate_episode_anchors": len(candidate_anchors),
        "control_records_excluded_near_candidate": excluded_near_candidate,
        "control_records_removed_by_thinning": removed_by_thinning,
        "potential_control_anchors": len(control_anchors),
        "candidate_episode_gap_min": episode_gap_min,
        "control_candidate_exclusion_min": candidate_exclusion_min,
        "control_minimum_separation_min": control_thinning_min,
        "matching_dimensions": [
            "date",
            "4h_utc_block",
            "0.05_degree_spatial_block",
            "0.5_nm_current_distance_band",
            "within_day_local_opportunity_exposure_tertile",
        ],
        "local_exposure_definition": (
            "complete opportunity-record count in the date/analysis cell; within-day "
            "tied-midrank tertile; missing cell ids fall back to a 0.005-degree cell"
        ),
    }
    return candidate_anchors, control_anchors, audit


def _optional_difference(left: object, right: object) -> float:
    left_value = optional_float(left)
    right_value = optional_float(right)
    if left_value is None and right_value is None:
        return 0.0
    if left_value is None or right_value is None:
        return math.inf
    return abs(left_value - right_value)


def _nearest_control_key(
    candidate: Mapping[str, object],
    control: Mapping[str, object],
) -> tuple[object, ...]:
    candidate_time: dt.datetime = candidate["reference_time"]  # type: ignore[assignment]
    control_time: dt.datetime = control["reference_time"]  # type: ignore[assignment]
    # Current distance is the primary continuous nearest-neighbour criterion.
    # Clock time and remaining motion fields are deterministic tie breakers.
    return (
        abs(float(candidate["current_distance_nm"]) - float(control["current_distance_nm"])),
        abs((candidate_time - control_time).total_seconds()),
        _optional_difference(candidate.get("dcpa_nm"), control.get("dcpa_nm")),
        _optional_difference(candidate.get("tcpa_min"), control.get("tcpa_min")),
        _optional_difference(candidate.get("state_skew_s"), control.get("state_skew_s")),
        _optional_difference(candidate.get("relative_speed_kn"), control.get("relative_speed_kn")),
        _optional_difference(candidate.get("closing_speed_kn"), control.get("closing_speed_kn")),
        str(control["anchor_id"]),
    )


def deterministic_exact_match(
    candidate_anchors: list[dict[str, object]],
    control_anchors: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Greedily match in frozen candidate order, exactly and without replacement."""
    controls_by_stratum: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for control in control_anchors:
        stratum = tuple(control["matching_stratum"])  # type: ignore[arg-type]
        controls_by_stratum.setdefault(stratum, []).append(control)
    for controls in controls_by_stratum.values():
        controls.sort(key=lambda row: str(row["anchor_id"]))

    ordered_candidates = sorted(
        candidate_anchors,
        key=lambda row: (
            row["reference_time"],
            str(row["mmsi_a"]),
            str(row["mmsi_b"]),
            str(row["anchor_id"]),
        ),
    )
    matches: list[dict[str, object]] = []
    unmatched_by_stratum: Counter[str] = Counter()
    for candidate in ordered_candidates:
        stratum = tuple(candidate["matching_stratum"])  # type: ignore[arg-type]
        available = controls_by_stratum.get(stratum, [])
        if not available:
            unmatched_by_stratum[str(candidate["matching_stratum_id"])] += 1
            continue
        selected_index = min(
            range(len(available)),
            key=lambda index: _nearest_control_key(candidate, available[index]),
        )
        control = available.pop(selected_index)
        candidate_time: dt.datetime = candidate["reference_time"]  # type: ignore[assignment]
        control_time: dt.datetime = control["reference_time"]  # type: ignore[assignment]
        matches.append(
            {
                "match_id": f"geometric_control_match_{len(matches) + 1:06d}",
                "matching_stratum_id": candidate["matching_stratum_id"],
                "candidate": candidate,
                "control": control,
                "current_distance_abs_difference_nm": abs(
                    float(candidate["current_distance_nm"]) - float(control["current_distance_nm"])
                ),
                "reference_time_abs_difference_s": abs(
                    (candidate_time - control_time).total_seconds()
                ),
            }
        )

    audit: dict[str, object] = {
        "candidate_anchors": len(candidate_anchors),
        "potential_control_anchors": len(control_anchors),
        "matched_pairs": len(matches),
        "unmatched_candidate_anchors": len(candidate_anchors) - len(matches),
        "unused_control_anchors": len(control_anchors) - len(matches),
        "candidate_match_rate": (
            len(matches) / len(candidate_anchors) if candidate_anchors else None
        ),
        "unmatched_candidates_by_stratum": dict(sorted(unmatched_by_stratum.items())),
        "replacement": False,
        "relaxation": False,
        "nearest_rule": (
            "minimum current-distance difference, then reference-time difference, "
            "DCPA, TCPA, state skew, relative speed, closing speed, and anchor id"
        ),
    }
    return matches, audit


def _anchor_episode(anchor: Mapping[str, object]) -> tuple[dict[str, object], bool]:
    prediction_time: dt.datetime = anchor["reference_time"]  # type: ignore[assignment]
    tcpa = optional_float(anchor.get("tcpa_min"))
    predicted_time_defined = tcpa is not None
    # Undefined CPA time is represented by the window midpoint only so the
    # shared trajectory-coverage evaluator can run.  It is cleared from the
    # audit output below and is never treated as a predicted-time error target.
    predicted_closest_time = (
        prediction_time + dt.timedelta(minutes=tcpa)
        if tcpa is not None
        else prediction_time + dt.timedelta(minutes=7.5)
    )
    score = optional_float(anchor.get("encounter_risk_score"))
    if score is None:
        score = 1.0 if str(anchor["anchor_type"]) == "candidate" else 0.0
    episode = {
        **anchor,
        "episode_id": anchor["anchor_id"],
        "start_time": anchor["episode_start_time"],
        "end_time": anchor["episode_end_time"],
        "record_count": anchor["episode_record_count"],
        "prediction_time": prediction_time,
        "representative_time_bin": prediction_time,
        "predicted_closest_time": predicted_closest_time,
        "predicted_dcpa_nm": anchor.get("dcpa_nm"),
        "predicted_tcpa_min": tcpa,
        "prediction_record_score": score,
        "prediction_mid_lon": anchor.get("lon_mid"),
        "prediction_mid_lat": anchor.get("lat_mid"),
        "prediction_state_skew_s": anchor.get("state_skew_s"),
        "max_record_score": score,
        "min_record_dcpa_nm": anchor.get("dcpa_nm"),
        "min_current_distance_nm": anchor.get("current_distance_nm"),
        "predicted_closest_time_defined": int(predicted_time_defined),
    }
    return episode, predicted_time_defined


def evaluate_anchors(
    anchors: list[dict[str, object]],
    positions: dict[str, list[encounter_backtest.Position]],
    *,
    lookahead_min: float = 15.0,
    evaluation_step_s: int = 30,
    max_interpolation_gap_s: int = 180,
    support_distance_nm: float = 0.5,
    near_distance_nm: float = 1.0,
    min_common_fraction: float = 0.70,
    max_uncovered_gap_s: int = 180,
    predicted_time_tolerance_s: int = 60,
) -> list[dict[str, object]]:
    """Run candidate and control anchors through one strict-future evaluator."""
    episodes: list[dict[str, object]] = []
    undefined_anchor_ids: set[str] = set()
    for anchor in anchors:
        episode, predicted_time_defined = _anchor_episode(anchor)
        episodes.append(episode)
        if not predicted_time_defined:
            undefined_anchor_ids.add(str(anchor["anchor_id"]))

    evaluated = encounter_backtest.backtest_episodes(
        episodes,
        positions,
        lookahead_min=lookahead_min,
        evaluation_step_s=evaluation_step_s,
        max_interpolation_gap_s=max_interpolation_gap_s,
        support_distance_nm=support_distance_nm,
        near_distance_nm=near_distance_nm,
        min_common_fraction=min_common_fraction,
        max_uncovered_gap_s=max_uncovered_gap_s,
        predicted_time_tolerance_s=predicted_time_tolerance_s,
    )
    for row in evaluated:
        row["state_skew_s"] = row.get("prediction_state_skew_s")
        row["current_distance_nm"] = row.get("min_current_distance_nm")
        if str(row["anchor_id"]) in undefined_anchor_ids:
            row["predicted_closest_time"] = None
            row["predicted_time_window_covered"] = 0
            row["predicted_time_error_eligible"] = 0
            row["predicted_to_observed_abs_delta_min"] = None
            row["backtest_status"] = (
                "undefined_predicted_closest_time_future_geometry_observable"
                if int(row.get("observable_followup") or 0)
                else "undefined_predicted_closest_time_insufficient_future_followup"
            )
    return evaluated


def _safe_rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None and math.isfinite(value) else None


def _is_observable(row: Mapping[str, object]) -> bool:
    return bool(int(row.get("observable_followup") or 0))


def _has_outcome(row: Mapping[str, object], threshold_nm: float) -> bool:
    value = optional_float(row.get("actual_min_distance_nm"))
    return bool(_is_observable(row) and value is not None and value <= threshold_nm)


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _interval(values: list[float]) -> list[float | None]:
    finite = [value for value in values if math.isfinite(value)]
    return [_rounded(_percentile(finite, 0.025)), _rounded(_percentile(finite, 0.975))]


def _cluster_key(row: Mapping[str, object]) -> tuple[str, str, str]:
    return str(row["date"]), str(row["mmsi_a"]), str(row["mmsi_b"])


def _matched_bootstrap(
    paired_rows: list[tuple[Mapping[str, object], Mapping[str, object]]],
    threshold_nm: float,
    iterations: int,
    seed: int,
) -> dict[str, object]:
    by_cluster: dict[tuple[str, str, str], list[tuple[Mapping[str, object], Mapping[str, object]]]] = {}
    for candidate, control in paired_rows:
        by_cluster.setdefault(_cluster_key(candidate), []).append((candidate, control))
    clusters = list(sorted(by_cluster))
    candidate_rates: list[float] = []
    control_rates: list[float] = []
    risk_differences: list[float] = []
    lifts: list[float] = []
    rng = random.Random(seed)
    if clusters and iterations > 0:
        for _ in range(iterations):
            sampled = rng.choices(clusters, k=len(clusters))
            pair_count = candidate_hits = control_hits = 0
            for cluster in sampled:
                for candidate, control in by_cluster[cluster]:
                    pair_count += 1
                    candidate_hits += int(_has_outcome(candidate, threshold_nm))
                    control_hits += int(_has_outcome(control, threshold_nm))
            if not pair_count:
                continue
            candidate_rate = candidate_hits / pair_count
            control_rate = control_hits / pair_count
            candidate_rates.append(candidate_rate)
            control_rates.append(control_rate)
            risk_differences.append(candidate_rate - control_rate)
            if control_rate > 0:
                lifts.append(candidate_rate / control_rate)
    return {
        "method": "percentile vessel-pair/day cluster bootstrap",
        "cluster_unit": "candidate vessel-pair/day",
        "clusters": len(clusters),
        "iterations": iterations,
        "seed": seed,
        "candidate_rate": _interval(candidate_rates),
        "control_rate": _interval(control_rates),
        "risk_difference": _interval(risk_differences),
        "lift": _interval(lifts),
    }


def _capture_bootstrap(
    rows: list[Mapping[str, object]],
    threshold_nm: float,
    iterations: int,
    seed: int,
) -> dict[str, object]:
    cluster_counts: dict[tuple[str, str, str], tuple[int, int]] = {
        _cluster_key(row): (0, 0) for row in rows
    }
    for row in rows:
        if not _has_outcome(row, threshold_nm):
            continue
        key = _cluster_key(row)
        candidate_hit, all_hits = cluster_counts.get(key, (0, 0))
        cluster_counts[key] = (
            candidate_hit + int(str(row["anchor_type"]) == "candidate"),
            all_hits + 1,
        )
    clusters = list(sorted(cluster_counts))
    captures: list[float] = []
    rng = random.Random(seed)
    if clusters and iterations > 0:
        for _ in range(iterations):
            sampled = rng.choices(clusters, k=len(clusters))
            numerator = sum(cluster_counts[key][0] for key in sampled)
            denominator = sum(cluster_counts[key][1] for key in sampled)
            if denominator:
                captures.append(numerator / denominator)
    return {
        "method": "percentile vessel-pair/day cluster bootstrap",
        "cluster_unit": "vessel-pair/day",
        "clusters_with_outcome": len(clusters),
        "iterations": iterations,
        "seed": seed,
        "capture_rate": _interval(captures),
    }


CALIBRATION_SPECS: dict[str, list[tuple[float, float, str]]] = {
    "predicted_dcpa_nm": [
        (-math.inf, 0.25, "<0.25"),
        (0.25, 0.5, "0.25-<0.5"),
        (0.5, 1.0, "0.5-<1.0"),
        (1.0, math.inf, ">=1.0"),
    ],
    "predicted_tcpa_min": [
        (-math.inf, 0.0, "<0"),
        (0.0, 5.0, "0-<5"),
        (5.0, 10.0, "5-<10"),
        (10.0, 15.0, "10-<15"),
        (15.0, math.inf, ">=15"),
    ],
    "state_skew_s": [
        (-math.inf, 15.0, "<15"),
        (15.0, 30.0, "15-<30"),
        (30.0, 45.0, "30-<45"),
        (45.0, 60.0, "45-<60"),
        (60.0, math.inf, ">=60"),
    ],
    "current_distance_nm": [
        (-math.inf, 0.5, "<0.5"),
        (0.5, 1.0, "0.5-<1.0"),
        (1.0, 1.5, "1.0-<1.5"),
        (1.5, 2.0, "1.5-<2.0"),
        (2.0, math.inf, ">=2.0"),
    ],
}


def _calibration(rows: list[Mapping[str, object]]) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for variable, bins in CALIBRATION_SPECS.items():
        entries: list[dict[str, object]] = []
        for group in ("candidate", "control"):
            group_rows = [row for row in rows if str(row["anchor_type"]) == group]
            missing = [row for row in group_rows if optional_float(row.get(variable)) is None]
            for lower, upper, label in bins:
                bin_rows = [
                    row
                    for row in group_rows
                    if (value := optional_float(row.get(variable))) is not None
                    and lower <= value < upper
                ]
                if not bin_rows:
                    continue
                observable = [row for row in bin_rows if _is_observable(row)]
                half_hits = sum(_has_outcome(row, 0.5) for row in observable)
                one_hits = sum(_has_outcome(row, 1.0) for row in observable)
                entries.append(
                    {
                        "anchor_type": group,
                        "bin": label,
                        "anchor_count": len(bin_rows),
                        "observable_count": len(observable),
                        "within_0_5_nm_count": half_hits,
                        "within_0_5_nm_rate": _rounded(_safe_rate(half_hits, len(observable))),
                        "within_1_0_nm_count": one_hits,
                        "within_1_0_nm_rate": _rounded(_safe_rate(one_hits, len(observable))),
                    }
                )
            if missing:
                observable = [row for row in missing if _is_observable(row)]
                half_hits = sum(_has_outcome(row, 0.5) for row in observable)
                one_hits = sum(_has_outcome(row, 1.0) for row in observable)
                entries.append(
                    {
                        "anchor_type": group,
                        "bin": "missing_or_undefined",
                        "anchor_count": len(missing),
                        "observable_count": len(observable),
                        "within_0_5_nm_count": half_hits,
                        "within_0_5_nm_rate": _rounded(_safe_rate(half_hits, len(observable))),
                        "within_1_0_nm_count": one_hits,
                        "within_1_0_nm_rate": _rounded(_safe_rate(one_hits, len(observable))),
                    }
                )
        result[variable] = entries
    return result


def summarize_evaluation(
    candidate_anchors: list[dict[str, object]],
    control_anchors: list[dict[str, object]],
    matches: list[dict[str, object]],
    evaluated_rows: list[dict[str, object]],
    *,
    matching_audit: Mapping[str, object] | None = None,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    lookahead_min: float = 15.0,
    evaluation_step_s: int = 30,
    max_interpolation_gap_s: int = 180,
    min_common_fraction: float = 0.70,
    max_uncovered_gap_s: int = 180,
    predicted_time_tolerance_s: int = 60,
) -> dict[str, object]:
    if bootstrap_iterations < 0:
        raise ValueError("bootstrap_iterations must be non-negative")
    evaluated_by_id = {str(row["anchor_id"]): row for row in evaluated_rows}
    paired_rows: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    missing_evaluations = 0
    for match in matches:
        candidate = evaluated_by_id.get(str(match["candidate"]["anchor_id"]))  # type: ignore[index]
        control = evaluated_by_id.get(str(match["control"]["anchor_id"]))  # type: ignore[index]
        if candidate is None or control is None:
            missing_evaluations += 1
            continue
        if _is_observable(candidate) and _is_observable(control):
            paired_rows.append((candidate, control))

    report: dict[str, object] = {
        "method": "review_v9_deterministic_matched_noncandidate_geometric_controls",
        "interpretation": (
            "Future geometric support comparison for candidate screening; not prediction accuracy, "
            "an accident or near-miss label, or an enforcement/navigation conclusion."
        ),
        "cohorts": {
            "candidate_anchors": len(candidate_anchors),
            "potential_control_anchors": len(control_anchors),
            "evaluated_anchors": len(evaluated_rows),
            "observable_candidate_anchors": sum(
                _is_observable(row)
                for row in evaluated_rows
                if str(row["anchor_type"]) == "candidate"
            ),
            "observable_control_anchors": sum(
                _is_observable(row)
                for row in evaluated_rows
                if str(row["anchor_type"]) == "control"
            ),
            "undefined_predicted_closest_time_anchors": sum(
                not bool(int(row.get("predicted_closest_time_defined") or 0))
                for row in evaluated_rows
            ),
        },
        "matching": {
            **dict(matching_audit or {}),
            "matched_pairs_with_both_strict_future_observable": len(paired_rows),
            "matched_pairs_excluded_for_incomplete_followup": len(matches) - len(paired_rows),
            "matched_pairs_missing_evaluation": missing_evaluations,
        },
        "strict_future_evaluator": {
            "implementation": "encounter_backtest.backtest_episodes",
            "lookahead_min": lookahead_min,
            "primary_grid_step_s": evaluation_step_s,
            "maximum_interpolation_gap_s": max_interpolation_gap_s,
            "minimum_common_fraction": min_common_fraction,
            "minimum_common_samples": math.ceil(
                (lookahead_min * 60 / evaluation_step_s) * min_common_fraction
            ),
            "minimum_common_coverage_s": lookahead_min * 60 * min_common_fraction,
            "maximum_uncovered_gap_s": max_uncovered_gap_s,
            "predicted_time_tolerance_s": predicted_time_tolerance_s,
            "continuous_piecewise_linear_minimum": True,
            "grid_sensitivity_s": [10, 30, 60],
        },
        "full_cohort_capture": {},
        "matched_outcomes": {},
        "calibration": _calibration(evaluated_rows),
    }

    for threshold, label in ((0.5, "within_0_5_nm"), (1.0, "within_1_0_nm")):
        all_hits = [row for row in evaluated_rows if _has_outcome(row, threshold)]
        candidate_hits = [row for row in all_hits if str(row["anchor_type"]) == "candidate"]
        capture_rate = _safe_rate(len(candidate_hits), len(all_hits))
        report["full_cohort_capture"][label] = {  # type: ignore[index]
            "observable_geometric_outcome_anchors": len(all_hits),
            "candidate_geometric_outcome_anchors": len(candidate_hits),
            "capture_rate": _rounded(capture_rate),
            "cluster_bootstrap_95_ci": _capture_bootstrap(
                evaluated_rows,
                threshold,
                bootstrap_iterations,
                bootstrap_seed,
            ),
        }

        candidate_hit_count = sum(_has_outcome(candidate, threshold) for candidate, _ in paired_rows)
        control_hit_count = sum(_has_outcome(control, threshold) for _, control in paired_rows)
        pair_count = len(paired_rows)
        candidate_rate = _safe_rate(candidate_hit_count, pair_count)
        control_rate = _safe_rate(control_hit_count, pair_count)
        risk_difference = (
            candidate_rate - control_rate
            if candidate_rate is not None and control_rate is not None
            else None
        )
        lift = (
            candidate_rate / control_rate
            if candidate_rate is not None and control_rate is not None and control_rate > 0
            else None
        )
        report["matched_outcomes"][label] = {  # type: ignore[index]
            "matched_pairs_both_observable": pair_count,
            "candidate_count": candidate_hit_count,
            "control_count": control_hit_count,
            "candidate_rate": _rounded(candidate_rate),
            "control_rate": _rounded(control_rate),
            "risk_difference": _rounded(risk_difference),
            "lift": _rounded(lift),
            "control_rate_zero": bool(pair_count and control_hit_count == 0),
            "cluster_bootstrap_95_ci": _matched_bootstrap(
                paired_rows,
                threshold,
                bootstrap_iterations,
                bootstrap_seed,
            ),
        }
    return report


def _serialize_csv_value(value: object) -> object:
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    if value is None:
        return ""
    return value


def write_evaluated_csv(
    path: Path,
    rows: list[dict[str, object]],
    matches: list[dict[str, object]],
) -> None:
    links: dict[str, tuple[str, str]] = {}
    for match in matches:
        candidate_id = str(match["candidate"]["anchor_id"])  # type: ignore[index]
        control_id = str(match["control"]["anchor_id"])  # type: ignore[index]
        match_id = str(match["match_id"])
        links[candidate_id] = (match_id, control_id)
        links[control_id] = (match_id, candidate_id)
    prepared: list[dict[str, object]] = []
    for row in rows:
        rendered = {key: _serialize_csv_value(value) for key, value in row.items()}
        match_id, matched_anchor_id = links.get(str(row["anchor_id"]), ("", ""))
        rendered["match_id"] = match_id
        rendered["matched_anchor_id"] = matched_anchor_id
        prepared.append(rendered)
    if not prepared:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    preferred = [
        "anchor_id",
        "anchor_type",
        "match_id",
        "matched_anchor_id",
        "date",
        "prediction_time",
        "mmsi_a",
        "mmsi_b",
        "matching_stratum_id",
        "observable_followup",
        "actual_min_distance_nm",
        "supported_within_threshold",
        "near_supported_within_1nm",
    ]
    all_fields = {key for row in prepared for key in row}
    fieldnames = [field for field in preferred if field in all_fields]
    fieldnames.extend(sorted(all_fields - set(fieldnames)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(prepared)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate matched non-candidate controls with strict-future geometry."
    )
    parser.add_argument("--opportunity-csv", type=Path, required=True)
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--start", type=parse_date, required=True)
    parser.add_argument("--end", type=parse_date, required=True)
    parser.add_argument("--dataset-prefix", default="sf_bay_ais")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, help="Optional anchor-level audit CSV.")
    parser.add_argument("--bootstrap-iterations", type=int, default=DEFAULT_BOOTSTRAP_ITERATIONS)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    parser.add_argument("--episode-gap-min", type=float, default=PAIR_EPISODE_GAP_MIN)
    parser.add_argument("--control-exclusion-min", type=float, default=CONTROL_EXCLUSION_MIN)
    parser.add_argument("--control-thinning-min", type=float, default=CONTROL_THINNING_MIN)
    parser.add_argument("--lookahead-min", type=float, default=15.0)
    parser.add_argument("--evaluation-step-s", type=int, default=30)
    parser.add_argument("--max-interpolation-gap-s", type=int, default=180)
    parser.add_argument("--min-common-fraction", type=float, default=0.70)
    parser.add_argument("--max-uncovered-gap-s", type=int, default=180)
    parser.add_argument("--predicted-time-tolerance-s", type=int, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.end < args.start:
        raise ValueError("end date must be on or after start date")
    opportunities = load_opportunities(args.opportunity_csv)
    candidates, potential_controls, cohort_audit = build_anchor_cohorts(
        opportunities,
        episode_gap_min=args.episode_gap_min,
        candidate_exclusion_min=args.control_exclusion_min,
        control_thinning_min=args.control_thinning_min,
    )
    matches, matching_audit = deterministic_exact_match(candidates, potential_controls)
    anchors = candidates + potential_controls
    wanted_mmsi = {str(row["mmsi_a"]) for row in anchors} | {
        str(row["mmsi_b"]) for row in anchors
    }
    positions = encounter_backtest.load_positions(
        args.processed_dir,
        args.start,
        args.end,
        args.lookahead_min,
        wanted_mmsi,
        dataset_prefix=args.dataset_prefix,
    )
    evaluated = evaluate_anchors(
        anchors,
        positions,
        lookahead_min=args.lookahead_min,
        evaluation_step_s=args.evaluation_step_s,
        max_interpolation_gap_s=args.max_interpolation_gap_s,
        min_common_fraction=args.min_common_fraction,
        max_uncovered_gap_s=args.max_uncovered_gap_s,
        predicted_time_tolerance_s=args.predicted_time_tolerance_s,
    )
    report = summarize_evaluation(
        candidates,
        potential_controls,
        matches,
        evaluated,
        matching_audit=matching_audit,
        bootstrap_iterations=args.bootstrap_iterations,
        bootstrap_seed=args.bootstrap_seed,
        lookahead_min=args.lookahead_min,
        evaluation_step_s=args.evaluation_step_s,
        max_interpolation_gap_s=args.max_interpolation_gap_s,
        min_common_fraction=args.min_common_fraction,
        max_uncovered_gap_s=args.max_uncovered_gap_s,
        predicted_time_tolerance_s=args.predicted_time_tolerance_s,
    )
    report["input"] = {
        "opportunity_csv": str(args.opportunity_csv),
        "opportunity_csv_sha256": sha256_file(args.opportunity_csv),
        "processed_dir": str(args.processed_dir),
        "dataset_prefix": args.dataset_prefix,
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
    }
    report["cohort_selection"] = cohort_audit
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if args.output_csv:
        write_evaluated_csv(args.output_csv, evaluated, matches)
    print(
        json.dumps(
            {
                "candidate_anchors": len(candidates),
                "potential_control_anchors": len(potential_controls),
                "matched_pairs": len(matches),
                "matched_pairs_both_observable": report["matching"][
                    "matched_pairs_with_both_strict_future_observable"
                ],
                "output_json": str(args.output_json),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
