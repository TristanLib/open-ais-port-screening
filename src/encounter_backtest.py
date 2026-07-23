#!/usr/bin/env python3
"""Strict future geometric checks for CPA/TCPA encounter candidates.

This script does not validate collisions, near misses, or operational alerts.
It groups repeated CPA/TCPA records into audit episodes, anchors prediction at
the first candidate time, then checks synchronized, interpolated positions only
after that causal prediction time.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path
from typing import NamedTuple


EARTH_RADIUS_NM = 3440.065


class Position(NamedTuple):
    """One observed position with its trajectory-segment identity."""

    timestamp: dt.datetime
    lon: float
    lat: float
    track_id: str = ""


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def xy_nm(lon: float, lat: float, ref_lon: float, ref_lat: float) -> tuple[float, float]:
    x = math.radians(lon - ref_lon) * math.cos(math.radians(ref_lat)) * EARTH_RADIUS_NM
    y = math.radians(lat - ref_lat) * EARTH_RADIUS_NM
    return x, y


def distance_nm(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    ref_lon = (lon1 + lon2) / 2
    ref_lat = (lat1 + lat2) / 2
    x1, y1 = xy_nm(lon1, lat1, ref_lon, ref_lat)
    x2, y2 = xy_nm(lon2, lat2, ref_lon, ref_lat)
    return math.hypot(x2 - x1, y2 - y1)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * pct
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[int(index)]
    fraction = index - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def rounded(value: float | int | None, digits: int = 6) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return round(float(value), digits)


def load_encounter_rows(encounter_csv: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with encounter_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                time_bin = parse_time(row["time_bin"])
            except (KeyError, ValueError):
                continue
            try:
                reference_time = parse_time(row.get("reference_time") or row["time_bin"])
            except (KeyError, ValueError):
                continue
            a, b = sorted([row.get("mmsi_a", ""), row.get("mmsi_b", "")])
            if not a or not b:
                continue
            dcpa = parse_float(row.get("dcpa_nm"))
            tcpa = parse_float(row.get("tcpa_min"))
            score = parse_float(row.get("encounter_risk_score")) or 0.0
            current_distance = parse_float(row.get("current_distance_nm"))
            lon_a = parse_float(row.get("lon_a"))
            lat_a = parse_float(row.get("lat_a"))
            lon_b = parse_float(row.get("lon_b"))
            lat_b = parse_float(row.get("lat_b"))
            prediction_mid_lon = (lon_a + lon_b) / 2 if lon_a is not None and lon_b is not None else None
            prediction_mid_lat = (lat_a + lat_b) / 2 if lat_a is not None and lat_b is not None else None
            rows.append(
                {
                    "date": row.get("date", time_bin.date().isoformat()),
                    "time_bin": time_bin,
                    "reference_time": reference_time,
                    "mmsi_a": a,
                    "mmsi_b": b,
                    "dcpa_nm": dcpa,
                    "tcpa_min": tcpa,
                    "current_distance_nm": current_distance,
                    "prediction_mid_lon": prediction_mid_lon,
                    "prediction_mid_lat": prediction_mid_lat,
                    "state_skew_s": parse_float(row.get("state_skew_s")),
                    "encounter_risk_score": score,
                    "predicted_closest_time": reference_time + dt.timedelta(minutes=tcpa or 0.0),
                }
            )
    return rows


def group_episodes(
    encounter_rows: list[dict[str, object]],
    episode_gap_min: float,
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in encounter_rows:
        key = (str(row["date"]), str(row["mmsi_a"]), str(row["mmsi_b"]))
        grouped.setdefault(key, []).append(row)

    episodes: list[dict[str, object]] = []
    gap = dt.timedelta(minutes=episode_gap_min)
    for (date, mmsi_a, mmsi_b), rows in grouped.items():
        rows.sort(key=lambda item: item["reference_time"])  # type: ignore[index]
        current: list[dict[str, object]] = []
        previous_time: dt.datetime | None = None
        for row in rows:
            reference_time = row["reference_time"]  # type: ignore[assignment]
            if previous_time is None or reference_time - previous_time <= gap:
                current.append(row)
            else:
                episodes.append(build_episode(date, mmsi_a, mmsi_b, current))
                current = [row]
            previous_time = reference_time
        if current:
            episodes.append(build_episode(date, mmsi_a, mmsi_b, current))

    episodes.sort(key=lambda item: (str(item["start_time"]), str(item["mmsi_a"]), str(item["mmsi_b"])))
    for index, episode in enumerate(episodes, start=1):
        episode["episode_id"] = f"enc_episode_{index:05d}"
    return episodes


def build_episode(
    date: str,
    mmsi_a: str,
    mmsi_b: str,
    records: list[dict[str, object]],
) -> dict[str, object]:
    records_by_score = sorted(records, key=lambda item: float(item["encounter_risk_score"]), reverse=True)
    highest_score_record = records_by_score[0]
    causal_record = min(records, key=lambda item: item.get("reference_time", item["time_bin"]))
    dcpa_values = [float(row["dcpa_nm"]) for row in records if row.get("dcpa_nm") is not None]
    episode_current_distances = [
        float(row["current_distance_nm"]) for row in records if row.get("current_distance_nm") is not None
    ]
    times = [row.get("reference_time", row["time_bin"]) for row in records]
    return {
        "date": date,
        "mmsi_a": mmsi_a,
        "mmsi_b": mmsi_b,
        "start_time": min(times),
        "end_time": max(times),
        "record_count": len(records),
        "prediction_time": causal_record.get("reference_time", causal_record["time_bin"]),
        "representative_time_bin": causal_record["time_bin"],
        "predicted_closest_time": causal_record["predicted_closest_time"],
        "predicted_dcpa_nm": causal_record.get("dcpa_nm"),
        "predicted_tcpa_min": causal_record.get("tcpa_min"),
        "prediction_record_score": causal_record["encounter_risk_score"],
        "prediction_mid_lon": causal_record.get("prediction_mid_lon"),
        "prediction_mid_lat": causal_record.get("prediction_mid_lat"),
        "prediction_state_skew_s": causal_record.get("state_skew_s"),
        # This is the current separation at the first causal record (t0).
        # The episode-wide minimum below is descriptive and must not be
        # presented as the prediction-time separation.
        "prediction_current_distance_nm": causal_record.get("current_distance_nm"),
        "max_record_score": highest_score_record["encounter_risk_score"],
        "min_record_dcpa_nm": min(dcpa_values) if dcpa_values else None,
        "min_current_distance_nm": (
            min(episode_current_distances) if episode_current_distances else None
        ),
    }


def load_positions(
    processed_dir: Path,
    start: dt.date,
    end: dt.date,
    lookahead_min: float,
    wanted_mmsi: set[str],
    dataset_prefix: str = "sf_bay_ais",
) -> dict[str, list[Position]]:
    extra_days = math.ceil(lookahead_min / (24 * 60))
    positions: dict[str, list[Position]] = {mmsi: [] for mmsi in wanted_mmsi}
    for day in iter_dates(start, end + dt.timedelta(days=extra_days)):
        feature_path = processed_dir / f"{dataset_prefix}_{day.isoformat()}_features.csv"
        if not feature_path.exists():
            continue
        with feature_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                mmsi = row.get("mmsi", "")
                if mmsi not in wanted_mmsi:
                    continue
                lon = parse_float(row.get("longitude"))
                lat = parse_float(row.get("latitude"))
                if lon is None or lat is None:
                    continue
                try:
                    timestamp = parse_time(row["base_date_time"])
                except (KeyError, ValueError):
                    continue
                source_track_id = row.get("track_id", "").strip()
                if not source_track_id:
                    continue
                # Daily feature extraction restarts its segment counter.  A
                # raw id such as ``123456789_0001`` can therefore occur on
                # adjacent dates without denoting one continuous segment.
                # Qualifying with the source day makes the continuity key
                # globally unambiguous and conservatively prevents a
                # cross-midnight interpolation edge.
                track_id = f"{day.isoformat()}::{source_track_id}"
                positions[mmsi].append(Position(timestamp, lon, lat, track_id))

    for rows in positions.values():
        rows.sort(key=lambda item: item[0])
    return positions


def window_positions(rows: list[Position], start: dt.datetime, end: dt.datetime) -> list[Position]:
    times = [item[0] for item in rows]
    left = bisect.bisect_left(times, start)
    right = bisect.bisect_right(times, end)
    return rows[left:right]


def interpolate_position(
    rows: list[Position],
    timestamp: dt.datetime,
    max_interpolation_gap_s: int,
    times: list[dt.datetime] | None = None,
    *,
    window_start: dt.datetime | None = None,
    window_end: dt.datetime | None = None,
) -> tuple[float, float] | None:
    """Reconstruct a position without crossing a segment or outcome window.

    Both interpolation endpoints must be strictly after ``window_start`` and
    no later than ``window_end``.  Exact observed positions follow the same
    window and non-empty-track rules.
    """
    if not rows:
        return None
    if window_start is not None and timestamp <= window_start:
        return None
    if window_end is not None and timestamp > window_end:
        return None
    if times is None:
        times = [item.timestamp for item in rows]
    index = bisect.bisect_left(times, timestamp)
    if index < len(rows) and rows[index].timestamp == timestamp:
        exact = rows[index]
        if not exact.track_id:
            return None
        if window_start is not None and exact.timestamp <= window_start:
            return None
        if window_end is not None and exact.timestamp > window_end:
            return None
        return exact.lon, exact.lat
    if index == 0 or index >= len(rows):
        return None
    before = rows[index - 1]
    after = rows[index]
    if not before.track_id or before.track_id != after.track_id:
        return None
    if window_start is not None and (before.timestamp <= window_start or after.timestamp <= window_start):
        return None
    if window_end is not None and (before.timestamp > window_end or after.timestamp > window_end):
        return None
    gap_s = (after.timestamp - before.timestamp).total_seconds()
    if gap_s <= 0 or gap_s > max_interpolation_gap_s:
        return None
    fraction = (timestamp - before.timestamp).total_seconds() / gap_s
    lon = before.lon + (after.lon - before.lon) * fraction
    lat = before.lat + (after.lat - before.lat) * fraction
    return lon, lat


Segment = tuple[Position, Position]
TimeInterval = tuple[dt.datetime, dt.datetime]


def eligible_segments(
    rows: list[Position],
    window_start: dt.datetime,
    window_end: dt.datetime,
    max_interpolation_gap_s: int,
) -> list[Segment]:
    """Return same-track, future-only piecewise-linear trajectory edges."""
    future_rows = [row for row in rows if window_start < row.timestamp <= window_end and row.track_id]
    segments: list[Segment] = []
    for before, after in zip(future_rows, future_rows[1:]):
        gap_s = (after.timestamp - before.timestamp).total_seconds()
        if (
            before.track_id == after.track_id
            and 0 < gap_s <= max_interpolation_gap_s
        ):
            segments.append((before, after))
    return segments


def segment_position(segment: Segment, timestamp: dt.datetime) -> tuple[float, float]:
    before, after = segment
    duration_s = (after.timestamp - before.timestamp).total_seconds()
    if duration_s <= 0:
        return before.lon, before.lat
    fraction = (timestamp - before.timestamp).total_seconds() / duration_s
    return (
        before.lon + (after.lon - before.lon) * fraction,
        before.lat + (after.lat - before.lat) * fraction,
    )


def merge_intervals(intervals: list[TimeInterval]) -> list[TimeInterval]:
    if not intervals:
        return []
    merged: list[list[dt.datetime]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def continuous_common_min_distance(
    segments_a: list[Segment],
    segments_b: list[Segment],
) -> tuple[float | None, dt.datetime | None, list[TimeInterval]]:
    """Solve minimum separation over overlapping linear segment intervals."""
    best_distance: float | None = None
    best_time: dt.datetime | None = None
    overlaps: list[TimeInterval] = []
    index_a = 0
    index_b = 0
    while index_a < len(segments_a) and index_b < len(segments_b):
        segment_a = segments_a[index_a]
        segment_b = segments_b[index_b]
        overlap_start = max(segment_a[0].timestamp, segment_b[0].timestamp)
        overlap_end = min(segment_a[1].timestamp, segment_b[1].timestamp)
        if overlap_start <= overlap_end:
            overlaps.append((overlap_start, overlap_end))
            a_start = segment_position(segment_a, overlap_start)
            b_start = segment_position(segment_b, overlap_start)
            a_end = segment_position(segment_a, overlap_end)
            b_end = segment_position(segment_b, overlap_end)

            ref_lon = (a_start[0] + b_start[0] + a_end[0] + b_end[0]) / 4
            ref_lat = (a_start[1] + b_start[1] + a_end[1] + b_end[1]) / 4
            ax0, ay0 = xy_nm(a_start[0], a_start[1], ref_lon, ref_lat)
            bx0, by0 = xy_nm(b_start[0], b_start[1], ref_lon, ref_lat)
            ax1, ay1 = xy_nm(a_end[0], a_end[1], ref_lon, ref_lat)
            bx1, by1 = xy_nm(b_end[0], b_end[1], ref_lon, ref_lat)
            rx0 = bx0 - ax0
            ry0 = by0 - ay0
            duration_s = (overlap_end - overlap_start).total_seconds()
            if duration_s <= 0:
                fraction = 0.0
            else:
                drx = (bx1 - ax1) - rx0
                dry = (by1 - ay1) - ry0
                delta2 = drx * drx + dry * dry
                fraction = max(0.0, min(1.0, -((rx0 * drx + ry0 * dry) / delta2))) if delta2 > 1e-18 else 0.0
            closest_rx = rx0 + ((bx1 - ax1) - rx0) * fraction
            closest_ry = ry0 + ((by1 - ay1) - ry0) * fraction
            candidate_distance = math.hypot(closest_rx, closest_ry)
            candidate_time = overlap_start + dt.timedelta(seconds=duration_s * fraction)
            if best_distance is None or candidate_distance < best_distance:
                best_distance = candidate_distance
                best_time = candidate_time

        if segment_a[1].timestamp <= segment_b[1].timestamp:
            index_a += 1
        else:
            index_b += 1

    return best_distance, best_time, merge_intervals(overlaps)


def interval_coverage_metrics(
    intervals: list[TimeInterval],
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> tuple[float, float]:
    coverage_s = sum(max(0.0, (end - start).total_seconds()) for start, end in intervals)
    cursor = window_start
    max_gap_s = 0.0
    for start, end in intervals:
        max_gap_s = max(max_gap_s, max(0.0, (start - cursor).total_seconds()))
        cursor = max(cursor, end)
    max_gap_s = max(max_gap_s, max(0.0, (window_end - cursor).total_seconds()))
    return coverage_s, max_gap_s


def interval_overlaps(
    intervals: list[TimeInterval],
    target_start: dt.datetime,
    target_end: dt.datetime,
) -> bool:
    return any(max(start, target_start) <= min(end, target_end) for start, end in intervals)


def grid_distance_metrics(
    rows_a: list[Position],
    rows_b: list[Position],
    prediction_time: dt.datetime,
    window_end: dt.datetime,
    step_s: int,
    max_interpolation_gap_s: int,
) -> dict[str, object]:
    times_a = [item.timestamp for item in rows_a]
    times_b = [item.timestamp for item in rows_b]
    best_distance: float | None = None
    best_time: dt.datetime | None = None
    common_count = 0
    timestamp = prediction_time + dt.timedelta(seconds=step_s)
    evaluation_times: list[dt.datetime] = []
    while timestamp <= window_end:
        evaluation_times.append(timestamp)
        timestamp += dt.timedelta(seconds=step_s)
    if not evaluation_times or evaluation_times[-1] < window_end:
        evaluation_times.append(window_end)
    for timestamp in evaluation_times:
        position_a = interpolate_position(
            rows_a,
            timestamp,
            max_interpolation_gap_s,
            times_a,
            window_start=prediction_time,
            window_end=window_end,
        )
        position_b = interpolate_position(
            rows_b,
            timestamp,
            max_interpolation_gap_s,
            times_b,
            window_start=prediction_time,
            window_end=window_end,
        )
        if position_a is None or position_b is None:
            continue
        common_count += 1
        observed_distance = distance_nm(position_a[0], position_a[1], position_b[0], position_b[1])
        if best_distance is None or observed_distance < best_distance:
            best_distance = observed_distance
            best_time = timestamp
    return {
        "step_s": step_s,
        "scheduled_count": len(evaluation_times),
        "common_sample_count": common_count,
        "min_distance_nm": best_distance,
        "min_time": best_time,
    }


def interpolated_future_min_distance(
    rows_a: list[Position],
    rows_b: list[Position],
    prediction_time: dt.datetime,
    window_end: dt.datetime,
    evaluation_step_s: int,
    max_interpolation_gap_s: int,
) -> dict[str, object]:
    if evaluation_step_s <= 0:
        raise ValueError("evaluation_step_s must be positive")
    if window_end <= prediction_time:
        raise ValueError("window_end must be after prediction_time")

    window_a = window_positions(rows_a, prediction_time + dt.timedelta(microseconds=1), window_end)
    window_b = window_positions(rows_b, prediction_time + dt.timedelta(microseconds=1), window_end)
    segments_a = eligible_segments(window_a, prediction_time, window_end, max_interpolation_gap_s)
    segments_b = eligible_segments(window_b, prediction_time, window_end, max_interpolation_gap_s)
    best_distance, best_time, common_intervals = continuous_common_min_distance(segments_a, segments_b)
    coverage_s, max_uncovered_gap_s = interval_coverage_metrics(common_intervals, prediction_time, window_end)

    sensitivity: dict[str, dict[str, object]] = {}
    for step_s in sorted({10, 30, 60, evaluation_step_s}):
        sensitivity[str(step_s)] = grid_distance_metrics(
            window_a,
            window_b,
            prediction_time,
            window_end,
            step_s,
            max_interpolation_gap_s,
        )
    primary_grid = sensitivity[str(evaluation_step_s)]
    if best_distance is None and primary_grid["min_distance_nm"] is not None:
        best_distance = float(primary_grid["min_distance_nm"])
        best_time = primary_grid["min_time"]  # type: ignore[assignment]

    return {
        "actual_min_distance_nm": best_distance,
        "actual_min_time": best_time,
        "continuous_min_distance_nm": best_distance,
        "continuous_min_time": best_time,
        "synchronized_sample_count": int(primary_grid["common_sample_count"]),
        "aligned_sample_count": int(primary_grid["common_sample_count"]),
        "scheduled_sample_count": int(primary_grid["scheduled_count"]),
        "observations_a": len(window_a),
        "observations_b": len(window_b),
        "common_coverage_duration_s": coverage_s,
        "max_uncovered_gap_s": max_uncovered_gap_s,
        "common_intervals": common_intervals,
        "grid_sensitivity": sensitivity,
    }


def backtest_episodes(
    episodes: list[dict[str, object]],
    positions: dict[str, list[Position]],
    lookahead_min: float,
    evaluation_step_s: int,
    max_interpolation_gap_s: int,
    support_distance_nm: float,
    near_distance_nm: float,
    min_common_fraction: float = 0.70,
    max_uncovered_gap_s: int = 180,
    predicted_time_tolerance_s: int = 60,
) -> list[dict[str, object]]:
    if not 0 < min_common_fraction <= 1:
        raise ValueError("min_common_fraction must be in (0, 1]")
    rows: list[dict[str, object]] = []
    for episode in episodes:
        prediction_time: dt.datetime = episode["prediction_time"]  # type: ignore[assignment]
        window_end = prediction_time + dt.timedelta(minutes=lookahead_min)
        mmsi_a = str(episode["mmsi_a"])
        mmsi_b = str(episode["mmsi_b"])
        actual = interpolated_future_min_distance(
            positions.get(mmsi_a, []),
            positions.get(mmsi_b, []),
            prediction_time,
            window_end,
            evaluation_step_s,
            max_interpolation_gap_s,
        )
        actual_min = actual["actual_min_distance_nm"]
        actual_time = actual["actual_min_time"]
        scheduled_count = int(actual["scheduled_sample_count"])
        synchronized_count = int(actual["synchronized_sample_count"])
        common_coverage_s = float(actual["common_coverage_duration_s"])
        longest_gap_s = float(actual["max_uncovered_gap_s"])
        predicted_time: dt.datetime = episode["predicted_closest_time"]  # type: ignore[assignment]
        tolerance = dt.timedelta(seconds=predicted_time_tolerance_s)
        target_start = max(prediction_time + dt.timedelta(microseconds=1), predicted_time - tolerance)
        target_end = min(window_end, predicted_time + tolerance)
        predicted_window_covered = bool(
            target_start <= target_end
            and interval_overlaps(actual["common_intervals"], target_start, target_end)  # type: ignore[arg-type]
        )

        def observable_at(fraction: float) -> bool:
            required_samples = math.ceil(scheduled_count * fraction)
            required_duration_s = lookahead_min * 60 * fraction
            return bool(
                actual_min is not None
                and synchronized_count >= required_samples
                and common_coverage_s >= required_duration_s
                and longest_gap_s <= max_uncovered_gap_s
            )

        observable_50 = observable_at(0.50)
        observable = observable_at(min_common_fraction)
        observable_90 = observable_at(0.90)
        supported = bool(observable and float(actual_min) <= support_distance_nm)
        near_supported = bool(observable and float(actual_min) <= near_distance_nm)
        time_error = (
            abs((actual_time - predicted_time).total_seconds()) / 60
            if observable and predicted_window_covered and isinstance(actual_time, dt.datetime)
            else None
        )
        predicted_dcpa = episode.get("predicted_dcpa_nm")
        distance_error = (
            abs(float(actual_min) - float(predicted_dcpa))
            if observable and predicted_dcpa is not None
            else None
        )
        status = "future_supported_within_threshold" if supported else "future_observable_not_within_threshold"
        if not observable:
            status = "insufficient_synchronized_future_followup"
        rows.append(
            {
                **episode,
                "window_end": window_end,
                "actual_min_distance_nm": actual_min,
                "actual_min_time": actual_time,
                "continuous_min_distance_nm": actual["continuous_min_distance_nm"],
                "continuous_min_time": actual["continuous_min_time"],
                "synchronized_sample_count": synchronized_count,
                "aligned_sample_count": actual["aligned_sample_count"],
                "scheduled_sample_count": scheduled_count,
                "observations_a": actual["observations_a"],
                "observations_b": actual["observations_b"],
                "common_coverage_duration_s": common_coverage_s,
                "common_coverage_fraction": common_coverage_s / (lookahead_min * 60),
                "max_uncovered_gap_s": longest_gap_s,
                "predicted_time_window_covered": int(predicted_window_covered),
                "predicted_time_error_eligible": int(observable and predicted_window_covered),
                "observable_followup_50pct": int(observable_50),
                "observable_followup": int(observable),
                "observable_followup_90pct": int(observable_90),
                "supported_within_threshold": int(supported),
                "near_supported_within_1nm": int(near_supported),
                "grid_10s_common_sample_count": actual["grid_sensitivity"]["10"]["common_sample_count"],  # type: ignore[index]
                "grid_10s_min_distance_nm": actual["grid_sensitivity"]["10"]["min_distance_nm"],  # type: ignore[index]
                "grid_30s_common_sample_count": actual["grid_sensitivity"]["30"]["common_sample_count"],  # type: ignore[index]
                "grid_30s_min_distance_nm": actual["grid_sensitivity"]["30"]["min_distance_nm"],  # type: ignore[index]
                "grid_60s_common_sample_count": actual["grid_sensitivity"]["60"]["common_sample_count"],  # type: ignore[index]
                "grid_60s_min_distance_nm": actual["grid_sensitivity"]["60"]["min_distance_nm"],  # type: ignore[index]
                "predicted_to_observed_abs_delta_min": time_error,
                "predicted_dcpa_abs_error_nm": distance_error,
                "backtest_status": status,
            }
        )
    return rows


def summarize(
    encounter_rows: list[dict[str, object]],
    backtest_rows: list[dict[str, object]],
    support_distance_nm: float,
    near_distance_nm: float,
    lookahead_min: float,
    evaluation_step_s: int,
    max_interpolation_gap_s: int,
    min_common_fraction: float = 0.70,
    max_uncovered_gap_s: int = 180,
    predicted_time_tolerance_s: int = 60,
) -> dict[str, object]:
    observable = [row for row in backtest_rows if int(row["observable_followup"])]
    supported = [row for row in observable if int(row["supported_within_threshold"])]
    near_supported = [row for row in observable if int(row["near_supported_within_1nm"])]
    actual_distances = [float(row["actual_min_distance_nm"]) for row in observable]
    time_errors = [
        float(row["predicted_to_observed_abs_delta_min"])
        for row in observable
        if row.get("predicted_to_observed_abs_delta_min") is not None
    ]
    distance_errors = [
        float(row["predicted_dcpa_abs_error_nm"])
        for row in observable
        if row.get("predicted_dcpa_abs_error_nm") is not None
    ]
    episode_lengths = [int(row["record_count"]) for row in backtest_rows]
    common_sample_counts = [int(row["synchronized_sample_count"]) for row in backtest_rows]
    coverage_durations = [float(row["common_coverage_duration_s"]) for row in backtest_rows]
    maximum_gaps = [float(row["max_uncovered_gap_s"]) for row in backtest_rows]

    observability_sensitivity: dict[str, dict[str, object]] = {}
    for label, field in (
        ("50pct", "observable_followup_50pct"),
        ("70pct_primary", "observable_followup"),
        ("90pct", "observable_followup_90pct"),
    ):
        rows_at_threshold = [row for row in backtest_rows if int(row[field])]
        supported_at_threshold = [
            row for row in rows_at_threshold if float(row["actual_min_distance_nm"]) <= support_distance_nm
        ]
        observability_sensitivity[label] = {
            "observable_episodes": len(rows_at_threshold),
            "observable_rate": round(len(rows_at_threshold) / len(backtest_rows), 6) if backtest_rows else None,
            "supported_episodes_within_threshold": len(supported_at_threshold),
            "support_rate_observable": (
                round(len(supported_at_threshold) / len(rows_at_threshold), 6) if rows_at_threshold else None
            ),
        }

    continuous_sensitivity = {
        "episodes_with_continuous_minimum_all": sum(
            row.get("continuous_min_distance_nm") is not None for row in backtest_rows
        ),
        "primary_observable_episodes": len(observable),
        "supported_primary_observable": len(supported),
        "support_rate_primary_observable": (
            round(len(supported) / len(observable), 6) if observable else None
        ),
    }
    grid_sensitivity: dict[str, dict[str, object]] = {}
    for step_s in (10, 30, 60):
        field = f"grid_{step_s}s_min_distance_nm"
        reconstructed = [row for row in backtest_rows if row.get(field) is not None]
        supported_grid = [row for row in reconstructed if float(row[field]) <= support_distance_nm]
        observable_reconstructed = [row for row in observable if row.get(field) is not None]
        observable_supported = [
            row for row in observable_reconstructed if float(row[field]) <= support_distance_nm
        ]
        grid_sensitivity[str(step_s)] = {
            # Compatibility aliases retain the all-reconstructed denominator;
            # the explicitly named primary-observable fields below are the
            # comparison used in review-v9 reporting.
            "episodes_with_grid_minimum": len(reconstructed),
            "supported_within_threshold": len(supported_grid),
            "support_rate_reconstructed": (
                round(len(supported_grid) / len(reconstructed), 6) if reconstructed else None
            ),
            "episodes_with_grid_minimum_all": len(reconstructed),
            "supported_all_reconstructed": len(supported_grid),
            "support_rate_all_reconstructed": (
                round(len(supported_grid) / len(reconstructed), 6) if reconstructed else None
            ),
            "primary_observable_episodes_with_grid_minimum": len(observable_reconstructed),
            "supported_primary_observable": len(observable_supported),
            "support_rate_primary_observable": (
                round(len(observable_supported) / len(observable_reconstructed), 6)
                if observable_reconstructed
                else None
            ),
        }
    return {
        "method": "strict_future_synchronized_geometric_check_for_encounter_candidate_screening",
        "future_only": True,
        "encounter_candidate_records": len(encounter_rows),
        "deduplicated_encounter_episodes": len(backtest_rows),
        "episodes_with_aligned_followup": len(observable),
        "episodes_without_aligned_followup": len(backtest_rows) - len(observable),
        "support_distance_nm": support_distance_nm,
        "near_distance_nm": near_distance_nm,
        "lookahead_min": lookahead_min,
        "evaluation_step_s": evaluation_step_s,
        "max_interpolation_gap_s": max_interpolation_gap_s,
        "primary_observability": {
            "common_grid_fraction": min_common_fraction,
            "required_common_samples_30s_grid": math.ceil((lookahead_min * 60 / evaluation_step_s) * min_common_fraction),
            "required_common_coverage_duration_s": lookahead_min * 60 * min_common_fraction,
            "maximum_uncovered_gap_s": max_uncovered_gap_s,
            "predicted_closest_time_tolerance_s": predicted_time_tolerance_s,
            "predicted_closest_time_coverage_is_primary_requirement": False,
        },
        "supported_episodes_within_threshold": len(supported),
        "near_supported_episodes_within_1nm": len(near_supported),
        "support_rate_observable": round(len(supported) / len(observable), 6) if observable else None,
        "near_support_rate_observable": round(len(near_supported) / len(observable), 6) if observable else None,
        "observable_rate": round(len(observable) / len(backtest_rows), 6) if backtest_rows else None,
        "future_coverage": {
            "common_sample_count_30s": {
                "median": rounded(statistics.median(common_sample_counts), 2) if common_sample_counts else None,
                "p10": rounded(percentile([float(value) for value in common_sample_counts], 0.10), 2),
                "p90": rounded(percentile([float(value) for value in common_sample_counts], 0.90), 2),
            },
            "common_coverage_duration_s": {
                "median": rounded(statistics.median(coverage_durations), 2) if coverage_durations else None,
                "p10": rounded(percentile(coverage_durations, 0.10), 2),
                "p90": rounded(percentile(coverage_durations, 0.90), 2),
            },
            "maximum_uncovered_gap_s": {
                "median": rounded(statistics.median(maximum_gaps), 2) if maximum_gaps else None,
                "p90": rounded(percentile(maximum_gaps, 0.90), 2),
            },
        },
        "observability_sensitivity": observability_sensitivity,
        "minimum_distance_solver_sensitivity": {
            "continuous_primary": continuous_sensitivity,
            "fixed_grids": grid_sensitivity,
        },
        "minimum_distance_grid_sensitivity": grid_sensitivity,
        "actual_min_distance_nm": {
            "min": rounded(min(actual_distances), 6) if actual_distances else None,
            "median": rounded(statistics.median(actual_distances), 6) if actual_distances else None,
            "p75": rounded(percentile(actual_distances, 0.75), 6),
            "p90": rounded(percentile(actual_distances, 0.90), 6),
            "max": rounded(max(actual_distances), 6) if actual_distances else None,
        },
        "predicted_to_observed_abs_delta_min": {
            "eligible_episodes": len(time_errors),
            "eligibility": "primary observable and common coverage within predicted closest time +/- tolerance",
            "median": rounded(statistics.median(time_errors), 6) if time_errors else None,
            "p75": rounded(percentile(time_errors, 0.75), 6),
            "p90": rounded(percentile(time_errors, 0.90), 6),
        },
        "predicted_dcpa_abs_error_nm": {
            "median": rounded(statistics.median(distance_errors), 6) if distance_errors else None,
            "p75": rounded(percentile(distance_errors, 0.75), 6),
            "p90": rounded(percentile(distance_errors, 0.90), 6),
        },
        "episode_record_count": {
            "median": rounded(statistics.median(episode_lengths), 2) if episode_lengths else None,
            "p90": rounded(percentile([float(value) for value in episode_lengths], 0.90), 2),
            "max": max(episode_lengths) if episode_lengths else None,
        },
        "status_counts": {
            "future_supported_within_threshold": len(supported),
            "future_observable_not_within_threshold": len(observable) - len(supported),
            "insufficient_synchronized_future_followup": len(backtest_rows) - len(observable),
        },
        "top_supported_examples": public_examples(supported[:10]),
        "top_unsupported_examples": public_examples([row for row in observable if not int(row["supported_within_threshold"])][:10]),
    }


def public_examples(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    examples: list[dict[str, object]] = []
    for row in rows:
        examples.append(
            {
                "episode_id": row["episode_id"],
                "start_time": format_time(row["start_time"]),
                "record_count": row["record_count"],
                "predicted_dcpa_nm": rounded(row.get("predicted_dcpa_nm"), 6),
                "actual_min_distance_nm": rounded(row.get("actual_min_distance_nm"), 6),
                "synchronized_sample_count": row["synchronized_sample_count"],
            }
        )
    return examples


def format_time(value: object) -> str:
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return "" if value is None else str(value)


def write_csv(output_csv: Path, rows: list[dict[str, object]]) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "episode_id",
        "date",
        "mmsi_a",
        "mmsi_b",
        "start_time",
        "end_time",
        "prediction_time",
        "window_end",
        "record_count",
        "representative_time_bin",
        "predicted_closest_time",
        "predicted_dcpa_nm",
        "predicted_tcpa_min",
        "prediction_record_score",
        "prediction_mid_lon",
        "prediction_mid_lat",
        "prediction_state_skew_s",
        "prediction_current_distance_nm",
        "max_record_score",
        "min_record_dcpa_nm",
        "min_current_distance_nm",
        "actual_min_distance_nm",
        "actual_min_time",
        "continuous_min_distance_nm",
        "continuous_min_time",
        "synchronized_sample_count",
        "aligned_sample_count",
        "scheduled_sample_count",
        "observations_a",
        "observations_b",
        "common_coverage_duration_s",
        "common_coverage_fraction",
        "max_uncovered_gap_s",
        "predicted_time_window_covered",
        "predicted_time_error_eligible",
        "observable_followup_50pct",
        "observable_followup",
        "observable_followup_90pct",
        "supported_within_threshold",
        "near_supported_within_1nm",
        "grid_10s_common_sample_count",
        "grid_10s_min_distance_nm",
        "grid_30s_common_sample_count",
        "grid_30s_min_distance_nm",
        "grid_60s_common_sample_count",
        "grid_60s_min_distance_nm",
        "predicted_to_observed_abs_delta_min",
        "predicted_dcpa_abs_error_nm",
        "backtest_status",
    ]
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: serialize(row.get(key)) for key in fieldnames})


def serialize(value: object) -> object:
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, float):
        return round(value, 6)
    return value


def write_markdown(output_md: Path, stats: dict[str, object]) -> None:
    output_md.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("Candidate records", stats["encounter_candidate_records"]),
        ("Deduplicated audit episodes", stats["deduplicated_encounter_episodes"]),
        ("Episodes with aligned follow-up", stats["episodes_with_aligned_followup"]),
        ("Supported within DCPA threshold", stats["supported_episodes_within_threshold"]),
        ("Support rate among observable episodes", stats["support_rate_observable"]),
        ("Near-supported within 1 nm", stats["near_supported_episodes_within_1nm"]),
        ("Near-support rate among observable episodes", stats["near_support_rate_observable"]),
        ("Median actual minimum distance (nm)", stats["actual_min_distance_nm"]["median"]),  # type: ignore[index]
        ("Median predicted/observed time delta (min)", stats["predicted_to_observed_abs_delta_min"]["median"]),  # type: ignore[index]
    ]
    lines = [
        "# CPA/TCPA Strict Future Geometric Check",
        "",
        "This table checks synchronized, interpolated AIS positions strictly after the first candidate time. "
        "It is not a collision, near-miss, or operational-alert validation.",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for label, value in rows:
        if isinstance(value, float):
            rendered = f"{value:.4f}" if value < 10 else f"{value:,.2f}"
        elif isinstance(value, int):
            rendered = f"{value:,}"
        else:
            rendered = str(value)
        lines.append(f"| {label} | {rendered} |")
    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest CPA/TCPA encounter candidates against follow-up AIS tracks.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"), help="Processed data directory.")
    parser.add_argument("--encounter-csv", type=Path, required=True, help="Encounter candidate CSV.")
    parser.add_argument("--output-csv", type=Path, required=True, help="Output episode-level backtest CSV.")
    parser.add_argument("--stats-json", type=Path, required=True, help="Output summary JSON.")
    parser.add_argument("--summary-md", type=Path, help="Optional Markdown summary table.")
    parser.add_argument("--episode-gap-min", type=float, default=15.0, help="Gap threshold for deduplicating records.")
    parser.add_argument("--lookahead-min", type=float, default=15.0, help="Future window after first prediction time.")
    parser.add_argument("--evaluation-step-s", type=int, default=30, help="Common-time evaluation grid step.")
    parser.add_argument(
        "--max-interpolation-gap-s",
        type=int,
        default=180,
        help="Maximum bracketing AIS interval allowed for interpolation.",
    )
    parser.add_argument("--support-distance-nm", type=float, default=0.5, help="Actual-distance support threshold.")
    parser.add_argument("--near-distance-nm", type=float, default=1.0, help="Secondary near-support threshold.")
    parser.add_argument(
        "--min-common-fraction",
        type=float,
        default=0.70,
        help="Primary minimum common 30 s samples and continuous coverage fraction.",
    )
    parser.add_argument(
        "--max-uncovered-gap-s",
        type=int,
        default=180,
        help="Primary maximum uncovered run anywhere in the future window.",
    )
    parser.add_argument(
        "--predicted-time-tolerance-s",
        type=int,
        default=60,
        help="Required common coverage around the predicted closest time.",
    )
    parser.add_argument("--dataset-prefix", default="sf_bay_ais", help="Daily feature-file prefix.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    encounter_rows = load_encounter_rows(args.encounter_csv)
    episodes = group_episodes(encounter_rows, args.episode_gap_min)
    wanted_mmsi = {str(row["mmsi_a"]) for row in episodes} | {str(row["mmsi_b"]) for row in episodes}
    positions = load_positions(
        args.processed_dir,
        args.start,
        args.end,
        args.lookahead_min,
        wanted_mmsi,
        dataset_prefix=args.dataset_prefix,
    )
    backtest_rows = backtest_episodes(
        episodes,
        positions,
        args.lookahead_min,
        args.evaluation_step_s,
        args.max_interpolation_gap_s,
        args.support_distance_nm,
        args.near_distance_nm,
        args.min_common_fraction,
        args.max_uncovered_gap_s,
        args.predicted_time_tolerance_s,
    )
    stats = summarize(
        encounter_rows,
        backtest_rows,
        args.support_distance_nm,
        args.near_distance_nm,
        args.lookahead_min,
        args.evaluation_step_s,
        args.max_interpolation_gap_s,
        args.min_common_fraction,
        args.max_uncovered_gap_s,
        args.predicted_time_tolerance_s,
    )
    write_csv(args.output_csv, backtest_rows)
    args.stats_json.parent.mkdir(parents=True, exist_ok=True)
    args.stats_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.summary_md:
        write_markdown(args.summary_md, stats)
    print(
        json.dumps(
            {
                "candidate_records": stats["encounter_candidate_records"],
                "episodes": stats["deduplicated_encounter_episodes"],
                "observable": stats["episodes_with_aligned_followup"],
                "supported": stats["supported_episodes_within_threshold"],
                "support_rate_observable": stats["support_rate_observable"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
