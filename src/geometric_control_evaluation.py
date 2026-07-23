#!/usr/bin/env python3
"""Matched non-candidate geometric controls for encounter screening.

This module implements the frozen review-v10 control protocol.  It compares
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
REFERENCE_TIME_CALIPER_MIN = 60.0
RELATIVE_SPEED_CALIPER_KN = 5.0
CLOSING_SPEED_CALIPER_KN = 2.5
STATE_SKEW_NORMALIZATION_S = 60.0


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


def _pair(row: Mapping[str, object]) -> tuple[str, str]:
    return str(row["mmsi_a"]), str(row["mmsi_b"])


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
    """Construct candidate episodes and pair-continuous potential controls.

    Candidate episodes intentionally retain the paper's vessel-pair/day unit.
    Candidate-window exclusion and control thinning instead operate on each
    vessel pair's continuous timestamp sequence, including across UTC midnight.
    """
    if min(episode_gap_min, candidate_exclusion_min, control_thinning_min) < 0:
        raise ValueError("episode, exclusion, and thinning intervals must be non-negative")
    prepared = assign_matching_strata(opportunities)
    grouped_by_pair_day: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    grouped_by_pair: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in prepared:
        grouped_by_pair_day.setdefault(_pair_day(row), []).append(row)
        grouped_by_pair.setdefault(_pair(row), []).append(row)

    candidate_anchors: list[dict[str, object]] = []
    control_anchors: list[dict[str, object]] = []
    excluded_near_candidate = 0
    removed_by_thinning = 0
    candidate_records = sum(bool(row["is_candidate"]) for row in prepared)
    noncandidate_records = len(prepared) - candidate_records
    exclusion = dt.timedelta(minutes=candidate_exclusion_min)
    thinning = dt.timedelta(minutes=control_thinning_min)

    # Candidate episode construction remains partitioned by pair/day.
    for pair_day in sorted(grouped_by_pair_day):
        rows = sorted(
            grouped_by_pair_day[pair_day],
            key=lambda row: (row["reference_time"], str(row["opportunity_id"])),
        )
        candidates = [row for row in rows if bool(row["is_candidate"])]
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

    # Exclusion and thinning must not reset at the calendar-day boundary.
    for pair_key in sorted(grouped_by_pair):
        rows = sorted(
            grouped_by_pair[pair_key],
            key=lambda row: (row["reference_time"], str(row["opportunity_id"])),
        )
        candidate_times: list[dt.datetime] = [
            row["reference_time"] for row in rows if bool(row["is_candidate"])  # type: ignore[misc]
        ]
        noncandidates = [row for row in rows if not bool(row["is_candidate"])]
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
        "candidate_episode_grouping": "vessel_pair_utc_calendar_day",
        "control_exclusion_and_thinning_grouping": (
            "vessel_pair_continuous_across_utc_dates"
        ),
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


PRIMARY_MATCHING_FIELDS = (
    "reference_time",
    "current_distance_nm",
    "relative_speed_kn",
    "closing_speed_kn",
    "state_skew_s",
    "local_opportunity_exposure",
)


def _missing_primary_matching_fields(row: Mapping[str, object]) -> list[str]:
    missing: list[str] = []
    if not isinstance(row.get("reference_time"), dt.datetime):
        missing.append("reference_time")
    for field in PRIMARY_MATCHING_FIELDS[1:]:
        if optional_float(row.get(field)) is None:
            missing.append(field)
    return missing


def _symmetric_relative_difference(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1.0)


def _matching_differences(
    candidate: Mapping[str, object],
    control: Mapping[str, object],
) -> dict[str, float]:
    candidate_time: dt.datetime = candidate["reference_time"]  # type: ignore[assignment]
    control_time: dt.datetime = control["reference_time"]  # type: ignore[assignment]
    return {
        "reference_time_abs_difference_s": abs(
            (candidate_time - control_time).total_seconds()
        ),
        "current_distance_abs_difference_nm": abs(
            required_float(candidate.get("current_distance_nm"), "candidate current distance")
            - required_float(control.get("current_distance_nm"), "control current distance")
        ),
        "relative_speed_abs_difference_kn": abs(
            required_float(candidate.get("relative_speed_kn"), "candidate relative speed")
            - required_float(control.get("relative_speed_kn"), "control relative speed")
        ),
        "closing_speed_abs_difference_kn": abs(
            required_float(candidate.get("closing_speed_kn"), "candidate closing speed")
            - required_float(control.get("closing_speed_kn"), "control closing speed")
        ),
        "state_skew_abs_difference_s": abs(
            required_float(candidate.get("state_skew_s"), "candidate state skew")
            - required_float(control.get("state_skew_s"), "control state skew")
        ),
        "local_exposure_abs_difference": abs(
            required_float(
                candidate.get("local_opportunity_exposure"),
                "candidate local exposure",
            )
            - required_float(
                control.get("local_opportunity_exposure"),
                "control local exposure",
            )
        ),
    }


def _normalized_matching_components(
    candidate: Mapping[str, object],
    control: Mapping[str, object],
    *,
    reference_time_caliper_min: float,
    relative_speed_caliper_kn: float,
    closing_speed_caliper_kn: float,
) -> dict[str, float]:
    differences = _matching_differences(candidate, control)
    candidate_exposure = required_float(
        candidate.get("local_opportunity_exposure"), "candidate local exposure"
    )
    control_exposure = required_float(
        control.get("local_opportunity_exposure"), "control local exposure"
    )
    return {
        "reference_time": (
            differences["reference_time_abs_difference_s"]
            / (reference_time_caliper_min * 60)
        ),
        "current_distance": (
            differences["current_distance_abs_difference_nm"] / DISTANCE_BAND_NM
        ),
        "relative_speed": (
            differences["relative_speed_abs_difference_kn"]
            / relative_speed_caliper_kn
        ),
        "closing_speed": (
            differences["closing_speed_abs_difference_kn"]
            / closing_speed_caliper_kn
        ),
        "state_skew": (
            differences["state_skew_abs_difference_s"] / STATE_SKEW_NORMALIZATION_S
        ),
        "local_exposure": _symmetric_relative_difference(
            candidate_exposure, control_exposure
        ),
    }


def _eligible_by_calipers(
    candidate: Mapping[str, object],
    control: Mapping[str, object],
    *,
    reference_time_caliper_min: float,
    relative_speed_caliper_kn: float,
    closing_speed_caliper_kn: float,
) -> bool:
    differences = _matching_differences(candidate, control)
    return (
        differences["reference_time_abs_difference_s"]
        <= reference_time_caliper_min * 60
        and differences["relative_speed_abs_difference_kn"]
        <= relative_speed_caliper_kn
        and differences["closing_speed_abs_difference_kn"]
        <= closing_speed_caliper_kn
    )


def _nearest_control_key(
    candidate: Mapping[str, object],
    control: Mapping[str, object],
    *,
    reference_time_caliper_min: float,
    relative_speed_caliper_kn: float,
    closing_speed_caliper_kn: float,
) -> tuple[object, ...]:
    components = _normalized_matching_components(
        candidate,
        control,
        reference_time_caliper_min=reference_time_caliper_min,
        relative_speed_caliper_kn=relative_speed_caliper_kn,
        closing_speed_caliper_kn=closing_speed_caliper_kn,
    )
    return (
        sum(components.values()),
        tuple(components[field] for field in (
            "reference_time",
            "current_distance",
            "relative_speed",
            "closing_speed",
            "state_skew",
            "local_exposure",
        )),
        str(control["anchor_id"]),
    )


def deterministic_exact_match(
    candidate_anchors: list[dict[str, object]],
    control_anchors: list[dict[str, object]],
    *,
    reference_time_caliper_min: float = REFERENCE_TIME_CALIPER_MIN,
    relative_speed_caliper_kn: float = RELATIVE_SPEED_CALIPER_KN,
    closing_speed_caliper_kn: float = CLOSING_SPEED_CALIPER_KN,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Apply the frozen review-v10 exact strata, calipers, and nearest objective."""
    if min(
        reference_time_caliper_min,
        relative_speed_caliper_kn,
        closing_speed_caliper_kn,
    ) <= 0:
        raise ValueError("primary matching calipers must be positive")

    candidate_missing: Counter[str] = Counter()
    control_missing: Counter[str] = Counter()
    eligible_candidates: list[dict[str, object]] = []
    eligible_controls: list[dict[str, object]] = []
    for candidate in candidate_anchors:
        missing = _missing_primary_matching_fields(candidate)
        if missing:
            candidate_missing.update(missing)
        else:
            eligible_candidates.append(candidate)
    for control in control_anchors:
        missing = _missing_primary_matching_fields(control)
        if missing:
            control_missing.update(missing)
        else:
            eligible_controls.append(control)

    controls_by_stratum: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for control in eligible_controls:
        stratum = tuple(control["matching_stratum"])  # type: ignore[arg-type]
        controls_by_stratum.setdefault(stratum, []).append(control)
    for controls in controls_by_stratum.values():
        controls.sort(key=lambda row: str(row["anchor_id"]))

    ordered_candidates = sorted(
        eligible_candidates,
        key=lambda row: (
            row["reference_time"],
            str(row["mmsi_a"]),
            str(row["mmsi_b"]),
            str(row["anchor_id"]),
        ),
    )
    matches: list[dict[str, object]] = []
    unmatched_by_stratum: Counter[str] = Counter()
    unmatched_no_caliper_control = 0
    for candidate in ordered_candidates:
        stratum = tuple(candidate["matching_stratum"])  # type: ignore[arg-type]
        available = controls_by_stratum.get(stratum, [])
        if not available:
            unmatched_by_stratum[str(candidate["matching_stratum_id"])] += 1
            continue
        caliper_eligible = [
            control
            for control in available
            if _eligible_by_calipers(
                candidate,
                control,
                reference_time_caliper_min=reference_time_caliper_min,
                relative_speed_caliper_kn=relative_speed_caliper_kn,
                closing_speed_caliper_kn=closing_speed_caliper_kn,
            )
        ]
        if not caliper_eligible:
            unmatched_no_caliper_control += 1
            continue
        control = min(
            caliper_eligible,
            key=lambda row: _nearest_control_key(
                candidate,
                row,
                reference_time_caliper_min=reference_time_caliper_min,
                relative_speed_caliper_kn=relative_speed_caliper_kn,
                closing_speed_caliper_kn=closing_speed_caliper_kn,
            ),
        )
        available.remove(control)
        differences = _matching_differences(candidate, control)
        components = _normalized_matching_components(
            candidate,
            control,
            reference_time_caliper_min=reference_time_caliper_min,
            relative_speed_caliper_kn=relative_speed_caliper_kn,
            closing_speed_caliper_kn=closing_speed_caliper_kn,
        )
        matches.append(
            {
                "match_id": f"geometric_control_match_{len(matches) + 1:06d}",
                "matching_stratum_id": candidate["matching_stratum_id"],
                "candidate": candidate,
                "control": control,
                **differences,
                "normalized_matching_components": components,
                "normalized_matching_distance": sum(components.values()),
            }
        )

    audit: dict[str, object] = {
        "candidate_anchors": len(candidate_anchors),
        "potential_control_anchors": len(control_anchors),
        "candidate_anchors_eligible_for_primary_matching": len(eligible_candidates),
        "potential_control_anchors_eligible_for_primary_matching": len(eligible_controls),
        "candidate_anchors_missing_primary_matching_values": (
            len(candidate_anchors) - len(eligible_candidates)
        ),
        "potential_control_anchors_missing_primary_matching_values": (
            len(control_anchors) - len(eligible_controls)
        ),
        "candidate_missing_primary_matching_values_by_field": dict(
            sorted(candidate_missing.items())
        ),
        "control_missing_primary_matching_values_by_field": dict(
            sorted(control_missing.items())
        ),
        "matched_pairs": len(matches),
        "unmatched_candidate_anchors": len(candidate_anchors) - len(matches),
        "unmatched_eligible_candidate_anchors": len(eligible_candidates) - len(matches),
        "unused_control_anchors": len(control_anchors) - len(matches),
        "unused_eligible_control_anchors": len(eligible_controls) - len(matches),
        "candidate_match_rate": (
            len(matches) / len(candidate_anchors) if candidate_anchors else None
        ),
        "eligible_candidate_match_rate": (
            len(matches) / len(eligible_candidates) if eligible_candidates else None
        ),
        "unmatched_candidates_by_stratum": dict(sorted(unmatched_by_stratum.items())),
        "unmatched_eligible_candidates_without_caliper_eligible_control": (
            unmatched_no_caliper_control
        ),
        "replacement": False,
        "relaxation": False,
        "reference_time_caliper_min": reference_time_caliper_min,
        "relative_speed_caliper_kn": relative_speed_caliper_kn,
        "closing_speed_caliper_kn": closing_speed_caliper_kn,
        "normalized_nearest_dimensions": [
            "reference_time",
            "current_distance",
            "relative_speed",
            "closing_speed",
            "state_skew",
            "local_exposure",
        ],
        "normalized_nearest_objective": (
            "minimum sum of six normalized absolute differences, then the six-term "
            "component tuple and stable control anchor id"
        ),
        "normalization": {
            "reference_time": f"absolute seconds / {reference_time_caliper_min * 60:g}",
            "current_distance": f"absolute nautical miles / {DISTANCE_BAND_NM:g}",
            "relative_speed": f"absolute knots / {relative_speed_caliper_kn:g}",
            "closing_speed": f"absolute knots / {closing_speed_caliper_kn:g}",
            "state_skew": f"absolute seconds / {STATE_SKEW_NORMALIZATION_S:g}",
            "local_exposure": (
                "absolute count difference / max(abs(candidate), abs(control), 1)"
            ),
        },
        "missing_value_rule": (
            "all six normalized-objective values must be finite on both sides"
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
    mmsi_a, mmsi_b = sorted((str(row["mmsi_a"]), str(row["mmsi_b"])))
    return str(row["date"]), mmsi_a, mmsi_b


def _dependency_components(
    paired_rows: list[tuple[Mapping[str, object], Mapping[str, object]]],
) -> tuple[
    list[list[tuple[Mapping[str, object], Mapping[str, object]]]],
    int,
    int,
    int,
    int,
]:
    """Return components of the physical vessel-pair/day match graph.

    A vessel-pair/day is one dependency node even when different anchors from
    that physical pair appear in both the candidate and control roles.
    """
    parent: dict[tuple[str, str, str], tuple[str, str, str]] = {}

    def find(node: tuple[str, str, str]) -> tuple[str, str, str]:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(
        left: tuple[str, str, str],
        right: tuple[str, str, str],
    ) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    edge_nodes: list[
        tuple[
            tuple[str, str, str],
            tuple[str, str, str],
            tuple[Mapping[str, object], Mapping[str, object]],
        ]
    ] = []
    candidate_nodes: set[tuple[str, str, str]] = set()
    control_nodes: set[tuple[str, str, str]] = set()
    for pair in paired_rows:
        candidate, control = pair
        candidate_node = _cluster_key(candidate)
        control_node = _cluster_key(control)
        candidate_nodes.add(candidate_node)
        control_nodes.add(control_node)
        union(candidate_node, control_node)
        edge_nodes.append((candidate_node, control_node, pair))

    by_component: dict[
        tuple[str, str, str],
        list[tuple[Mapping[str, object], Mapping[str, object]]],
    ] = {}
    for candidate_node, _, pair in edge_nodes:
        by_component.setdefault(find(candidate_node), []).append(pair)
    components = [
        by_component[key]
        for key in sorted(by_component)
    ]
    physical_nodes = candidate_nodes | control_nodes
    cross_role_nodes = candidate_nodes & control_nodes
    return (
        components,
        len(candidate_nodes),
        len(control_nodes),
        len(physical_nodes),
        len(cross_role_nodes),
    )


def _matched_bootstrap(
    paired_rows: list[tuple[Mapping[str, object], Mapping[str, object]]],
    threshold_nm: float,
    iterations: int,
    seed: int,
) -> dict[str, object]:
    (
        components,
        candidate_node_count,
        control_node_count,
        physical_node_count,
        cross_role_node_count,
    ) = _dependency_components(paired_rows)
    candidate_rates: list[float] = []
    control_rates: list[float] = []
    risk_differences: list[float] = []
    lifts: list[float] = []
    rng = random.Random(seed)
    if components and iterations > 0:
        for _ in range(iterations):
            sampled = rng.choices(components, k=len(components))
            pair_count = candidate_hits = control_hits = 0
            for component in sampled:
                for candidate, control in component:
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
        "method": (
            "percentile physical-vessel-pair/day dependency-component cluster bootstrap"
        ),
        "dependency_unit": (
            "connected_component_of_physical_vessel_pair_day_match_graph"
        ),
        "dependency_components": len(components),
        "candidate_vessel_pair_day_nodes": candidate_node_count,
        "control_vessel_pair_day_nodes": control_node_count,
        "physical_vessel_pair_day_nodes": physical_node_count,
        "cross_role_reused_vessel_pair_day_nodes": cross_role_node_count,
        "matched_set_edges": len(paired_rows),
        "iterations": iterations,
        "seed": seed,
        "candidate_rate": _interval(candidate_rates),
        "control_rate": _interval(control_rates),
        "risk_difference": _interval(risk_differences),
        "lift": _interval(lifts),
    }


def _selected_anchor_share_bootstrap(
    rows: list[Mapping[str, object]],
    threshold_nm: float,
    iterations: int,
    seed: int,
) -> dict[str, object]:
    cluster_counts: dict[tuple[str, str, str], tuple[int, int]] = {}
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
    candidate_shares: list[float] = []
    rng = random.Random(seed)
    if clusters and iterations > 0:
        for _ in range(iterations):
            sampled = rng.choices(clusters, k=len(clusters))
            numerator = sum(cluster_counts[key][0] for key in sampled)
            denominator = sum(cluster_counts[key][1] for key in sampled)
            if denominator:
                candidate_shares.append(numerator / denominator)
    return {
        "method": "percentile vessel-pair/day cluster bootstrap",
        "cluster_unit": "vessel-pair/day",
        "outcome_vessel_pair_day_clusters": len(clusters),
        "iterations": iterations,
        "seed": seed,
        "candidate_share_of_selected_anchor_outcomes": _interval(candidate_shares),
    }


BALANCE_FIELDS = (
    "current_distance_nm",
    "time_of_day_s",
    "state_skew_s",
    "relative_speed_kn",
    "closing_speed_kn",
    "local_opportunity_exposure",
)


def _balance_value(row: Mapping[str, object], field: str) -> float | None:
    if field == "time_of_day_s":
        timestamp = row.get("reference_time") or row.get("prediction_time")
        if not isinstance(timestamp, dt.datetime):
            try:
                timestamp = parse_datetime(timestamp)
            except ValueError:
                return None
        return (
            timestamp.hour * 3600
            + timestamp.minute * 60
            + timestamp.second
            + timestamp.microsecond / 1_000_000
        )
    return optional_float(row.get(field))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sample_variance(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _standardized_mean_difference(
    candidate_values: list[float],
    control_values: list[float],
) -> float | None:
    candidate_mean = _mean(candidate_values)
    control_mean = _mean(control_values)
    if candidate_mean is None or control_mean is None:
        return None
    candidate_variance = _sample_variance(candidate_values)
    control_variance = _sample_variance(control_values)
    available_variances = [
        value
        for value in (candidate_variance, control_variance)
        if value is not None
    ]
    if not available_variances:
        return 0.0 if candidate_mean == control_mean else None
    pooled_sd = math.sqrt(sum(available_variances) / len(available_variances))
    if pooled_sd == 0:
        return 0.0 if candidate_mean == control_mean else None
    return (candidate_mean - control_mean) / pooled_sd


def _balance_summary(
    candidate_rows: list[Mapping[str, object]],
    control_rows: list[Mapping[str, object]],
) -> dict[str, object]:
    variables: dict[str, object] = {}
    for field in BALANCE_FIELDS:
        candidate_values = [
            value
            for row in candidate_rows
            if (value := _balance_value(row, field)) is not None
        ]
        control_values = [
            value
            for row in control_rows
            if (value := _balance_value(row, field)) is not None
        ]
        smd = _standardized_mean_difference(candidate_values, control_values)
        variables[field] = {
            "complete_candidate_count": len(candidate_values),
            "complete_control_count": len(control_values),
            "missing_candidate_count": len(candidate_rows) - len(candidate_values),
            "missing_control_count": len(control_rows) - len(control_values),
            "candidate_mean": _rounded(_mean(candidate_values)),
            "control_mean": _rounded(_mean(control_values)),
            "candidate_median": _rounded(_percentile(candidate_values, 0.5)),
            "control_median": _rounded(_percentile(control_values, 0.5)),
            "candidate_p90": _rounded(_percentile(candidate_values, 0.9)),
            "control_p90": _rounded(_percentile(control_values, 0.9)),
            "standardized_mean_difference": _rounded(smd),
            "absolute_standardized_mean_difference": (
                _rounded(abs(smd)) if smd is not None else None
            ),
        }
    return {
        "candidate_count": len(candidate_rows),
        "control_count": len(control_rows),
        "pair_count": (
            len(candidate_rows)
            if len(candidate_rows) == len(control_rows)
            else None
        ),
        "variables": variables,
    }


def _absolute_match_difference_summary(
    matches: list[Mapping[str, object]],
) -> dict[str, dict[str, float | int | None]]:
    fields = (
        "reference_time_abs_difference_s",
        "current_distance_abs_difference_nm",
        "relative_speed_abs_difference_kn",
        "closing_speed_abs_difference_kn",
        "state_skew_abs_difference_s",
        "local_exposure_abs_difference",
        "normalized_matching_distance",
    )
    result: dict[str, dict[str, float | int | None]] = {}
    for field in fields:
        values = [
            value
            for match in matches
            if (value := optional_float(match.get(field))) is not None
        ]
        result[field] = {
            "count": len(values),
            "median": _rounded(_percentile(values, 0.5)),
            "p90": _rounded(_percentile(values, 0.9)),
            "maximum": _rounded(max(values)) if values else None,
        }
    return result


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


def _matched_outcome_entry(
    paired_rows: list[tuple[Mapping[str, object], Mapping[str, object]]],
    threshold_nm: float,
    bootstrap_iterations: int,
    bootstrap_seed: int,
) -> dict[str, object]:
    candidate_hit_count = sum(
        _has_outcome(candidate, threshold_nm) for candidate, _ in paired_rows
    )
    control_hit_count = sum(
        _has_outcome(control, threshold_nm) for _, control in paired_rows
    )
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
    return {
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
            threshold_nm,
            bootstrap_iterations,
            bootstrap_seed,
        ),
    }


def summarize_evaluation(
    candidate_anchors: list[dict[str, object]],
    control_anchors: list[dict[str, object]],
    matches: list[dict[str, object]],
    evaluated_rows: list[dict[str, object]],
    *,
    matching_audit: Mapping[str, object] | None = None,
    cohort_audit: Mapping[str, object] | None = None,
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
    complete_evaluation_pairs = 0
    matched_candidate_observable = 0
    matched_control_observable = 0
    candidate_only_observable = 0
    control_only_observable = 0
    neither_observable = 0
    for match in matches:
        candidate = evaluated_by_id.get(str(match["candidate"]["anchor_id"]))  # type: ignore[index]
        control = evaluated_by_id.get(str(match["control"]["anchor_id"]))  # type: ignore[index]
        if candidate is None or control is None:
            missing_evaluations += 1
            continue
        complete_evaluation_pairs += 1
        candidate_observable = _is_observable(candidate)
        control_observable = _is_observable(control)
        matched_candidate_observable += int(candidate_observable)
        matched_control_observable += int(control_observable)
        if candidate_observable and control_observable:
            paired_rows.append((candidate, control))
        elif candidate_observable:
            candidate_only_observable += 1
        elif control_observable:
            control_only_observable += 1
        else:
            neither_observable += 1

    candidate_observable_all = sum(
        _is_observable(row)
        for row in evaluated_rows
        if str(row["anchor_type"]) == "candidate"
    )
    control_observable_all = sum(
        _is_observable(row)
        for row in evaluated_rows
        if str(row["anchor_type"]) == "control"
    )
    matching_details = dict(matching_audit or {})
    cohort_details = dict(cohort_audit or {})
    matched_candidate_rows = [
        match["candidate"]  # type: ignore[index]
        for match in matches
    ]
    matched_control_rows = [
        match["control"]  # type: ignore[index]
        for match in matches
    ]

    report: dict[str, object] = {
        "method": "review_v10_calipered_matched_noncandidate_geometric_controls",
        "interpretation": (
            "Future geometric support comparison for candidate screening; not prediction accuracy, "
            "an accident or near-miss label, or an enforcement/navigation conclusion."
        ),
        "cohorts": {
            "candidate_anchors": len(candidate_anchors),
            "potential_control_anchors": len(control_anchors),
            "evaluated_anchors": len(evaluated_rows),
            "observable_candidate_anchors": candidate_observable_all,
            "observable_control_anchors": control_observable_all,
            "candidate_anchor_observable_rate": _rounded(
                _safe_rate(candidate_observable_all, len(candidate_anchors))
            ),
            "control_anchor_observable_rate": _rounded(
                _safe_rate(control_observable_all, len(control_anchors))
            ),
            "undefined_predicted_closest_time_anchors": sum(
                not bool(int(row.get("predicted_closest_time_defined") or 0))
                for row in evaluated_rows
            ),
        },
        "matching": {
            **matching_details,
            "matched_pairs_with_complete_evaluation": complete_evaluation_pairs,
            "matched_candidate_anchors_observable": matched_candidate_observable,
            "matched_control_anchors_observable": matched_control_observable,
            "matched_pairs_with_both_strict_future_observable": len(paired_rows),
            "matched_pairs_candidate_only_observable": candidate_only_observable,
            "matched_pairs_control_only_observable": control_only_observable,
            "matched_pairs_neither_observable": neither_observable,
            "matched_pairs_excluded_for_nonjoint_observability": (
                complete_evaluation_pairs - len(paired_rows)
            ),
            "matched_pairs_missing_evaluation": missing_evaluations,
            "matched_candidate_observable_rate": _rounded(
                _safe_rate(matched_candidate_observable, complete_evaluation_pairs)
            ),
            "matched_control_observable_rate": _rounded(
                _safe_rate(matched_control_observable, complete_evaluation_pairs)
            ),
            "matched_joint_observable_rate": _rounded(
                _safe_rate(len(paired_rows), complete_evaluation_pairs)
            ),
        },
        "cohort_flow": {
            "candidate": {
                "source_candidate_records": cohort_details.get("source_candidate_records"),
                "candidate_episode_anchors": len(candidate_anchors),
                "anchors_eligible_for_primary_matching": matching_details.get(
                    "candidate_anchors_eligible_for_primary_matching"
                ),
                "anchors_missing_primary_matching_values": matching_details.get(
                    "candidate_anchors_missing_primary_matching_values"
                ),
                "matched_anchors": len(matches),
                "unmatched_anchors": len(candidate_anchors) - len(matches),
                "observable_anchors": candidate_observable_all,
                "matched_observable_anchors": matched_candidate_observable,
            },
            "control": {
                "source_noncandidate_records": cohort_details.get(
                    "source_noncandidate_records"
                ),
                "records_excluded_near_candidate": cohort_details.get(
                    "control_records_excluded_near_candidate"
                ),
                "records_removed_by_thinning": cohort_details.get(
                    "control_records_removed_by_thinning"
                ),
                "potential_control_anchors": len(control_anchors),
                "anchors_eligible_for_primary_matching": matching_details.get(
                    "potential_control_anchors_eligible_for_primary_matching"
                ),
                "anchors_missing_primary_matching_values": matching_details.get(
                    "potential_control_anchors_missing_primary_matching_values"
                ),
                "matched_anchors": len(matches),
                "unused_anchors": len(control_anchors) - len(matches),
                "observable_anchors": control_observable_all,
                "matched_observable_anchors": matched_control_observable,
            },
            "matched_pairs": {
                "accepted": len(matches),
                "complete_evaluation": complete_evaluation_pairs,
                "both_observable": len(paired_rows),
                "candidate_only_observable": candidate_only_observable,
                "control_only_observable": control_only_observable,
                "neither_observable": neither_observable,
                "missing_evaluation": missing_evaluations,
            },
        },
        "matching_balance": {
            "definition": (
                "SMD=(candidate mean-control mean)/pooled sample SD; DCPA and TCPA "
                "are excluded because they define candidate assignment."
            ),
            "pre_match": _balance_summary(candidate_anchors, control_anchors),
            "post_match": {
                **_balance_summary(matched_candidate_rows, matched_control_rows),
                "absolute_pair_differences": _absolute_match_difference_summary(matches),
            },
            "post_match_joint_observable": _balance_summary(
                [candidate for candidate, _ in paired_rows],
                [control for _, control in paired_rows],
            ),
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
        "selected_anchor_hit_composition": {},
        "matched_outcomes": {},
        "matched_outcome_sensitivities": {},
        "calibration": _calibration(evaluated_rows),
        "schema_migration": {
            "removed_key": "full_cohort_capture",
            "replacement_key": "selected_anchor_hit_composition",
            "reason": (
                "The evaluated denominator is the selected candidate/control anchor "
                "sample after candidate-window exclusion and control thinning, not all "
                "pair opportunities and not a candidate-independent outcome cohort."
            ),
            "not_all_opportunity_capture_or_recall": True,
        },
        "uncertainty_boundary": (
            "Connected-component bootstrap uses one physical vessel-pair/day node "
            "regardless of candidate or control role, preserving repeated and "
            "cross-role use within the match graph. Dependence between separate "
            "components is assumed negligible."
        ),
    }

    for threshold, label in ((0.5, "within_0_5_nm"), (1.0, "within_1_0_nm")):
        all_hits = [row for row in evaluated_rows if _has_outcome(row, threshold)]
        candidate_hits = [row for row in all_hits if str(row["anchor_type"]) == "candidate"]
        candidate_share = _safe_rate(len(candidate_hits), len(all_hits))
        report["selected_anchor_hit_composition"][label] = {  # type: ignore[index]
            "selected_anchor_geometric_outcome_count": len(all_hits),
            "selected_candidate_anchor_geometric_outcome_count": len(candidate_hits),
            "candidate_share_of_selected_anchor_outcomes": _rounded(candidate_share),
            "denominator_definition": (
                "observable geometric-outcome anchors in the selected candidate/control "
                "anchor sample after candidate-window exclusion and control thinning"
            ),
            "is_all_opportunity_capture_or_recall": False,
            "cluster_bootstrap_95_ci": _selected_anchor_share_bootstrap(
                evaluated_rows,
                threshold,
                bootstrap_iterations,
                bootstrap_seed,
            ),
        }

        report["matched_outcomes"][label] = _matched_outcome_entry(  # type: ignore[index]
            paired_rows,
            threshold,
            bootstrap_iterations,
            bootstrap_seed,
        )

    for cutoff in (0.5, 1.0):
        sensitivity_pairs = [
            (candidate, control)
            for candidate, control in paired_rows
            if (
                (candidate_distance := optional_float(candidate.get("current_distance_nm")))
                is not None
                and (
                    control_distance := optional_float(control.get("current_distance_nm"))
                )
                is not None
                and candidate_distance > cutoff
                and control_distance > cutoff
            )
        ]
        sensitivity_key = (
            f"both_prediction_current_distance_above_{str(cutoff).replace('.', '_')}_nm"
        )
        report["matched_outcome_sensitivities"][sensitivity_key] = {  # type: ignore[index]
            "condition": (
                f"candidate and matched control current distance at t0 both exceed {cutoff:g} nm"
            ),
            "matched_pairs_both_observable": len(sensitivity_pairs),
            "within_0_5_nm": _matched_outcome_entry(
                sensitivity_pairs,
                0.5,
                bootstrap_iterations,
                bootstrap_seed,
            ),
            "within_1_0_nm": _matched_outcome_entry(
                sensitivity_pairs,
                1.0,
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
    parser.add_argument(
        "--reference-time-caliper-min",
        type=float,
        default=REFERENCE_TIME_CALIPER_MIN,
    )
    parser.add_argument(
        "--relative-speed-caliper-kn",
        type=float,
        default=RELATIVE_SPEED_CALIPER_KN,
    )
    parser.add_argument(
        "--closing-speed-caliper-kn",
        type=float,
        default=CLOSING_SPEED_CALIPER_KN,
    )
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
    matches, matching_audit = deterministic_exact_match(
        candidates,
        potential_controls,
        reference_time_caliper_min=args.reference_time_caliper_min,
        relative_speed_caliper_kn=args.relative_speed_caliper_kn,
        closing_speed_caliper_kn=args.closing_speed_caliper_kn,
    )
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
        cohort_audit=cohort_audit,
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
