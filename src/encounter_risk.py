#!/usr/bin/env python3
"""Approximate CPA/TCPA encounter-candidate extraction from AIS feature data."""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path


EARTH_RADIUS_NM = 3440.065


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


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


def floor_time(timestamp: dt.datetime, seconds: int) -> dt.datetime:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    epoch = int(timestamp.timestamp())
    return dt.datetime.fromtimestamp(epoch - epoch % seconds)


def ceil_time(timestamp: dt.datetime, seconds: int) -> dt.datetime:
    """Return the first epoch-aligned grid time at or after ``timestamp``."""
    floored = floor_time(timestamp, seconds)
    if floored == timestamp:
        return floored
    return floored + dt.timedelta(seconds=seconds)


def xy_nm(lon: float, lat: float, ref_lon: float, ref_lat: float) -> tuple[float, float]:
    x = math.radians(lon - ref_lon) * math.cos(math.radians(ref_lat)) * EARTH_RADIUS_NM
    y = math.radians(lat - ref_lat) * EARTH_RADIUS_NM
    return x, y


def velocity_kn(sog: float, bearing_deg: float) -> tuple[float, float]:
    angle = math.radians(bearing_deg)
    return sog * math.sin(angle), sog * math.cos(angle)


def state_timestamp(state: dict[str, object]) -> dt.datetime:
    value = state["timestamp"]
    if isinstance(value, dt.datetime):
        return value
    return parse_time(str(value))


def propagate_state_to(state: dict[str, object], reference_time: dt.datetime) -> dict[str, object]:
    """Propagate one AIS state forward under the same constant-velocity CPA assumption."""
    timestamp = state_timestamp(state)
    elapsed_h = (reference_time - timestamp).total_seconds() / 3600
    if elapsed_h < 0:
        raise ValueError("reference_time must not precede the AIS state")

    aligned = dict(state)
    if elapsed_h == 0:
        aligned["timestamp"] = reference_time
        return aligned

    east_kn, north_kn = velocity_kn(float(state["sog"]), float(state["bearing"]))
    east_nm = east_kn * elapsed_h
    north_nm = north_kn * elapsed_h
    lat = float(state["lat"])
    lon = float(state["lon"])
    aligned["lat"] = lat + math.degrees(north_nm / EARTH_RADIUS_NM)
    lon_scale = EARTH_RADIUS_NM * math.cos(math.radians(lat))
    aligned["lon"] = lon + math.degrees(east_nm / lon_scale) if abs(lon_scale) > 1e-12 else lon
    aligned["timestamp"] = reference_time
    return aligned


def synchronize_pair_states(
    a: dict[str, object],
    b: dict[str, object],
    max_state_skew_s: int,
) -> tuple[dict[str, object], dict[str, object], dt.datetime, float] | None:
    """Align a vessel pair to the later observation time or reject excessive skew."""
    timestamp_a = state_timestamp(a)
    timestamp_b = state_timestamp(b)
    state_skew_s = abs((timestamp_a - timestamp_b).total_seconds())
    if state_skew_s > max_state_skew_s:
        return None
    reference_time = max(timestamp_a, timestamp_b)
    return (
        propagate_state_to(a, reference_time),
        propagate_state_to(b, reference_time),
        reference_time,
        state_skew_s,
    )


def select_ground_track_course(row: dict[str, object]) -> tuple[float | None, str]:
    """Select a causal ground-track course and report its auditable source.

    A segment-derived value is valid only away from a recorded segment start.
    When it is unavailable, a valid native COG may be used as the fallback.
    Heading is deliberately not treated as an interchangeable velocity course.
    """
    break_reason = str(row.get("segment_break_reason") or "").strip()
    point_index = str(row.get("point_index") or "").strip()
    derived = parse_float(str(row.get("bearing_deg") or ""))
    if not break_reason and point_index != "0" and derived is not None and 0.0 <= derived < 360.0:
        return derived % 360.0, "segment_derived"

    native_cog = parse_float(str(row.get("cog") or ""))
    if native_cog is not None and 0.0 <= native_cog < 360.0:
        return native_cog % 360.0, "native_cog_fallback"
    return None, "unavailable"


def build_causal_grid_states(
    rows: list[dict[str, object]],
    time_bin_seconds: int,
    max_state_age_s: int,
) -> dict[dt.datetime, dict[str, dict[str, object]]]:
    """Build epoch-aligned, causal vessel states on a common time grid.

    Each source state contributes only to grid times at or after its timestamp
    and no more than ``max_state_age_s`` later.  If multiple source states can
    represent a vessel at the same grid time, the most recent one wins.
    """
    if time_bin_seconds <= 0:
        raise ValueError("time_bin_seconds must be positive")
    if max_state_age_s < 0:
        raise ValueError("max_state_age_s must be non-negative")

    selected_bins: dict[dt.datetime, dict[str, dict[str, object]]] = {}
    for state in rows:
        mmsi = str(state.get("mmsi") or "")
        if not mmsi:
            continue
        try:
            source_timestamp = state_timestamp(state)
            lon = float(state["lon"])
            lat = float(state["lat"])
            sog = float(state["sog"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (lon, lat, sog)):
            continue

        grid_time = ceil_time(source_timestamp, time_bin_seconds)
        latest_grid_time = source_timestamp + dt.timedelta(seconds=max_state_age_s)
        while grid_time <= latest_grid_time:
            current = selected_bins.setdefault(grid_time, {}).get(mmsi)
            current_source = state_timestamp(current) if current is not None else None
            if current_source is None or source_timestamp > current_source:
                selected_bins[grid_time][mmsi] = state
            grid_time += dt.timedelta(seconds=time_bin_seconds)

    bins: dict[dt.datetime, dict[str, dict[str, object]]] = {}
    for grid_time, selected_vessels in selected_bins.items():
        for mmsi, state in selected_vessels.items():
            try:
                bearing = float(state["bearing"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(bearing):
                continue
            source_timestamp = state_timestamp(state)
            aligned = propagate_state_to(state, grid_time)
            aligned["mmsi"] = mmsi
            aligned["source_timestamp"] = source_timestamp
            aligned["source_lon"] = float(state["lon"])
            aligned["source_lat"] = float(state["lat"])
            aligned["state_age_s"] = (grid_time - source_timestamp).total_seconds()
            bins.setdefault(grid_time, {})[mmsi] = aligned
    return bins


def distance_nm(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    ref_lon = (lon1 + lon2) / 2
    ref_lat = (lat1 + lat2) / 2
    x1, y1 = xy_nm(lon1, lat1, ref_lon, ref_lat)
    x2, y2 = xy_nm(lon2, lat2, ref_lon, ref_lat)
    return math.hypot(x2 - x1, y2 - y1)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * pct
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def cpa_tcpa(
    a: dict[str, object],
    b: dict[str, object],
) -> tuple[float, float, float]:
    ref_lon = (float(a["lon"]) + float(b["lon"])) / 2
    ref_lat = (float(a["lat"]) + float(b["lat"])) / 2
    ax, ay = xy_nm(float(a["lon"]), float(a["lat"]), ref_lon, ref_lat)
    bx, by = xy_nm(float(b["lon"]), float(b["lat"]), ref_lon, ref_lat)
    avx, avy = velocity_kn(float(a["sog"]), float(a["bearing"]))
    bvx, bvy = velocity_kn(float(b["sog"]), float(b["bearing"]))

    rx = bx - ax
    ry = by - ay
    rvx = bvx - avx
    rvy = bvy - avy
    rv2 = rvx * rvx + rvy * rvy
    current_distance = math.hypot(rx, ry)
    if rv2 <= 1e-9:
        return current_distance, math.inf, current_distance

    tcpa_h = -((rx * rvx + ry * rvy) / rv2)
    if tcpa_h <= 0:
        return current_distance, tcpa_h * 60, current_distance
    dcpa = math.hypot(rx + rvx * tcpa_h, ry + rvy * tcpa_h)
    return dcpa, tcpa_h * 60, current_distance


def relative_motion_metrics(
    a: dict[str, object],
    b: dict[str, object],
) -> tuple[float, float]:
    """Return relative speed and non-negative instantaneous closing speed."""
    ref_lon = (float(a["lon"]) + float(b["lon"])) / 2
    ref_lat = (float(a["lat"]) + float(b["lat"])) / 2
    ax, ay = xy_nm(float(a["lon"]), float(a["lat"]), ref_lon, ref_lat)
    bx, by = xy_nm(float(b["lon"]), float(b["lat"]), ref_lon, ref_lat)
    avx, avy = velocity_kn(float(a["sog"]), float(a["bearing"]))
    bvx, bvy = velocity_kn(float(b["sog"]), float(b["bearing"]))
    rx, ry = bx - ax, by - ay
    rvx, rvy = bvx - avx, bvy - avy
    relative_speed = math.hypot(rvx, rvy)
    current_distance = math.hypot(rx, ry)
    closing_speed = max(0.0, -((rx * rvx + ry * rvy) / current_distance)) if current_distance > 1e-12 else 0.0
    return relative_speed, closing_speed


def finite_or_blank(value: float, digits: int = 6) -> float | str:
    return round(value, digits) if math.isfinite(value) else ""


def spatial_bucket(lon: float, lat: float, cell_size_deg: float) -> tuple[int, int]:
    if cell_size_deg <= 0:
        raise ValueError("cell_size_deg must be positive")
    return math.floor(lon / cell_size_deg), math.floor(lat / cell_size_deg)


def analysis_cell_for_point(
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


def neighbor_buckets(
    bucket: tuple[int, int],
    lon_radius: int = 1,
    lat_radius: int | None = None,
) -> list[tuple[int, int]]:
    if lon_radius < 0:
        raise ValueError("lon_radius must be non-negative")
    if lat_radius is None:
        lat_radius = lon_radius
    if lat_radius < 0:
        raise ValueError("lat_radius must be non-negative")
    x, y = bucket
    return [
        (x + dx, y + dy)
        for dx in range(-lon_radius, lon_radius + 1)
        for dy in range(-lat_radius, lat_radius + 1)
    ]


def dynamic_bucket_radii(
    states: list[dict[str, object]],
    max_distance_nm: float,
    spatial_cell_deg: float,
) -> tuple[int, int]:
    """Return conservative lon/lat bucket radii for a nautical-mile search."""
    if max_distance_nm < 0:
        raise ValueError("max_distance_nm must be non-negative")
    if spatial_cell_deg <= 0:
        raise ValueError("spatial_cell_deg must be positive")
    if not states or max_distance_nm == 0:
        return 0, 0

    max_abs_lat = max(abs(float(state["lat"])) for state in states)
    latitude_radius_deg = math.degrees(max_distance_nm / EARTH_RADIUS_NM)
    poleward_latitude = min(89.999999, max_abs_lat + latitude_radius_deg)
    longitude_scale = math.cos(math.radians(poleward_latitude))
    if longitude_scale <= 0:
        raise ValueError("longitude bucket search is undefined at the pole")
    longitude_radius_deg = math.degrees(max_distance_nm / (EARTH_RADIUS_NM * longitude_scale))
    return (
        math.ceil(longitude_radius_deg / spatial_cell_deg),
        math.ceil(latitude_radius_deg / spatial_cell_deg),
    )


def _indexed_pairs_within_distance(
    states: list[dict[str, object]],
    max_distance_nm: float,
    spatial_cell_deg: float,
) -> tuple[set[tuple[str, str]], int]:
    """Return exact within-radius MMSI pairs and the number of index candidates."""
    lon_radius, lat_radius = dynamic_bucket_radii(states, max_distance_nm, spatial_cell_deg)
    by_bucket: dict[tuple[int, int], list[int]] = {}
    for index, state in enumerate(states):
        bucket = spatial_bucket(float(state["lon"]), float(state["lat"]), spatial_cell_deg)
        by_bucket.setdefault(bucket, []).append(index)

    pairs: set[tuple[str, str]] = set()
    candidate_checks = 0
    seen_indices: set[tuple[int, int]] = set()
    for bucket, indices in by_bucket.items():
        candidate_indices: list[int] = []
        for neighbor in neighbor_buckets(bucket, lon_radius, lat_radius):
            candidate_indices.extend(by_bucket.get(neighbor, []))
        for left_index in indices:
            left = states[left_index]
            for right_index in candidate_indices:
                if right_index == left_index:
                    continue
                index_pair = tuple(sorted((left_index, right_index)))
                if index_pair in seen_indices:
                    continue
                seen_indices.add(index_pair)
                right = states[right_index]
                left_mmsi = str(left.get("mmsi") or "")
                right_mmsi = str(right.get("mmsi") or "")
                if not left_mmsi or not right_mmsi or left_mmsi == right_mmsi:
                    continue
                candidate_checks += 1
                if distance_nm(
                    float(left["lon"]),
                    float(left["lat"]),
                    float(right["lon"]),
                    float(right["lat"]),
                ) <= max_distance_nm:
                    pairs.add(tuple(sorted((left_mmsi, right_mmsi))))
    return pairs, candidate_checks


def indexed_pairs_within_distance(
    states: list[dict[str, object]],
    max_distance_nm: float,
    spatial_cell_deg: float,
) -> set[tuple[str, str]]:
    """Return every vessel pair within ``max_distance_nm`` after exact filtering."""
    pairs, _ = _indexed_pairs_within_distance(states, max_distance_nm, spatial_cell_deg)
    return pairs


def brute_force_pairs_within_distance(
    states: list[dict[str, object]],
    max_distance_nm: float,
) -> set[tuple[str, str]]:
    """Independent all-pairs reference used for production completeness audits."""
    pairs: set[tuple[str, str]] = set()
    for left_index, left in enumerate(states):
        left_mmsi = str(left.get("mmsi") or "")
        if not left_mmsi:
            continue
        for right in states[left_index + 1 :]:
            right_mmsi = str(right.get("mmsi") or "")
            if not right_mmsi or left_mmsi == right_mmsi:
                continue
            if distance_nm(
                float(left["lon"]),
                float(left["lat"]),
                float(right["lon"]),
                float(right["lat"]),
            ) <= max_distance_nm:
                pairs.add(tuple(sorted((left_mmsi, right_mmsi))))
    return pairs


def summarize_deduplicated_events(
    encounter_rows: list[dict[str, object]],
    episode_gap_min: float,
) -> dict[str, int | float]:
    """Summarize repeated time-bin records without treating them as labels."""
    pair_times: dict[tuple[str, str, str], list[dt.datetime]] = {}
    pair_days: set[tuple[str, str, str]] = set()
    pairs: set[tuple[str, str]] = set()

    for row in encounter_rows:
        a, b = sorted([str(row["mmsi_a"]), str(row["mmsi_b"])])
        date = str(row["date"])
        time_bin = parse_time(str(row.get("reference_time") or row["time_bin"]))
        pairs.add((a, b))
        pair_days.add((date, a, b))
        pair_times.setdefault((date, a, b), []).append(time_bin)

    episodes = 0
    gap_seconds = episode_gap_min * 60
    for times in pair_times.values():
        previous: dt.datetime | None = None
        for current in sorted(times):
            if previous is None or (current - previous).total_seconds() > gap_seconds:
                episodes += 1
            previous = current

    return {
        "unique_vessel_pairs": len(pairs),
        "unique_vessel_pair_days": len(pair_days),
        "deduplicated_encounter_episodes": episodes,
        "episode_gap_min": episode_gap_min,
    }


def load_feature_states(feature_path: Path, min_sog: float) -> list[dict[str, object]]:
    """Load usable causal motion states from one daily feature file."""
    states: list[dict[str, object]] = []
    with feature_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            sog = parse_float(row.get("sog"))
            if sog is None or sog < min_sog:
                continue
            lon = parse_float(row.get("longitude"))
            lat = parse_float(row.get("latitude"))
            bearing, direction_source = select_ground_track_course(row)  # type: ignore[arg-type]
            if lon is None or lat is None:
                continue
            mmsi = row.get("mmsi", "")
            if not mmsi:
                continue
            try:
                timestamp = parse_time(row["base_date_time"])
            except (KeyError, ValueError):
                continue
            states.append(
                {
                    "mmsi": mmsi,
                    "timestamp": timestamp,
                    "lon": lon,
                    "lat": lat,
                    "sog": sog,
                    "bearing": bearing,
                    "direction_source": direction_source,
                    "track_id": row.get("track_id", ""),
                    "vessel_type": row.get("vessel_type", ""),
                    "length": row.get("length", ""),
                }
            )
    return states


def load_day_states(
    feature_path: Path,
    time_bin_seconds: int,
    min_sog: float,
    max_state_age_s: int | None = None,
) -> dict[dt.datetime, dict[str, dict[str, object]]]:
    """Compatibility wrapper returning causal common-grid states for one file."""
    states = load_feature_states(feature_path, min_sog)
    return build_causal_grid_states(
        states,
        time_bin_seconds,
        time_bin_seconds if max_state_age_s is None else max_state_age_s,
    )


OPPORTUNITY_RECORD_FIELDS = [
    "date",
    "time_bin",
    "reference_time",
    "source_timestamp_a",
    "source_timestamp_b",
    "state_age_s_a",
    "state_age_s_b",
    "source_state_skew_s",
    "mmsi_a",
    "mmsi_b",
    "lon_a",
    "lat_a",
    "lon_b",
    "lat_b",
    "sog_a",
    "sog_b",
    "ground_track_course_a",
    "ground_track_course_b",
    "direction_source_a",
    "direction_source_b",
    "track_id_a",
    "track_id_b",
    "cell_id",
    "current_distance_nm",
    "dcpa_nm",
    "tcpa_min",
    "relative_speed_kn",
    "closing_speed_kn",
    "is_candidate",
    "encounter_risk_score",
]


def compute_encounters(
    start: dt.date,
    end: dt.date,
    processed_dir: Path,
    output_csv: Path,
    output_geojson: Path,
    stats_json: Path,
    time_bin_seconds: int,
    spatial_cell_deg: float,
    min_sog: float,
    max_current_distance_nm: float,
    dcpa_threshold_nm: float,
    tcpa_threshold_min: float,
    geojson_limit: int,
    dataset_prefix: str = "sf_bay_ais",
    max_state_skew_s: int = 60,
    opportunity_csv: Path | None = None,
    analysis_bbox: tuple[float, float, float, float] | None = None,
    analysis_cell_size_deg: float = 0.005,
    opportunity_records_csv: Path | None = None,
) -> dict[str, object]:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_geojson.parent.mkdir(parents=True, exist_ok=True)
    stats_json.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "date",
        "time_bin",
        "reference_time",
        "timestamp_a",
        "timestamp_b",
        "state_skew_s",
        "state_age_s_a",
        "state_age_s_b",
        "mmsi_a",
        "mmsi_b",
        "lon_a",
        "lat_a",
        "lon_b",
        "lat_b",
        "sog_a",
        "sog_b",
        "bearing_a",
        "bearing_b",
        "direction_source_a",
        "direction_source_b",
        "dcpa_nm",
        "tcpa_min",
        "current_distance_nm",
        "encounter_risk_score",
        "track_id_a",
        "track_id_b",
    ]

    encounter_rows: list[dict[str, object]] = []
    pair_checks = 0
    neighbor_pair_candidates = 0
    pairs_rejected_state_skew = 0
    bins_processed = 0
    vessel_states = 0
    synchronized_grid_vessel_states = 0
    accepted_skews: list[float] = []
    opportunities: dict[tuple[str, str], dict[str, object]] = {}
    opportunity_record_count = 0
    densest_grid_states: list[tuple[int, dt.datetime, list[dict[str, object]]]] = []

    if opportunity_csv is not None and analysis_bbox is None:
        raise ValueError("analysis_bbox is required when opportunity_csv is provided")
    if opportunity_records_csv is not None:
        opportunity_records_csv.parent.mkdir(parents=True, exist_ok=True)
        opportunity_records_context = opportunity_records_csv.open("w", encoding="utf-8", newline="")
    else:
        opportunity_records_context = contextlib.nullcontext(None)

    day_list = iter_dates(start, end)
    first_day_start = dt.datetime.combine(start, dt.time.min)
    carry_states: list[dict[str, object]] = []
    previous_path = processed_dir / f"{dataset_prefix}_{(start - dt.timedelta(days=1)).isoformat()}_features.csv"
    if previous_path.exists() and max_state_skew_s > 0:
        previous_states = load_feature_states(previous_path, min_sog)
        carry_cutoff = first_day_start - dt.timedelta(seconds=max_state_skew_s)
        carry_states = [state for state in previous_states if state_timestamp(state) >= carry_cutoff]

    with opportunity_records_context as opportunity_records_target:
        opportunity_records_writer = None
        if opportunity_records_target is not None:
            opportunity_records_writer = csv.DictWriter(
                opportunity_records_target,
                fieldnames=OPPORTUNITY_RECORD_FIELDS,
            )
            opportunity_records_writer.writeheader()

        for day in day_list:
            day_start = dt.datetime.combine(day, dt.time.min)
            day_end = day_start + dt.timedelta(days=1)
            path = processed_dir / f"{dataset_prefix}_{day.isoformat()}_features.csv"
            day_states = load_feature_states(path, min_sog)
            vessel_states += len(day_states)
            bins = build_causal_grid_states(
                carry_states + day_states,
                time_bin_seconds=time_bin_seconds,
                max_state_age_s=max_state_skew_s,
            )
            for time_bin, vessels in sorted(bins.items()):
                if not day_start <= time_bin < day_end:
                    continue
                bins_processed += 1
                states = list(vessels.values())
                synchronized_grid_vessel_states += len(states)
                densest_grid_states.append((len(states), time_bin, states))
                densest_grid_states.sort(key=lambda item: (item[0], item[1]), reverse=True)
                if len(densest_grid_states) > 12:
                    densest_grid_states.pop()
                within_distance_pairs, index_candidates = _indexed_pairs_within_distance(
                    states,
                    max_distance_nm=max_current_distance_nm,
                    spatial_cell_deg=spatial_cell_deg,
                )
                neighbor_pair_candidates += index_candidates
                state_by_mmsi = {str(state["mmsi"]): state for state in states}
                reference_time = time_bin

                for pair_key in sorted(within_distance_pairs):
                    aligned_a = state_by_mmsi[pair_key[0]]
                    aligned_b = state_by_mmsi[pair_key[1]]
                    source_timestamp_a = state_timestamp({"timestamp": aligned_a["source_timestamp"]})
                    source_timestamp_b = state_timestamp({"timestamp": aligned_b["source_timestamp"]})
                    state_skew_s = abs((source_timestamp_a - source_timestamp_b).total_seconds())
                    state_age_s_a = float(aligned_a["state_age_s"])
                    state_age_s_b = float(aligned_b["state_age_s"])
                    pair_checks += 1
                    opportunity_record_count += 1
                    accepted_skews.append(state_skew_s)

                    lon_mid = (float(aligned_a["lon"]) + float(aligned_b["lon"])) / 2
                    lat_mid = (float(aligned_a["lat"]) + float(aligned_b["lat"])) / 2
                    opportunity_cell = (
                        analysis_cell_for_point(
                            lon_mid,
                            lat_mid,
                            analysis_bbox,
                            analysis_cell_size_deg,
                        )
                        if analysis_bbox is not None
                        else None
                    )
                    if opportunity_cell is not None:
                        opportunity = opportunities.setdefault(
                            (reference_time.date().isoformat(), opportunity_cell),
                            {
                                "pair_opportunity_count": 0,
                                "state_skew_sum_s": 0.0,
                                "max_state_skew_s": 0.0,
                                "pairs": set(),
                            },
                        )
                        opportunity["pair_opportunity_count"] = int(opportunity["pair_opportunity_count"]) + 1
                        opportunity["state_skew_sum_s"] = float(opportunity["state_skew_sum_s"]) + state_skew_s
                        opportunity["max_state_skew_s"] = max(
                            float(opportunity["max_state_skew_s"]),
                            state_skew_s,
                        )
                        opportunity["pairs"].add(pair_key)  # type: ignore[union-attr]

                    dcpa, tcpa_min, current_distance = cpa_tcpa(aligned_a, aligned_b)
                    relative_speed, closing_speed = relative_motion_metrics(aligned_a, aligned_b)
                    is_candidate = bool(
                        0 < tcpa_min <= tcpa_threshold_min
                        and dcpa <= dcpa_threshold_nm
                    )
                    risk_score = (
                        (1 - dcpa / dcpa_threshold_nm) * 0.65
                        + (1 - tcpa_min / tcpa_threshold_min) * 0.35
                        if is_candidate
                        else None
                    )

                    if opportunity_records_writer is not None:
                        opportunity_records_writer.writerow(
                            {
                                "date": reference_time.date().isoformat(),
                                "time_bin": reference_time.strftime("%Y-%m-%d %H:%M:%S"),
                                "reference_time": reference_time.strftime("%Y-%m-%d %H:%M:%S"),
                                "source_timestamp_a": source_timestamp_a.strftime("%Y-%m-%d %H:%M:%S"),
                                "source_timestamp_b": source_timestamp_b.strftime("%Y-%m-%d %H:%M:%S"),
                                "state_age_s_a": round(state_age_s_a, 3),
                                "state_age_s_b": round(state_age_s_b, 3),
                                "source_state_skew_s": round(state_skew_s, 3),
                                "mmsi_a": aligned_a["mmsi"],
                                "mmsi_b": aligned_b["mmsi"],
                                "lon_a": round(float(aligned_a["lon"]), 6),
                                "lat_a": round(float(aligned_a["lat"]), 6),
                                "lon_b": round(float(aligned_b["lon"]), 6),
                                "lat_b": round(float(aligned_b["lat"]), 6),
                                "sog_a": round(float(aligned_a["sog"]), 3),
                                "sog_b": round(float(aligned_b["sog"]), 3),
                                "ground_track_course_a": round(float(aligned_a["bearing"]), 3),
                                "ground_track_course_b": round(float(aligned_b["bearing"]), 3),
                                "direction_source_a": aligned_a.get("direction_source", ""),
                                "direction_source_b": aligned_b.get("direction_source", ""),
                                "track_id_a": aligned_a.get("track_id", ""),
                                "track_id_b": aligned_b.get("track_id", ""),
                                "cell_id": opportunity_cell or "",
                                "current_distance_nm": round(current_distance, 6),
                                "dcpa_nm": finite_or_blank(dcpa),
                                "tcpa_min": finite_or_blank(tcpa_min),
                                "relative_speed_kn": round(relative_speed, 6),
                                "closing_speed_kn": round(closing_speed, 6),
                                "is_candidate": int(is_candidate),
                                "encounter_risk_score": (
                                    round(max(0.0, min(1.0, risk_score)), 6)
                                    if risk_score is not None
                                    else ""
                                ),
                            }
                        )

                    if not is_candidate or risk_score is None:
                        continue
                    encounter_rows.append(
                        {
                            "date": reference_time.date().isoformat(),
                            "time_bin": reference_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "reference_time": reference_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "timestamp_a": source_timestamp_a.strftime("%Y-%m-%d %H:%M:%S"),
                            "timestamp_b": source_timestamp_b.strftime("%Y-%m-%d %H:%M:%S"),
                            "state_skew_s": round(state_skew_s, 3),
                            "state_age_s_a": round(state_age_s_a, 3),
                            "state_age_s_b": round(state_age_s_b, 3),
                            "mmsi_a": aligned_a["mmsi"],
                            "mmsi_b": aligned_b["mmsi"],
                            "lon_a": round(float(aligned_a["lon"]), 6),
                            "lat_a": round(float(aligned_a["lat"]), 6),
                            "lon_b": round(float(aligned_b["lon"]), 6),
                            "lat_b": round(float(aligned_b["lat"]), 6),
                            "sog_a": round(float(aligned_a["sog"]), 3),
                            "sog_b": round(float(aligned_b["sog"]), 3),
                            "bearing_a": round(float(aligned_a["bearing"]), 3),
                            "bearing_b": round(float(aligned_b["bearing"]), 3),
                            "direction_source_a": aligned_a.get("direction_source", ""),
                            "direction_source_b": aligned_b.get("direction_source", ""),
                            "dcpa_nm": round(dcpa, 6),
                            "tcpa_min": round(tcpa_min, 6),
                            "current_distance_nm": round(current_distance, 6),
                            "encounter_risk_score": round(max(0.0, min(1.0, risk_score)), 6),
                            "track_id_a": aligned_a.get("track_id", ""),
                            "track_id_b": aligned_b.get("track_id", ""),
                        }
                    )

            carry_cutoff = day_end - dt.timedelta(seconds=max_state_skew_s)
            carry_states = [state for state in day_states if state_timestamp(state) >= carry_cutoff]

    encounter_rows.sort(key=lambda item: float(item["encounter_risk_score"]), reverse=True)
    with output_csv.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(encounter_rows)

    opportunity_rows: list[dict[str, object]] = []
    for (date_key, cell_id), record in sorted(opportunities.items()):
        count = int(record["pair_opportunity_count"])
        opportunity_rows.append(
            {
                "date": date_key,
                "cell_id": cell_id,
                "pair_opportunity_count": count,
                "unique_vessel_pairs": len(record["pairs"]),  # type: ignore[arg-type]
                "mean_state_skew_s": round(float(record["state_skew_sum_s"]) / count, 6) if count else 0.0,
                "max_state_skew_s": round(float(record["max_state_skew_s"]), 6),
            }
        )
    if opportunity_csv is not None:
        opportunity_csv.parent.mkdir(parents=True, exist_ok=True)
        with opportunity_csv.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(
                target,
                fieldnames=[
                    "date",
                    "cell_id",
                    "pair_opportunity_count",
                    "unique_vessel_pairs",
                    "mean_state_skew_s",
                    "max_state_skew_s",
                ],
            )
            writer.writeheader()
            writer.writerows(opportunity_rows)

    features = []
    for row in encounter_rows[:geojson_limit]:
        lon_mid = (float(row["lon_a"]) + float(row["lon_b"])) / 2
        lat_mid = (float(row["lat_a"]) + float(row["lat_b"])) / 2
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon_mid, lat_mid]},
                "properties": row,
            }
        )
    output_geojson.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": features,
                "metadata": {
                    "source_count": len(encounter_rows),
                    "published_count": len(features),
                    "geojson_limit": geojson_limit,
                    "selection_rule": "highest encounter screening score; stable source order for ties",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    spatial_audit_details: list[dict[str, object]] = []
    for state_count, reference_time, states in sorted(densest_grid_states, key=lambda item: item[1]):
        indexed_pairs = indexed_pairs_within_distance(
            states,
            max_distance_nm=max_current_distance_nm,
            spatial_cell_deg=spatial_cell_deg,
        )
        brute_force_pairs = brute_force_pairs_within_distance(states, max_current_distance_nm)
        missing_pairs = brute_force_pairs - indexed_pairs
        extra_pairs = indexed_pairs - brute_force_pairs
        spatial_audit_details.append(
            {
                "reference_time": reference_time.strftime("%Y-%m-%d %H:%M:%S"),
                "states": state_count,
                "indexed_pairs": len(indexed_pairs),
                "brute_force_pairs": len(brute_force_pairs),
                "missing_pairs": len(missing_pairs),
                "extra_pairs": len(extra_pairs),
                "exact_match": not missing_pairs and not extra_pairs,
            }
        )
    spatial_index_completeness_audit = {
        "selection": "12_densest_synchronized_grid_bins",
        "bins": len(spatial_audit_details),
        "states": sum(int(row["states"]) for row in spatial_audit_details),
        "indexed_pairs": sum(int(row["indexed_pairs"]) for row in spatial_audit_details),
        "brute_force_pairs": sum(int(row["brute_force_pairs"]) for row in spatial_audit_details),
        "missing_pairs": sum(int(row["missing_pairs"]) for row in spatial_audit_details),
        "extra_pairs": sum(int(row["extra_pairs"]) for row in spatial_audit_details),
        "exact_match_all": all(bool(row["exact_match"]) for row in spatial_audit_details),
        "bin_details": spatial_audit_details,
    }

    deduplicated = summarize_deduplicated_events(encounter_rows, episode_gap_min=tcpa_threshold_min)
    stats: dict[str, object] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "time_bin_seconds": time_bin_seconds,
        "state_alignment": "epoch_aligned_causal_grid_latest_valid_state_propagated_forward",
        "causal_grid": True,
        "max_state_age_s": max_state_skew_s,
        "max_state_skew_s": max_state_skew_s,
        "spatial_index": "dynamic_lon_lat_bucket_radius_with_exact_distance_filter",
        "spatial_index_completeness_audit": spatial_index_completeness_audit,
        "spatial_cell_deg": spatial_cell_deg,
        "min_sog": min_sog,
        "max_current_distance_nm": max_current_distance_nm,
        "dcpa_threshold_nm": dcpa_threshold_nm,
        "tcpa_threshold_min": tcpa_threshold_min,
        "tcpa_condition": f"0 < TCPA <= {tcpa_threshold_min} min",
        "dataset_prefix": dataset_prefix,
        "bins_processed": bins_processed,
        "vessel_states": vessel_states,
        "vessel_states_definition": "usable causal source states loaded from feature files",
        "synchronized_grid_vessel_states": synchronized_grid_vessel_states,
        "neighbor_pair_candidates": neighbor_pair_candidates,
        "pairs_rejected_state_skew": pairs_rejected_state_skew,
        "pair_checks_within_distance": pair_checks,
        "pair_opportunity_cells": len({row["cell_id"] for row in opportunity_rows}),
        "pair_opportunity_cell_days": len(opportunity_rows),
        "opportunity_csv": str(opportunity_csv) if opportunity_csv else None,
        "opportunity_records_csv": str(opportunity_records_csv) if opportunity_records_csv else None,
        "pair_opportunity_records": opportunity_record_count,
        "accepted_state_skew_s": {
            "median": round(statistics.median(accepted_skews), 6) if accepted_skews else None,
            "p95": round(percentile(accepted_skews, 0.95), 6) if accepted_skews else None,
            "max": round(max(accepted_skews), 6) if accepted_skews else None,
        },
        "encounters": len(encounter_rows),
        **deduplicated,
        "geojson_features": len(features),
        "top_encounters": encounter_rows[:20],
    }
    stats_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute approximate CPA/TCPA encounter risk.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"), help="Processed data directory.")
    parser.add_argument("--output-csv", type=Path, required=True, help="Output encounters CSV.")
    parser.add_argument("--output-geojson", type=Path, required=True, help="Output encounters GeoJSON.")
    parser.add_argument("--stats-json", type=Path, required=True, help="Output stats JSON.")
    parser.add_argument("--time-bin-seconds", type=int, default=60, help="Time bin size in seconds.")
    parser.add_argument(
        "--max-state-skew-s",
        type=int,
        default=60,
        help="Maximum causal source-state age at a common grid time (legacy option name).",
    )
    parser.add_argument("--spatial-cell-deg", type=float, default=0.02, help="Spatial candidate bucket size.")
    parser.add_argument("--min-sog", type=float, default=1.0, help="Minimum SOG for encounter analysis.")
    parser.add_argument("--max-current-distance-nm", type=float, default=2.0, help="Candidate current distance threshold.")
    parser.add_argument("--dcpa-threshold-nm", type=float, default=0.5, help="DCPA threshold.")
    parser.add_argument("--tcpa-threshold-min", type=float, default=15.0, help="TCPA threshold.")
    parser.add_argument("--geojson-limit", type=int, default=2000, help="Maximum GeoJSON features.")
    parser.add_argument("--dataset-prefix", default="sf_bay_ais", help="Daily feature-file prefix.")
    parser.add_argument("--opportunity-csv", type=Path, help="Optional cell-level pair-opportunity exposure CSV.")
    parser.add_argument(
        "--opportunity-records-csv",
        type=Path,
        help="Optional private audit CSV containing every synchronized pair opportunity within the distance limit.",
    )
    parser.add_argument(
        "--analysis-bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        help="Analysis bounding box used for opportunity-cell aggregation.",
    )
    parser.add_argument("--analysis-cell-size-deg", type=float, default=0.005)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = compute_encounters(
        start=args.start,
        end=args.end,
        processed_dir=args.processed_dir,
        output_csv=args.output_csv,
        output_geojson=args.output_geojson,
        stats_json=args.stats_json,
        time_bin_seconds=args.time_bin_seconds,
        spatial_cell_deg=args.spatial_cell_deg,
        min_sog=args.min_sog,
        max_current_distance_nm=args.max_current_distance_nm,
        dcpa_threshold_nm=args.dcpa_threshold_nm,
        tcpa_threshold_min=args.tcpa_threshold_min,
        geojson_limit=args.geojson_limit,
        dataset_prefix=args.dataset_prefix,
        max_state_skew_s=args.max_state_skew_s,
        opportunity_csv=args.opportunity_csv,
        opportunity_records_csv=args.opportunity_records_csv,
        analysis_bbox=tuple(args.analysis_bbox) if args.analysis_bbox else None,
        analysis_cell_size_deg=args.analysis_cell_size_deg,
    )
    print(
        json.dumps(
            {
                "bins_processed": stats["bins_processed"],
                "vessel_states": stats["vessel_states"],
                "encounters": stats["encounters"],
                "deduplicated_encounter_episodes": stats["deduplicated_encounter_episodes"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
