#!/usr/bin/env python3
"""Build de-identified hotspot evidence cards for the web prototype."""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from encounter_backtest import (
    Position,
    continuous_common_min_distance,
    eligible_segments,
    load_positions,
    segment_position,
    xy_nm,
)


ENCOUNTER_CARD_SCHEMA_VERSION = "review-v10.encounter-evidence-card.v2"
ENCOUNTER_CARD_DISCLAIMER = (
    "De-identified historical AIS encounter-candidate evidence for human audit. "
    "Geometric support is not prediction accuracy, an incident or near-miss label, "
    "an enforcement finding, or navigation or collision-avoidance advice."
)
PUBLIC_CARD_SENSITIVE_KEYS = {
    "mmsi",
    "mmsi_a",
    "mmsi_b",
    "track_id",
    "track_id_a",
    "track_id_b",
    "episode_id",
    "date",
    "time_bin",
    "reference_time",
    "prediction_time",
    "window_end",
    "predicted_closest_time",
    "actual_min_time",
    "continuous_min_time",
    "timestamp",
    "timestamp_a",
    "timestamp_b",
    "source_timestamp_a",
    "source_timestamp_b",
    "longitude",
    "latitude",
    "lon",
    "lat",
    "lon_a",
    "lat_a",
    "lon_b",
    "lat_b",
    "sog_a",
    "sog_b",
    "bearing_a",
    "bearing_b",
    "ground_track_course_a",
    "ground_track_course_b",
}
EXACT_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b")
MMSI_LIKE_RE = re.compile(r"(?<!\d)\d{9}(?!\d)")


def parse_float(value: str | int | float | None) -> float:
    if value is None or value == "":
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def parse_int(value: str | int | float | None) -> int:
    return int(round(parse_float(value)))


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def rounded(value: object, digits: int = 6) -> object:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value


def cell_id_for_point(
    lon: float,
    lat: float,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> str | None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
        return None
    col = int((lon - min_lon) / cell_size_deg)
    row = int((lat - min_lat) / cell_size_deg)
    max_col = int((max_lon - min_lon) / cell_size_deg)
    max_row = int((max_lat - min_lat) / cell_size_deg)
    return f"r{min(row, max_row)}_c{min(col, max_col)}"


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def group_encounter_episodes_by_cell(
    encounter_csv: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    episode_gap_min: float,
) -> dict[str, dict[str, int]]:
    rows_by_pair_day: dict[tuple[str, str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    with encounter_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                time_bin = parse_time(row["time_bin"])
                lon_mid = (float(row["lon_a"]) + float(row["lon_b"])) / 2
                lat_mid = (float(row["lat_a"]) + float(row["lat_b"])) / 2
            except (KeyError, ValueError):
                continue
            cell_id = cell_id_for_point(lon_mid, lat_mid, bbox, cell_size_deg)
            if cell_id is None:
                continue
            a, b = sorted([row.get("mmsi_a", ""), row.get("mmsi_b", "")])
            if not a or not b:
                continue
            rows_by_pair_day[(row.get("date", time_bin.date().isoformat()), a, b)].append(
                {
                    "time_bin": time_bin,
                    "cell_id": cell_id,
                    "score": parse_float(row.get("encounter_risk_score")),
                }
            )

    counts: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"encounter_episode_count": 0})
    gap = dt.timedelta(minutes=episode_gap_min)
    episode_index = 0
    for key in sorted(rows_by_pair_day):
        rows = sorted(rows_by_pair_day[key], key=lambda item: item["time_bin"])
        current: list[dict[str, Any]] = []
        previous_time: dt.datetime | None = None
        for row in rows:
            if previous_time is None or row["time_bin"] - previous_time <= gap:
                current.append(row)
            else:
                episode_index += 1
                add_episode_cell_count(current, counts, episode_index)
                current = [row]
            previous_time = row["time_bin"]
        if current:
            episode_index += 1
            add_episode_cell_count(current, counts, episode_index)
    return dict(counts)


def add_episode_cell_count(
    records: list[dict[str, Any]],
    counts: dict[str, dict[str, int]],
    episode_index: int,
) -> None:
    representative = sorted(records, key=lambda item: float(item["score"]), reverse=True)[0]
    cell_id = str(representative["cell_id"])
    counts[cell_id]["encounter_episode_count"] += 1


def supported_episodes_by_cell(
    backtest_csv: Path,
    encounter_csv: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    episode_gap_min: float,
) -> dict[str, int]:
    if not backtest_csv.exists():
        return {}
    supported_ids: set[str] = set()
    with backtest_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if parse_int(row.get("supported_within_threshold")) == 1:
                supported_ids.add(str(row.get("episode_id", "")))

    rows_by_pair_day: dict[tuple[str, str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    with encounter_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                time_bin = parse_time(row["time_bin"])
                lon_mid = (float(row["lon_a"]) + float(row["lon_b"])) / 2
                lat_mid = (float(row["lat_a"]) + float(row["lat_b"])) / 2
            except (KeyError, ValueError):
                continue
            cell_id = cell_id_for_point(lon_mid, lat_mid, bbox, cell_size_deg)
            if cell_id is None:
                continue
            a, b = sorted([row.get("mmsi_a", ""), row.get("mmsi_b", "")])
            rows_by_pair_day[(row.get("date", time_bin.date().isoformat()), a, b)].append(
                {"time_bin": time_bin, "cell_id": cell_id, "score": parse_float(row.get("encounter_risk_score"))}
            )

    counts: dict[str, int] = collections.Counter()
    gap = dt.timedelta(minutes=episode_gap_min)
    episode_index = 0
    for key in sorted(rows_by_pair_day):
        rows = sorted(rows_by_pair_day[key], key=lambda item: item["time_bin"])
        current: list[dict[str, Any]] = []
        previous_time: dt.datetime | None = None
        for row in rows:
            if previous_time is None or row["time_bin"] - previous_time <= gap:
                current.append(row)
            else:
                episode_index += 1
                episode_id = f"enc_episode_{episode_index:05d}"
                if episode_id in supported_ids:
                    representative = sorted(current, key=lambda item: float(item["score"]), reverse=True)[0]
                    counts[str(representative["cell_id"])] += 1
                current = [row]
            previous_time = row["time_bin"]
        if current:
            episode_index += 1
            episode_id = f"enc_episode_{episode_index:05d}"
            if episode_id in supported_ids:
                representative = sorted(current, key=lambda item: float(item["score"]), reverse=True)[0]
                counts[str(representative["cell_id"])] += 1
    return dict(counts)


def related_cases_by_cell(case_json: Path) -> dict[str, list[str]]:
    if not case_json.exists():
        return {}
    data = load_json(case_json)
    mapping: dict[str, list[str]] = collections.defaultdict(list)
    for case in data.get("cases", []):
        case_id = case.get("case_id")
        for cell_id, _count in case.get("top_cells", []):
            if case_id:
                mapping[str(cell_id)].append(str(case_id))
    return dict(mapping)


def episode_outcomes_by_cell(
    backtest_csv: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"encounter_episode_count": 0, "supported_episode_count": 0}
    )
    if not backtest_csv.exists():
        return {}
    with backtest_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                lon = float(row["prediction_mid_lon"])
                lat = float(row["prediction_mid_lat"])
            except (KeyError, TypeError, ValueError):
                continue
            cell_id = cell_id_for_point(lon, lat, bbox, cell_size_deg)
            if cell_id is None:
                continue
            counts[cell_id]["encounter_episode_count"] += 1
            if parse_int(row.get("supported_within_threshold")) == 1:
                counts[cell_id]["supported_episode_count"] += 1
    return dict(counts)


def build_cards(
    typology_csv: Path,
    encounter_csv: Path,
    backtest_csv: Path,
    case_json: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    episode_gap_min: float,
) -> dict[str, Any]:
    rows = [row for row in load_csv(typology_csv) if parse_int(row.get("is_hotspot")) == 1]
    rows.sort(key=lambda row: parse_float(row.get("risk_score")), reverse=True)
    episode_outcomes = episode_outcomes_by_cell(backtest_csv, bbox, cell_size_deg)
    case_mapping = related_cases_by_cell(case_json)

    cards: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        cell_id = row["cell_id"]
        cards.append(
            {
                "card_id": f"hotspot_card_{rank:03d}",
                "rank": rank,
                "cell_id": cell_id,
                "screening_score": rounded(row.get("risk_score")),
                "hotspot_type": row.get("hotspot_type", ""),
                "dominant_evidence": row.get("dominant_evidence", ""),
                "stability_class": row.get("stability_class", ""),
                "review_focus": row.get("review_focus", ""),
                "dominant_anomaly_reason": row.get("dominant_reason", ""),
                "counts": {
                    "ais_points": parse_int(row.get("point_count")),
                    "moving_points": parse_int(row.get("moving_count")),
                    "anomaly_candidates": parse_int(row.get("anomaly_count")),
                    "corroborated_anomaly_candidates": parse_int(row.get("corroborated_anomaly_count")),
                    "low_support_only_candidates": parse_int(row.get("low_support_only_count")),
                    "encounter_records": parse_int(row.get("encounter_count")),
                    "pair_opportunities": parse_int(row.get("pair_opportunity_count")),
                    "encounter_episodes": episode_outcomes.get(cell_id, {}).get("encounter_episode_count", 0),
                    "backtest_supported_episodes": episode_outcomes.get(cell_id, {}).get(
                        "supported_episode_count", 0
                    ),
                },
                "scores": {
                    "mean_anomaly_screening_score": rounded(row.get("mean_anomaly_score")),
                    "mean_encounter_evidence_score": rounded(row.get("mean_encounter_score")),
                    "max_encounter_evidence_score": rounded(row.get("max_encounter_score")),
                    "anomaly_component_share": rounded(row.get("anomaly_component_share")),
                    "encounter_component_share": rounded(row.get("encounter_component_share")),
                },
                "stability": {
                    "density_topk_days": parse_int(row.get("density_topk_days")),
                    "fused_topk_days": parse_int(row.get("fused_topk_days")),
                },
                "related_case_ids": case_mapping.get(cell_id, [])[:5],
            }
        )

    return {
        "project": "Open AIS Port Screening Explorer",
        "publication_mode": "sanitized_research_demo",
        "disclaimer": (
            "Evidence cards are de-identified review aids. They are not alarms, incident labels, "
            "near-miss determinations, or enforcement findings."
        ),
        "card_count": len(cards),
        "summary": {
            "counts_by_hotspot_type": dict(collections.Counter(card["hotspot_type"] for card in cards)),
            "counts_by_dominant_evidence": dict(collections.Counter(card["dominant_evidence"] for card in cards)),
            "total_encounter_episodes_on_cards": sum(card["counts"]["encounter_episodes"] for card in cards),
            "total_backtest_supported_episodes_on_cards": sum(
                card["counts"]["backtest_supported_episodes"] for card in cards
            ),
        },
        "cards": cards,
    }


def optional_float(value: object, digits: int = 6) -> float | None:
    """Parse a finite public metric without turning missing values into zero."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def optional_int(value: object) -> int | None:
    number = optional_float(value)
    return int(round(number)) if number is not None else None


def parse_optional_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_time(value)
    except ValueError:
        return None


def canonical_pair(row: dict[str, str]) -> tuple[str, str] | None:
    a = str(row.get("mmsi_a") or "")
    b = str(row.get("mmsi_b") or "")
    if not a or not b or a == b:
        return None
    return tuple(sorted((a, b)))


def load_encounter_index(encounter_csv: Path) -> dict[tuple[str, str, dt.datetime], dict[str, str]]:
    """Index the synchronized first-pass states used by the backtest anchor."""
    index: dict[tuple[str, str, dt.datetime], dict[str, str]] = {}
    with encounter_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            pair = canonical_pair(row)
            reference_time = parse_optional_time(row.get("reference_time") or row.get("time_bin"))
            if pair is None or reference_time is None:
                continue
            index[(pair[0], pair[1], reference_time)] = row
    return index


def load_backtest_rows(backtest_csv: Path) -> list[dict[str, str]]:
    with backtest_csv.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def encounter_row_for_backtest(
    backtest_row: dict[str, str],
    encounter_index: dict[tuple[str, str, dt.datetime], dict[str, str]],
) -> dict[str, str] | None:
    pair = canonical_pair(backtest_row)
    prediction_time = parse_optional_time(backtest_row.get("prediction_time"))
    if pair is None or prediction_time is None:
        return None
    return encounter_index.get((pair[0], pair[1], prediction_time))


def normalized_encounter_states(
    row: dict[str, str] | None,
    pair: tuple[str, str],
) -> tuple[dict[str, float], dict[str, float]] | None:
    """Return synchronized states in the canonical private pair order."""
    if row is None:
        return None
    raw_a = str(row.get("mmsi_a") or "")
    raw_b = str(row.get("mmsi_b") or "")
    values_a = (
        optional_float(row.get("lon_a")),
        optional_float(row.get("lat_a")),
        optional_float(row.get("sog_a")),
        optional_float(row.get("ground_track_course_a") or row.get("bearing_a")),
    )
    values_b = (
        optional_float(row.get("lon_b")),
        optional_float(row.get("lat_b")),
        optional_float(row.get("sog_b")),
        optional_float(row.get("ground_track_course_b") or row.get("bearing_b")),
    )
    if any(value is None for value in (*values_a, *values_b)):
        return None
    state_a = {
        "lon": float(values_a[0]),
        "lat": float(values_a[1]),
        "sog": float(values_a[2]),
        "course": float(values_a[3]),
    }
    state_b = {
        "lon": float(values_b[0]),
        "lat": float(values_b[1]),
        "sog": float(values_b[2]),
        "course": float(values_b[3]),
    }
    if (raw_a, raw_b) == pair:
        return state_a, state_b
    if (raw_b, raw_a) == pair:
        return state_b, state_a
    return None


def relative_point(
    lon: float,
    lat: float,
    origin_lon: float,
    origin_lat: float,
    digits: int = 2,
) -> dict[str, float]:
    x_nm, y_nm = xy_nm(lon, lat, origin_lon, origin_lat)
    return {"x_nm": round(x_nm, digits), "y_nm": round(y_nm, digits)}


def predicted_relative_geometry(
    states: tuple[dict[str, float], dict[str, float]] | None,
    tcpa_min: float | None,
) -> tuple[dict[str, Any] | None, tuple[float, float] | None]:
    if states is None:
        return None, None
    state_a, state_b = states
    origin_lon = (state_a["lon"] + state_b["lon"]) / 2
    origin_lat = (state_a["lat"] + state_b["lat"]) / 2
    t0_a = relative_point(state_a["lon"], state_a["lat"], origin_lon, origin_lat)
    t0_b = relative_point(state_b["lon"], state_b["lat"], origin_lon, origin_lat)
    if tcpa_min is None:
        return {
            "coordinate_frame": "local_tangent_plane_centered_at_synchronized_t0_midpoint",
            "coordinate_unit": "nautical_mile",
            "coordinate_precision_nm": 0.01,
            "vessels": [
                {"label": "A", **t0_a},
                {"label": "B", **t0_b},
            ],
        }, (origin_lon, origin_lat)

    elapsed_h = max(0.0, tcpa_min) / 60

    def advance(point: dict[str, float], state: dict[str, float]) -> dict[str, float]:
        course_rad = math.radians(state["course"])
        return {
            "x_nm": round(point["x_nm"] + state["sog"] * math.sin(course_rad) * elapsed_h, 2),
            "y_nm": round(point["y_nm"] + state["sog"] * math.cos(course_rad) * elapsed_h, 2),
        }

    predicted_a = advance(t0_a, state_a)
    predicted_b = advance(t0_b, state_b)
    return {
        "coordinate_frame": "local_tangent_plane_centered_at_synchronized_t0_midpoint",
        "coordinate_unit": "nautical_mile",
        "coordinate_precision_nm": 0.01,
        "vessels": [
            {"label": "A", **t0_a},
            {"label": "B", **t0_b},
        ],
        "predicted_closest_positions": [
            {"label": "A", **predicted_a},
            {"label": "B", **predicted_b},
        ],
        "predicted_closest_midpoint": {
            "x_nm": round((predicted_a["x_nm"] + predicted_b["x_nm"]) / 2, 2),
            "y_nm": round((predicted_a["y_nm"] + predicted_b["y_nm"]) / 2, 2),
        },
    }, (origin_lon, origin_lat)


def position_at(segments: list[tuple[Position, Position]], timestamp: dt.datetime) -> tuple[float, float] | None:
    for segment in segments:
        if segment[0].timestamp <= timestamp <= segment[1].timestamp:
            return segment_position(segment, timestamp)
    return None


def sample_interval_times(
    start: dt.datetime,
    end: dt.datetime,
    step_s: int,
    extra_times: list[dt.datetime],
    max_points: int,
) -> list[dt.datetime]:
    if end < start:
        return []
    times = [start]
    cursor = start + dt.timedelta(seconds=step_s)
    while cursor < end:
        times.append(cursor)
        cursor += dt.timedelta(seconds=step_s)
    times.append(end)
    times.extend(timestamp for timestamp in extra_times if start <= timestamp <= end)
    times = sorted(set(times))
    if len(times) <= max_points:
        return times
    mandatory = {start, end, *(timestamp for timestamp in extra_times if start <= timestamp <= end)}
    remaining = [timestamp for timestamp in times if timestamp not in mandatory]
    capacity = max(0, max_points - len(mandatory))
    if capacity == 0:
        return sorted(mandatory)[:max_points]
    if len(remaining) > capacity:
        if capacity == 1:
            remaining = [remaining[len(remaining) // 2]]
        else:
            last = len(remaining) - 1
            indexes = sorted({round(index * last / (capacity - 1)) for index in range(capacity)})
            remaining = [remaining[index] for index in indexes]
    return sorted([*mandatory, *remaining])


def relative_future_geometry(
    rows_a: list[Position],
    rows_b: list[Position],
    prediction_time: dt.datetime,
    window_end: dt.datetime,
    origin: tuple[float, float] | None,
    predicted_time: dt.datetime | None,
    actual_time: dt.datetime | None,
    max_interpolation_gap_s: int,
    geometry_step_s: int,
    max_points_per_segment: int,
) -> dict[str, Any]:
    """Build a public relative drawing from the same strict-future segment rules."""
    segments_a = eligible_segments(rows_a, prediction_time, window_end, max_interpolation_gap_s)
    segments_b = eligible_segments(rows_b, prediction_time, window_end, max_interpolation_gap_s)
    _distance, _time, common_intervals = continuous_common_min_distance(segments_a, segments_b)
    if not common_intervals:
        return {
            "status": "unavailable",
            "reason": "no_same_track_common_future_segment",
            "time_unit": "seconds_from_t0",
            "coordinate_unit": "nautical_mile",
            "common_segments": [],
        }

    if origin is None:
        first_time = common_intervals[0][0]
        first_a = position_at(segments_a, first_time)
        first_b = position_at(segments_b, first_time)
        if first_a is None or first_b is None:
            return {
                "status": "unavailable",
                "reason": "relative_origin_unavailable",
                "time_unit": "seconds_from_t0",
                "coordinate_unit": "nautical_mile",
                "common_segments": [],
            }
        origin = ((first_a[0] + first_b[0]) / 2, (first_a[1] + first_b[1]) / 2)

    public_segments: list[dict[str, Any]] = []
    for interval_start, interval_end in common_intervals:
        times = sample_interval_times(
            interval_start,
            interval_end,
            geometry_step_s,
            [timestamp for timestamp in (predicted_time, actual_time) if timestamp is not None],
            max_points_per_segment,
        )
        vessel_points: dict[str, list[dict[str, float | int]]] = {"A": [], "B": []}
        for timestamp in times:
            point_a = position_at(segments_a, timestamp)
            point_b = position_at(segments_b, timestamp)
            if point_a is None or point_b is None:
                continue
            t_s = int(round((timestamp - prediction_time).total_seconds()))
            vessel_points["A"].append({"t_s": t_s, **relative_point(*point_a, *origin)})
            vessel_points["B"].append({"t_s": t_s, **relative_point(*point_b, *origin)})
        if vessel_points["A"] and vessel_points["B"]:
            public_segments.append(
                {
                    "start_t_s": int(round((interval_start - prediction_time).total_seconds())),
                    "end_t_s": int(round((interval_end - prediction_time).total_seconds())),
                    "vessels": [
                        {"label": "A", "points": vessel_points["A"]},
                        {"label": "B", "points": vessel_points["B"]},
                    ],
                }
            )
    observed_positions: list[dict[str, Any]] = []
    if actual_time is not None:
        observed_a = position_at(segments_a, actual_time)
        observed_b = position_at(segments_b, actual_time)
        if observed_a is not None and observed_b is not None:
            observed_positions = [
                {"label": "A", **relative_point(*observed_a, *origin)},
                {"label": "B", **relative_point(*observed_b, *origin)},
            ]
    return {
        "status": "available" if public_segments else "unavailable",
        "reason": None if public_segments else "common_segment_sampling_failed",
        "source": "same-track strict-future piecewise-linear reconstruction",
        "time_unit": "seconds_from_t0",
        "coordinate_frame": "local_tangent_plane_centered_at_synchronized_t0_midpoint",
        "coordinate_unit": "nautical_mile",
        "coordinate_precision_nm": 0.01,
        "sampling_step_s": geometry_step_s,
        "observed_minimum_positions": observed_positions,
        "common_segments": public_segments,
    }


def public_support_status(row: dict[str, str]) -> str:
    observable = parse_int(row.get("observable_followup")) == 1
    if not observable:
        return "insufficient_common_future_coverage"
    if parse_int(row.get("supported_within_threshold")) == 1:
        return "geometrically_supported_within_0_5_nm"
    if parse_int(row.get("near_supported_within_1nm")) == 1:
        return "geometrically_supported_within_1_0_nm_only"
    return "observable_without_1_0_nm_geometric_support"


def select_backtest_rows(rows: list[dict[str, str]], max_cards: int) -> list[dict[str, str]]:
    """Choose a compact deterministic audit sample without hard-coding results."""
    if max_cards <= 0:
        return []
    ranked = sorted(
        rows,
        key=lambda row: (
            -parse_int(row.get("observable_followup")),
            -parse_int(row.get("predicted_time_window_covered")),
            -parse_int(row.get("supported_within_threshold")),
            -int(parse_optional_time(row.get("continuous_min_time") or row.get("actual_min_time")) is not None),
            -parse_float(row.get("common_coverage_fraction")),
            -parse_float(row.get("prediction_record_score")),
            str(row.get("episode_id") or ""),
        ),
    )
    return ranked[:max_cards]


def make_encounter_card(
    rank: int,
    dataset_id: str,
    backtest_row: dict[str, str],
    encounter_row: dict[str, str] | None,
    positions: dict[str, list[Position]],
    lookahead_min: float,
    max_interpolation_gap_s: int,
    geometry_step_s: int,
    max_geometry_points: int,
) -> dict[str, Any]:
    pair = canonical_pair(backtest_row)
    prediction_time = parse_optional_time(backtest_row.get("prediction_time"))
    if pair is None or prediction_time is None:
        raise ValueError("backtest card row is missing a private pair or prediction time")
    window_end = parse_optional_time(backtest_row.get("window_end")) or (
        prediction_time + dt.timedelta(minutes=lookahead_min)
    )
    predicted_time = parse_optional_time(backtest_row.get("predicted_closest_time"))
    actual_time = parse_optional_time(
        backtest_row.get("continuous_min_time") or backtest_row.get("actual_min_time")
    )
    tcpa_min = optional_float(backtest_row.get("predicted_tcpa_min"))
    states = normalized_encounter_states(encounter_row, pair)
    t0_geometry, origin = predicted_relative_geometry(states, tcpa_min)
    future_geometry = relative_future_geometry(
        positions.get(pair[0], []),
        positions.get(pair[1], []),
        prediction_time,
        window_end,
        origin,
        predicted_time,
        actual_time,
        max_interpolation_gap_s,
        geometry_step_s,
        max_geometry_points,
    )
    source_skew = optional_float(
        backtest_row.get("prediction_state_skew_s")
        or (encounter_row or {}).get("source_state_skew_s")
        or (encounter_row or {}).get("state_skew_s")
    )
    source_ages = [
        value
        for value in (
            optional_float((encounter_row or {}).get("state_age_s_a")),
            optional_float((encounter_row or {}).get("state_age_s_b")),
        )
        if value is not None
    ]
    predicted_offset = (
        int(round((predicted_time - prediction_time).total_seconds())) if predicted_time is not None else None
    )
    actual_offset = int(round((actual_time - prediction_time).total_seconds())) if actual_time is not None else None
    predicted_time_window_covered = parse_int(backtest_row.get("predicted_time_window_covered")) == 1
    time_error_s = optional_float(backtest_row.get("predicted_to_observed_abs_delta_min"))
    time_error_s = round(time_error_s * 60) if time_error_s is not None else None
    if (
        time_error_s is None
        and predicted_time_window_covered
        and predicted_time is not None
        and actual_time is not None
    ):
        time_error_s = round(abs((actual_time - predicted_time).total_seconds()))
    if not predicted_time_window_covered:
        time_error_s = None
    predicted_dcpa = optional_float(backtest_row.get("predicted_dcpa_nm"))
    observed_distance = optional_float(
        backtest_row.get("continuous_min_distance_nm")
        or backtest_row.get("actual_min_distance_nm")
    )
    dcpa_error = optional_float(backtest_row.get("predicted_dcpa_abs_error_nm"))
    if dcpa_error is None and predicted_dcpa is not None and observed_distance is not None:
        dcpa_error = round(abs(observed_distance - predicted_dcpa), 6)
    causal_current_distance = optional_float(backtest_row.get("prediction_current_distance_nm"))
    if causal_current_distance is None:
        causal_current_distance = optional_float((encounter_row or {}).get("current_distance_nm"))
    slug = dataset_id.replace("_", "-")
    card = {
        "case_id": f"{slug}-enc-rv10-{rank:03d}",
        "rank": rank,
        "card_type": "encounter_candidate_geometric_evidence",
        "synchronized_t0": {
            "t_s": 0,
            "alignment": "causal common-time grid",
            "source_state_skew_s": optional_int(source_skew),
            "max_source_state_age_s": int(round(max(source_ages))) if source_ages else None,
            "relative_positions": t0_geometry,
        },
        "prediction": {
            "dcpa_nm": predicted_dcpa,
            "tcpa_min": tcpa_min,
            "closest_time_t_s": predicted_offset,
            "current_distance_nm": causal_current_distance,
            "screening_score": optional_float(backtest_row.get("prediction_record_score")),
        },
        "observation": {
            "method": "continuous same-track strict-future piecewise-linear minimum",
            "continuous_min_distance_nm": observed_distance,
            "continuous_min_time_t_s": actual_offset,
            "dcpa_abs_error_nm": dcpa_error,
            "closest_time_abs_error_s": time_error_s,
            "closest_time_error_eligible": predicted_time_window_covered,
        },
        "coverage": {
            "window_duration_s": int(round((window_end - prediction_time).total_seconds())),
            "common_coverage_duration_s": optional_float(
                backtest_row.get("common_coverage_duration_s"), 2
            ),
            "common_coverage_fraction": optional_float(backtest_row.get("common_coverage_fraction")),
            "common_sample_count": optional_int(
                backtest_row.get("synchronized_sample_count")
                or backtest_row.get("aligned_sample_count")
            ),
            "scheduled_sample_count": optional_int(backtest_row.get("scheduled_sample_count")),
            "max_uncovered_gap_s": optional_float(backtest_row.get("max_uncovered_gap_s"), 2),
            "predicted_time_window_covered": predicted_time_window_covered,
        },
        "support": {
            "observable": parse_int(backtest_row.get("observable_followup")) == 1,
            "within_0_5_nm": parse_int(backtest_row.get("supported_within_threshold")) == 1,
            "within_1_0_nm": parse_int(backtest_row.get("near_supported_within_1nm")) == 1,
            "status": public_support_status(backtest_row),
            "interpretation": "candidate-screening geometric support only",
        },
        "grid_sensitivity": {
            "10_s": {
                "common_sample_count": optional_int(backtest_row.get("grid_10s_common_sample_count")),
                "min_distance_nm": optional_float(backtest_row.get("grid_10s_min_distance_nm")),
            },
            "30_s": {
                "common_sample_count": optional_int(backtest_row.get("grid_30s_common_sample_count")),
                "min_distance_nm": optional_float(backtest_row.get("grid_30s_min_distance_nm")),
            },
            "60_s": {
                "common_sample_count": optional_int(backtest_row.get("grid_60s_common_sample_count")),
                "min_distance_nm": optional_float(backtest_row.get("grid_60s_min_distance_nm")),
            },
        },
        "future_geometry": future_geometry,
    }
    return card


def build_encounter_card_bundle(
    *,
    encounter_csv: Path,
    backtest_csv: Path,
    processed_dir: Path | None,
    dataset_prefix: str,
    dataset_id: str,
    max_cards: int = 12,
    lookahead_min: float = 15.0,
    max_interpolation_gap_s: int = 180,
    geometry_step_s: int = 30,
    max_geometry_points: int = 32,
) -> dict[str, Any]:
    """Build compact public encounter cards from private review-v10 evidence."""
    if lookahead_min <= 0:
        raise ValueError("lookahead_min must be positive")
    if max_interpolation_gap_s <= 0:
        raise ValueError("max_interpolation_gap_s must be positive")
    if geometry_step_s <= 0:
        raise ValueError("geometry_step_s must be positive")
    if max_geometry_points < 2:
        raise ValueError("max_geometry_points must be at least 2")
    encounter_index = load_encounter_index(encounter_csv)
    selected_rows = select_backtest_rows(load_backtest_rows(backtest_csv), max_cards)
    selected_pairs = {
        pair
        for pair in (canonical_pair(row) for row in selected_rows)
        if pair is not None
    }
    prediction_times = [
        timestamp
        for timestamp in (parse_optional_time(row.get("prediction_time")) for row in selected_rows)
        if timestamp is not None
    ]
    positions: dict[str, list[Position]] = {}
    geometry_input_status = "unavailable"
    if processed_dir is not None and prediction_times and selected_pairs:
        positions = load_positions(
            processed_dir,
            min(timestamp.date() for timestamp in prediction_times),
            max(timestamp.date() for timestamp in prediction_times),
            lookahead_min,
            {mmsi for pair in selected_pairs for mmsi in pair},
            dataset_prefix,
        )
        if any(positions.values()):
            geometry_input_status = "available"

    cards: list[dict[str, Any]] = []
    for rank, row in enumerate(selected_rows, start=1):
        card = make_encounter_card(
            rank,
            dataset_id,
            row,
            encounter_row_for_backtest(row, encounter_index),
            positions,
            lookahead_min,
            max_interpolation_gap_s,
            geometry_step_s,
            max_geometry_points,
        )
        cards.append(card)

    support_counts = collections.Counter(card["support"]["status"] for card in cards)
    bundle = {
        "schema_version": ENCOUNTER_CARD_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "publication_mode": "sanitized_research_demo",
        "disclaimer": ENCOUNTER_CARD_DISCLAIMER,
        "selection": {
            "method": "deterministic compact audit sample ranked by observability, predicted-time coverage, geometric support, common coverage, then screening score",
            "max_cards": max_cards,
            "geometry_input_status": geometry_input_status,
        },
        "card_count": len(cards),
        "summary": {
            "support_status_counts": dict(sorted(support_counts.items())),
            "geometry_available_cards": sum(
                card["future_geometry"]["status"] == "available" for card in cards
            ),
        },
        "cards": cards,
    }
    validate_public_card_bundle(bundle, expected_dataset_id=dataset_id)
    return bundle


def iter_nested(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield path + (str(key),), item
            yield from iter_nested(item, path + (str(key),))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_nested(item, path + (str(index),))


def validate_public_card_bundle(bundle: dict[str, Any], expected_dataset_id: str | None = None) -> None:
    if bundle.get("schema_version") != ENCOUNTER_CARD_SCHEMA_VERSION:
        raise ValueError("unexpected encounter evidence-card schema version")
    if expected_dataset_id is not None and bundle.get("dataset_id") != expected_dataset_id:
        raise ValueError("evidence-card dataset_id does not match its manifest")
    if bundle.get("publication_mode") != "sanitized_research_demo":
        raise ValueError("evidence cards must be in sanitized research-demo mode")
    cards = bundle.get("cards")
    if not isinstance(cards, list) or bundle.get("card_count") != len(cards):
        raise ValueError("evidence-card count does not match cards")
    expected_prefix = str(bundle.get("dataset_id") or "").replace("_", "-") + "-enc-rv10-"
    for card in cards:
        if not isinstance(card, dict) or not str(card.get("case_id") or "").startswith(expected_prefix):
            raise ValueError("evidence card has an invalid de-identified case_id")
    for path, value in iter_nested(bundle):
        key = path[-1] if path else ""
        if key.lower() in PUBLIC_CARD_SENSITIVE_KEYS:
            raise ValueError(f"sensitive key remains in evidence card: {'.'.join(path)}")
        if isinstance(value, str):
            if EXACT_TIMESTAMP_RE.search(value):
                raise ValueError(f"exact timestamp remains in evidence card: {'.'.join(path)}")
            if MMSI_LIKE_RE.search(value):
                raise ValueError(f"MMSI-like identifier remains in evidence card: {'.'.join(path)}")
    for card in cards:
        synchronized_t0 = card.get("synchronized_t0", {})
        if synchronized_t0.get("t_s") != 0:
            raise ValueError("encounter evidence card must use t0 = 0")
        vessels = (synchronized_t0.get("relative_positions") or {}).get("vessels")
        if not isinstance(vessels, list) or len(vessels) != 2:
            raise ValueError("encounter evidence card is missing synchronized t0 geometry")
        try:
            current_distance = float(card["prediction"]["current_distance_nm"])
            relative_distance = math.hypot(
                float(vessels[1]["x_nm"]) - float(vessels[0]["x_nm"]),
                float(vessels[1]["y_nm"]) - float(vessels[0]["y_nm"]),
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("encounter evidence card has incomplete causal t0 distance fields") from None
        if not math.isfinite(current_distance) or current_distance < 0:
            raise ValueError("encounter evidence card has invalid causal t0 distance")
        if abs(relative_distance - current_distance) > 0.05:
            raise ValueError("causal t0 distance disagrees with synchronized relative geometry")

        prediction = card.get("prediction", {})
        tcpa_min = optional_float(prediction.get("tcpa_min"))
        dcpa_nm = optional_float(prediction.get("dcpa_nm"))
        if tcpa_min is not None and tcpa_min > 0 and dcpa_nm is not None:
            if dcpa_nm > current_distance + 0.05:
                raise ValueError("positive-TCPA DCPA exceeds the causal t0 separation")

        window_duration_s = optional_float(card.get("coverage", {}).get("window_duration_s"))
        if window_duration_s is None or window_duration_s <= 0:
            raise ValueError("encounter evidence card has an invalid future window")
        geometry = card.get("future_geometry", {})
        for segment in geometry.get("common_segments", []):
            start_t_s = optional_float(segment.get("start_t_s"))
            end_t_s = optional_float(segment.get("end_t_s"))
            if (
                start_t_s is None
                or end_t_s is None
                or start_t_s <= 0
                or end_t_s < start_t_s
                or end_t_s > window_duration_s
            ):
                raise ValueError("future geometry extends outside the strict-future window")
            for vessel in segment.get("vessels", []):
                for point in vessel.get("points", []):
                    t_s = optional_float(point.get("t_s"))
                    if t_s is None or t_s <= 0 or t_s > window_duration_s:
                        raise ValueError("future geometry point extends outside the strict-future window")


def write_csv(path: Path, cards_json: dict[str, Any]) -> None:
    fieldnames = [
        "card_id",
        "rank",
        "cell_id",
        "screening_score",
        "hotspot_type",
        "dominant_evidence",
        "stability_class",
        "review_focus",
        "dominant_anomaly_reason",
        "ais_points",
        "moving_points",
        "anomaly_candidates",
        "corroborated_anomaly_candidates",
        "low_support_only_candidates",
        "encounter_records",
        "pair_opportunities",
        "encounter_episodes",
        "backtest_supported_episodes",
        "density_topk_days",
        "fused_topk_days",
        "related_case_ids",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for card in cards_json["cards"]:
            writer.writerow(
                {
                    "card_id": card["card_id"],
                    "rank": card["rank"],
                    "cell_id": card["cell_id"],
                    "screening_score": card["screening_score"],
                    "hotspot_type": card["hotspot_type"],
                    "dominant_evidence": card["dominant_evidence"],
                    "stability_class": card["stability_class"],
                    "review_focus": card["review_focus"],
                    "dominant_anomaly_reason": card["dominant_anomaly_reason"],
                    "ais_points": card["counts"]["ais_points"],
                    "moving_points": card["counts"]["moving_points"],
                    "anomaly_candidates": card["counts"]["anomaly_candidates"],
                    "corroborated_anomaly_candidates": card["counts"]["corroborated_anomaly_candidates"],
                    "low_support_only_candidates": card["counts"]["low_support_only_candidates"],
                    "encounter_records": card["counts"]["encounter_records"],
                    "pair_opportunities": card["counts"]["pair_opportunities"],
                    "encounter_episodes": card["counts"]["encounter_episodes"],
                    "backtest_supported_episodes": card["counts"]["backtest_supported_episodes"],
                    "density_topk_days": card["stability"]["density_topk_days"],
                    "fused_topk_days": card["stability"]["fused_topk_days"],
                    "related_case_ids": ";".join(card["related_case_ids"]),
                }
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build hotspot evidence cards.")
    parser.add_argument("--typology-csv", type=Path, help="Hotspot typology CSV.")
    parser.add_argument("--encounter-csv", type=Path, required=True, help="Encounter candidate CSV.")
    parser.add_argument("--backtest-csv", type=Path, required=True, help="Encounter backtest CSV.")
    parser.add_argument("--case-json", type=Path, help="Case studies JSON.")
    parser.add_argument("--bbox", type=float, nargs=4, help="Study area bbox.")
    parser.add_argument("--cell-size-deg", type=float, default=0.005, help="Grid cell size.")
    parser.add_argument("--episode-gap-min", type=float, default=15.0, help="Encounter episode gap.")
    parser.add_argument("--output-json", type=Path, help="Optional legacy hotspot evidence-card JSON.")
    parser.add_argument("--output-csv", type=Path, help="Optional table CSV.")
    parser.add_argument("--web-json", type=Path, nargs="*", default=[], help="Additional JSON paths to write.")
    parser.add_argument(
        "--encounter-card-output-json",
        type=Path,
        help="Optional review-v10 de-identified CPA/TCPA encounter evidence-card JSON.",
    )
    parser.add_argument(
        "--encounter-card-web-json",
        type=Path,
        nargs="*",
        default=[],
        help="Additional public encounter evidence-card JSON paths.",
    )
    parser.add_argument("--processed-dir", type=Path, help="Private feature directory used for relative future geometry.")
    parser.add_argument("--dataset-prefix", default="sf_bay_ais", help="Feature-file dataset prefix.")
    parser.add_argument("--dataset-id", default="sf_bay", help="Public web catalog dataset id.")
    parser.add_argument("--max-encounter-cards", type=int, default=12, help="Maximum compact audit cards.")
    parser.add_argument("--lookahead-min", type=float, default=15.0, help="Future evidence window in minutes.")
    parser.add_argument(
        "--max-interpolation-gap-s",
        type=int,
        default=180,
        help="Maximum same-track interpolation edge in seconds.",
    )
    parser.add_argument("--geometry-step-s", type=int, default=30, help="Public relative path sampling step.")
    parser.add_argument(
        "--max-geometry-points",
        type=int,
        default=32,
        help="Maximum display points per vessel and common segment.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output_json is None and args.encounter_card_output_json is None:
        raise SystemExit("at least one of --output-json or --encounter-card-output-json is required")
    cards = None
    if args.output_json is not None:
        if args.typology_csv is None or args.case_json is None or args.bbox is None:
            raise SystemExit("--typology-csv, --case-json, and --bbox are required with --output-json")
        cards = build_cards(
            args.typology_csv,
            args.encounter_csv,
            args.backtest_csv,
            args.case_json,
            tuple(args.bbox),
            args.cell_size_deg,
            args.episode_gap_min,
        )
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(cards, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for path in args.web_json:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cards, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if args.output_csv:
            write_csv(args.output_csv, cards)
    encounter_bundle = None
    if args.encounter_card_output_json:
        encounter_bundle = build_encounter_card_bundle(
            encounter_csv=args.encounter_csv,
            backtest_csv=args.backtest_csv,
            processed_dir=args.processed_dir,
            dataset_prefix=args.dataset_prefix,
            dataset_id=args.dataset_id,
            max_cards=args.max_encounter_cards,
            lookahead_min=args.lookahead_min,
            max_interpolation_gap_s=args.max_interpolation_gap_s,
            geometry_step_s=args.geometry_step_s,
            max_geometry_points=args.max_geometry_points,
        )
        for path in [args.encounter_card_output_json, *args.encounter_card_web_json]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(encounter_bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "card_count": cards["card_count"] if cards is not None else None,
                "total_encounter_episodes_on_cards": (
                    cards["summary"]["total_encounter_episodes_on_cards"] if cards is not None else None
                ),
                "total_backtest_supported_episodes_on_cards": (
                    cards["summary"]["total_backtest_supported_episodes_on_cards"] if cards is not None else None
                ),
                "encounter_audit_card_count": (
                    encounter_bundle["card_count"] if encounter_bundle is not None else None
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
