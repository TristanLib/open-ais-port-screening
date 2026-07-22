#!/usr/bin/env python3
"""Approximate CPA/TCPA encounter-candidate extraction from AIS feature data."""

from __future__ import annotations

import argparse
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
    epoch = int(timestamp.timestamp())
    return dt.datetime.fromtimestamp(epoch - epoch % seconds)


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


def spatial_bucket(lon: float, lat: float, cell_size_deg: float) -> tuple[int, int]:
    return int(lon / cell_size_deg), int(lat / cell_size_deg)


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


def neighbor_buckets(bucket: tuple[int, int]) -> list[tuple[int, int]]:
    x, y = bucket
    return [(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]


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


def load_day_states(
    feature_path: Path,
    time_bin_seconds: int,
    min_sog: float,
) -> dict[dt.datetime, dict[str, dict[str, object]]]:
    bins: dict[dt.datetime, dict[str, dict[str, object]]] = {}
    with feature_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            sog = parse_float(row.get("sog"))
            if sog is None or sog < min_sog:
                continue
            lon = parse_float(row.get("longitude"))
            lat = parse_float(row.get("latitude"))
            bearing = parse_float(row.get("bearing_deg"))
            if bearing is None:
                bearing = parse_float(row.get("cog"))
            if lon is None or lat is None or bearing is None:
                continue
            timestamp = parse_time(row["base_date_time"])
            time_bin = floor_time(timestamp, time_bin_seconds)
            mmsi = row.get("mmsi", "")
            if not mmsi:
                continue
            # Keep the latest position per vessel per time bin.
            current = bins.setdefault(time_bin, {}).get(mmsi)
            if current is None or str(current["timestamp"]) < row["base_date_time"]:
                bins[time_bin][mmsi] = {
                    "mmsi": mmsi,
                    "timestamp": row["base_date_time"],
                    "lon": lon,
                    "lat": lat,
                    "sog": sog,
                    "bearing": bearing,
                    "track_id": row.get("track_id", ""),
                    "vessel_type": row.get("vessel_type", ""),
                    "length": row.get("length", ""),
                }
    return bins


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
    accepted_skews: list[float] = []
    opportunities: dict[tuple[str, str], dict[str, object]] = {}

    if opportunity_csv is not None and analysis_bbox is None:
        raise ValueError("analysis_bbox is required when opportunity_csv is provided")

    for day in iter_dates(start, end):
        path = processed_dir / f"{dataset_prefix}_{day.isoformat()}_features.csv"
        bins = load_day_states(path, time_bin_seconds, min_sog)
        for time_bin, vessels in bins.items():
            bins_processed += 1
            states = list(vessels.values())
            vessel_states += len(states)
            by_bucket: dict[tuple[int, int], list[int]] = {}
            for index, state in enumerate(states):
                bucket = spatial_bucket(float(state["lon"]), float(state["lat"]), spatial_cell_deg)
                by_bucket.setdefault(bucket, []).append(index)

            seen_pairs: set[tuple[str, str]] = set()
            for bucket, indices in by_bucket.items():
                candidate_indices = []
                for neighbor in neighbor_buckets(bucket):
                    candidate_indices.extend(by_bucket.get(neighbor, []))
                for i in indices:
                    a = states[i]
                    for j in candidate_indices:
                        if j <= i:
                            continue
                        b = states[j]
                        pair_key = tuple(sorted([str(a["mmsi"]), str(b["mmsi"])]))
                        if pair_key in seen_pairs:
                            continue
                        seen_pairs.add(pair_key)
                        neighbor_pair_candidates += 1
                        synchronized = synchronize_pair_states(a, b, max_state_skew_s)
                        if synchronized is None:
                            pairs_rejected_state_skew += 1
                            continue
                        aligned_a, aligned_b, reference_time, state_skew_s = synchronized
                        current_distance = distance_nm(
                            float(aligned_a["lon"]),
                            float(aligned_a["lat"]),
                            float(aligned_b["lon"]),
                            float(aligned_b["lat"]),
                        )
                        if current_distance > max_current_distance_nm:
                            continue
                        pair_checks += 1
                        accepted_skews.append(state_skew_s)

                        if analysis_bbox is not None:
                            lon_mid = (float(aligned_a["lon"]) + float(aligned_b["lon"])) / 2
                            lat_mid = (float(aligned_a["lat"]) + float(aligned_b["lat"])) / 2
                            opportunity_cell = analysis_cell_for_point(
                                lon_mid,
                                lat_mid,
                                analysis_bbox,
                                analysis_cell_size_deg,
                            )
                            if opportunity_cell is not None:
                                opportunity = opportunities.setdefault(
                                    (day.isoformat(), opportunity_cell),
                                    {
                                        "pair_opportunity_count": 0,
                                        "state_skew_sum_s": 0.0,
                                        "max_state_skew_s": 0.0,
                                        "pairs": set(),
                                    },
                                )
                                opportunity["pair_opportunity_count"] = int(
                                    opportunity["pair_opportunity_count"]
                                ) + 1
                                opportunity["state_skew_sum_s"] = float(opportunity["state_skew_sum_s"]) + state_skew_s
                                opportunity["max_state_skew_s"] = max(
                                    float(opportunity["max_state_skew_s"]), state_skew_s
                                )
                                opportunity["pairs"].add(pair_key)  # type: ignore[union-attr]

                        dcpa, tcpa_min, current_distance = cpa_tcpa(aligned_a, aligned_b)
                        if tcpa_min <= 0 or dcpa > dcpa_threshold_nm or tcpa_min > tcpa_threshold_min:
                            continue
                        risk_score = (1 - dcpa / dcpa_threshold_nm) * 0.65 + (1 - tcpa_min / tcpa_threshold_min) * 0.35
                        encounter_rows.append(
                            {
                                "date": day.isoformat(),
                                "time_bin": time_bin.strftime("%Y-%m-%d %H:%M:%S"),
                                "reference_time": reference_time.strftime("%Y-%m-%d %H:%M:%S"),
                                "timestamp_a": state_timestamp(a).strftime("%Y-%m-%d %H:%M:%S"),
                                "timestamp_b": state_timestamp(b).strftime("%Y-%m-%d %H:%M:%S"),
                                "state_skew_s": round(state_skew_s, 3),
                                "mmsi_a": a["mmsi"],
                                "mmsi_b": b["mmsi"],
                                "lon_a": round(float(aligned_a["lon"]), 6),
                                "lat_a": round(float(aligned_a["lat"]), 6),
                                "lon_b": round(float(aligned_b["lon"]), 6),
                                "lat_b": round(float(aligned_b["lat"]), 6),
                                "sog_a": round(float(a["sog"]), 3),
                                "sog_b": round(float(b["sog"]), 3),
                                "bearing_a": round(float(a["bearing"]), 3),
                                "bearing_b": round(float(b["bearing"]), 3),
                                "dcpa_nm": round(dcpa, 6),
                                "tcpa_min": round(tcpa_min, 6),
                                "current_distance_nm": round(current_distance, 6),
                                "encounter_risk_score": round(max(0.0, min(1.0, risk_score)), 6),
                                "track_id_a": a["track_id"],
                                "track_id_b": b["track_id"],
                            }
                        )

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
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    deduplicated = summarize_deduplicated_events(encounter_rows, episode_gap_min=tcpa_threshold_min)
    stats: dict[str, object] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "time_bin_seconds": time_bin_seconds,
        "state_alignment": "propagate_earlier_state_to_later_observation_time",
        "max_state_skew_s": max_state_skew_s,
        "spatial_cell_deg": spatial_cell_deg,
        "min_sog": min_sog,
        "max_current_distance_nm": max_current_distance_nm,
        "dcpa_threshold_nm": dcpa_threshold_nm,
        "tcpa_threshold_min": tcpa_threshold_min,
        "tcpa_condition": f"0 < TCPA <= {tcpa_threshold_min} min",
        "dataset_prefix": dataset_prefix,
        "bins_processed": bins_processed,
        "vessel_states": vessel_states,
        "neighbor_pair_candidates": neighbor_pair_candidates,
        "pairs_rejected_state_skew": pairs_rejected_state_skew,
        "pair_checks_within_distance": pair_checks,
        "pair_opportunity_cells": len({row["cell_id"] for row in opportunity_rows}),
        "pair_opportunity_cell_days": len(opportunity_rows),
        "opportunity_csv": str(opportunity_csv) if opportunity_csv else None,
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
    parser.add_argument("--max-state-skew-s", type=int, default=60, help="Maximum pair-state timestamp skew.")
    parser.add_argument("--spatial-cell-deg", type=float, default=0.02, help="Spatial candidate bucket size.")
    parser.add_argument("--min-sog", type=float, default=1.0, help="Minimum SOG for encounter analysis.")
    parser.add_argument("--max-current-distance-nm", type=float, default=2.0, help="Candidate current distance threshold.")
    parser.add_argument("--dcpa-threshold-nm", type=float, default=0.5, help="DCPA threshold.")
    parser.add_argument("--tcpa-threshold-min", type=float, default=15.0, help="TCPA threshold.")
    parser.add_argument("--geojson-limit", type=int, default=2000, help="Maximum GeoJSON features.")
    parser.add_argument("--dataset-prefix", default="sf_bay_ais", help="Daily feature-file prefix.")
    parser.add_argument("--opportunity-csv", type=Path, help="Optional cell-level pair-opportunity exposure CSV.")
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
