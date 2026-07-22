#!/usr/bin/env python3
"""Create and evaluate synthetic AIS anomalies against the current scorer."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from anomaly_score import cell_for, load_patterns, parse_float, score_row


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def cell_id_from_lonlat(
    lon: float,
    lat: float,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> str | None:
    cell = cell_for(lon, lat, bbox, cell_size_deg)
    if cell is None:
        return None
    col, row = cell
    return f"r{row}_c{col}"


def reservoir_add(pool: list[dict[str, str]], row: dict[str, str], limit: int, seen: int, rng: random.Random) -> None:
    if len(pool) < limit:
        pool.append(dict(row))
        return
    index = rng.randint(0, seen - 1)
    if index < limit:
        pool[index] = dict(row)


def collect_candidate_pools(
    start: dt.date,
    end: dt.date,
    processed_dir: Path,
    patterns: dict[str, dict[str, object]],
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    moving_sog: float,
    min_score: float,
    pool_limit: int,
    seed: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rng = random.Random(seed)
    normal_pool: list[dict[str, str]] = []
    high_conf_pool: list[dict[str, str]] = []
    normal_seen = 0
    high_conf_seen = 0

    for day in iter_dates(start, end):
        path = processed_dir / f"sf_bay_ais_{day.isoformat()}_features.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                lon = parse_float(row.get("longitude"))
                lat = parse_float(row.get("latitude"))
                sog = parse_float(row.get("sog"))
                bearing = parse_float(row.get("bearing_deg")) or parse_float(row.get("cog"))
                if lon is None or lat is None or sog is None or bearing is None or sog < moving_sog:
                    continue
                cell_id = cell_id_from_lonlat(lon, lat, bbox, cell_size_deg)
                if not cell_id or cell_id not in patterns:
                    continue
                baseline = score_row(row, patterns, bbox, cell_size_deg, moving_sog)
                if baseline is None or float(baseline["anomaly_score"]) >= min_score:
                    continue
                normal_seen += 1
                reservoir_add(normal_pool, row, pool_limit, normal_seen, rng)
                if bool(patterns[cell_id].get("is_high_confidence_route_cell")):
                    high_conf_seen += 1
                    reservoir_add(high_conf_pool, row, pool_limit, high_conf_seen, rng)

    return normal_pool, high_conf_pool


def shifted_outside_pattern(
    row: dict[str, str],
    patterns: dict[str, dict[str, object]],
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> tuple[float, float] | None:
    lon = parse_float(row.get("longitude"))
    lat = parse_float(row.get("latitude"))
    if lon is None or lat is None:
        return None
    min_lon, min_lat, max_lon, max_lat = bbox
    offsets = [
        (0.035, 0.0),
        (-0.035, 0.0),
        (0.0, 0.035),
        (0.0, -0.035),
        (0.055, 0.025),
        (-0.055, 0.025),
        (0.025, -0.055),
        (-0.025, -0.055),
        (0.085, 0.045),
        (-0.085, -0.045),
    ]
    for dlon, dlat in offsets:
        new_lon = lon + dlon
        new_lat = lat + dlat
        if not (min_lon <= new_lon <= max_lon and min_lat <= new_lat <= max_lat):
            continue
        cell_id = cell_id_from_lonlat(new_lon, new_lat, bbox, cell_size_deg)
        if cell_id and cell_id not in patterns:
            return new_lon, new_lat
    return None


def opposite_bearing(row: dict[str, str], patterns: dict[str, dict[str, object]], bbox: tuple[float, float, float, float], cell_size_deg: float) -> float:
    lon = parse_float(row.get("longitude"))
    lat = parse_float(row.get("latitude"))
    if lon is None or lat is None:
        return 180.0
    cell_id = cell_id_from_lonlat(lon, lat, bbox, cell_size_deg)
    pattern = patterns.get(cell_id or "")
    if pattern:
        dominant_bin = int(pattern["dominant_direction_bin"])
        return (dominant_bin * 45 + 180) % 360
    bearing = parse_float(row.get("bearing_deg")) or parse_float(row.get("cog")) or 0.0
    return (bearing + 180) % 360


def mutate_row(
    row: dict[str, str],
    injection_type: str,
    patterns: dict[str, dict[str, object]],
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> dict[str, str] | None:
    mutated = dict(row)
    if injection_type == "normal_control":
        return mutated

    if injection_type in {"speed_surge", "route_deviation"}:
        shifted = shifted_outside_pattern(row, patterns, bbox, cell_size_deg)
        if shifted is None:
            return None
        mutated["longitude"] = f"{shifted[0]:.6f}"
        mutated["latitude"] = f"{shifted[1]:.6f}"

    if injection_type == "speed_surge":
        mutated["sog"] = "22.0"
        mutated["implied_sog_kn"] = "40.0"
        mutated["accel_kn_per_min"] = "5.0"
        return mutated

    if injection_type == "route_deviation":
        mutated["sog"] = str(max(parse_float(row.get("sog")) or 4.0, 4.0))
        return mutated

    if injection_type == "course_reversal":
        mutated["bearing_deg"] = f"{opposite_bearing(row, patterns, bbox, cell_size_deg):.3f}"
        mutated["turn_rate_deg_per_min"] = "45.0"
        mutated["sog"] = str(max(parse_float(row.get("sog")) or 3.0, 3.0))
        return mutated

    if injection_type == "unexpected_stop":
        mutated["sog"] = "0.1"
        mutated["accel_kn_per_min"] = "-4.0"
        mutated["turn_rate_deg_per_min"] = "45.0"
        return mutated

    raise ValueError(f"Unknown injection type: {injection_type}")


def evaluate_injections(
    normal_pool: list[dict[str, str]],
    high_conf_pool: list[dict[str, str]],
    patterns: dict[str, dict[str, object]],
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
    moving_sog: float,
    min_score: float,
    samples_per_type: int,
    seed: int,
) -> tuple[list[dict[str, object]], dict[str, Any]]:
    rng = random.Random(seed)
    injection_specs = [
        ("normal_control", normal_pool),
        ("speed_surge", normal_pool),
        ("course_reversal", normal_pool),
        ("route_deviation", normal_pool),
        ("unexpected_stop", high_conf_pool or normal_pool),
    ]
    rows: list[dict[str, object]] = []
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    score_sums: Counter[str] = Counter()
    reason_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for injection_type, pool in injection_specs:
        if not pool:
            continue
        selected = rng.sample(pool, min(samples_per_type, len(pool)))
        for source_index, source in enumerate(selected):
            mutated = mutate_row(source, injection_type, patterns, bbox, cell_size_deg)
            if mutated is None:
                counters[injection_type]["skipped_no_valid_mutation"] += 1
                continue
            scored = score_row(mutated, patterns, bbox, cell_size_deg, moving_sog)
            if scored is None:
                counters[injection_type]["skipped_unscorable"] += 1
                continue
            score = float(scored["anomaly_score"])
            detected = int(score >= min_score)
            counters[injection_type]["evaluated"] += 1
            counters[injection_type]["detected"] += detected
            score_sums[injection_type] += score
            for reason in str(scored.get("reasons", "")).split(";"):
                if reason:
                    reason_counts[injection_type][reason] += 1
            rows.append(
                {
                    "injection_id": f"{injection_type}_{source_index:04d}",
                    "injection_type": injection_type,
                    "source_mmsi": source.get("mmsi", ""),
                    "source_track_id": source.get("track_id", ""),
                    "source_time": source.get("base_date_time", ""),
                    "longitude": scored["longitude"],
                    "latitude": scored["latitude"],
                    "sog": scored["sog"],
                    "anomaly_score": scored["anomaly_score"],
                    "detected": detected,
                    "reasons": scored["reasons"],
                }
            )

    stats_rows = []
    for injection_type in [item[0] for item in injection_specs]:
        evaluated = counters[injection_type]["evaluated"]
        detected = counters[injection_type]["detected"]
        rate_name = "false_positive_rate" if injection_type == "normal_control" else "recall"
        rate = detected / evaluated if evaluated else 0.0
        stats_rows.append(
            {
                "injection_type": injection_type,
                "evaluated": evaluated,
                "detected": detected,
                rate_name: round(rate, 6),
                "mean_score": round(score_sums[injection_type] / evaluated, 6) if evaluated else 0.0,
                "top_reasons": reason_counts[injection_type].most_common(5),
                "skipped_no_valid_mutation": counters[injection_type]["skipped_no_valid_mutation"],
                "skipped_unscorable": counters[injection_type]["skipped_unscorable"],
            }
        )

    stats = {
        "samples_per_type": samples_per_type,
        "min_score": min_score,
        "seed": seed,
        "normal_pool_size": len(normal_pool),
        "high_confidence_pool_size": len(high_conf_pool),
        "by_type": stats_rows,
    }
    return rows, stats


def write_stats_markdown(path: Path, stats: dict[str, Any]) -> None:
    lines = [
        "# Synthetic Anomaly Injection Validation",
        "",
        f"- Samples per type: {stats['samples_per_type']}",
        f"- Detection threshold: {stats['min_score']}",
        f"- Normal candidate pool: {stats['normal_pool_size']}",
        f"- High-confidence candidate pool: {stats['high_confidence_pool_size']}",
        "",
        "| Type | Evaluated | Detected | Rate | Mean score | Top reasons |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in stats["by_type"]:
        rate = item.get("false_positive_rate", item.get("recall", 0.0))
        reasons = ", ".join(f"{reason}:{count}" for reason, count in item["top_reasons"])
        lines.append(
            f"| {item['injection_type']} | {item['evaluated']} | {item['detected']} | "
            f"{rate:.4f} | {item['mean_score']:.4f} | {reasons} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate synthetic AIS anomaly injections.")
    parser.add_argument("--start", type=parse_date, required=True)
    parser.add_argument("--end", type=parse_date, required=True)
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--patterns-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--stats-json", type=Path, required=True)
    parser.add_argument("--stats-md", type=Path, required=True)
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"), required=True)
    parser.add_argument("--cell-size-deg", type=float, default=0.005)
    parser.add_argument("--moving-sog", type=float, default=1.0)
    parser.add_argument("--min-score", type=float, default=0.35)
    parser.add_argument("--samples-per-type", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20260527)
    args = parser.parse_args()

    bbox = tuple(args.bbox)  # type: ignore[assignment]
    patterns = load_patterns(args.patterns_csv)
    pool_limit = max(args.samples_per_type * 5, args.samples_per_type)
    normal_pool, high_conf_pool = collect_candidate_pools(
        args.start,
        args.end,
        args.processed_dir,
        patterns,
        bbox,  # type: ignore[arg-type]
        args.cell_size_deg,
        args.moving_sog,
        args.min_score,
        pool_limit,
        args.seed,
    )
    rows, stats = evaluate_injections(
        normal_pool,
        high_conf_pool,
        patterns,
        bbox,  # type: ignore[arg-type]
        args.cell_size_deg,
        args.moving_sog,
        args.min_score,
        args.samples_per_type,
        args.seed,
    )

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    args.stats_json.parent.mkdir(parents=True, exist_ok=True)
    args.stats_md.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "injection_id",
        "injection_type",
        "source_mmsi",
        "source_track_id",
        "source_time",
        "longitude",
        "latitude",
        "sog",
        "anomaly_score",
        "detected",
        "reasons",
    ]
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    args.stats_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_stats_markdown(args.stats_md, stats)
    print(json.dumps({"rows": len(rows), "stats": stats}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
