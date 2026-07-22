#!/usr/bin/env python3
"""Audit derived-bearing coverage and agreement with native AIS COG."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
from pathlib import Path
from typing import Any


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)]


def parse_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def angular_delta(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * pct
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def direction_bin(value: float) -> int:
    return int(((value + 22.5) % 360) // 45)


def audit_dataset(
    processed_dir: Path,
    dataset_prefix: str,
    start: dt.date,
    end: dt.date,
    min_sog: float = 1.0,
) -> dict[str, Any]:
    counters = {
        "feature_rows": 0,
        "moving_rows": 0,
        "derived_bearing_available": 0,
        "native_cog_available": 0,
        "both_available": 0,
        "derived_preferred_by_pipeline": 0,
        "cog_fallback_used": 0,
        "no_direction_available": 0,
        "same_45deg_direction_bin": 0,
    }
    deltas: list[float] = []
    for day in iter_dates(start, end):
        path = processed_dir / f"{dataset_prefix}_{day.isoformat()}_features.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                counters["feature_rows"] += 1
                sog = parse_float(row.get("sog"))
                if sog is None or sog < min_sog:
                    continue
                counters["moving_rows"] += 1
                derived = parse_float(row.get("bearing_deg"))
                cog = parse_float(row.get("cog"))
                if derived is not None:
                    counters["derived_bearing_available"] += 1
                    counters["derived_preferred_by_pipeline"] += 1
                elif cog is not None:
                    counters["cog_fallback_used"] += 1
                else:
                    counters["no_direction_available"] += 1
                if cog is not None:
                    counters["native_cog_available"] += 1
                if derived is not None and cog is not None:
                    counters["both_available"] += 1
                    delta = angular_delta(derived, cog)
                    deltas.append(delta)
                    if direction_bin(derived) == direction_bin(cog):
                        counters["same_45deg_direction_bin"] += 1

    moving = counters["moving_rows"]
    both = counters["both_available"]
    return {
        "dataset_prefix": dataset_prefix,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "min_sog_kn": min_sog,
        **counters,
        "derived_coverage_rate": round(counters["derived_bearing_available"] / moving, 6) if moving else None,
        "pipeline_direction_coverage_rate": round(
            (counters["derived_preferred_by_pipeline"] + counters["cog_fallback_used"]) / moving, 6
        )
        if moving
        else None,
        "derived_vs_cog_delta_deg": {
            "median": round(statistics.median(deltas), 6) if deltas else None,
            "p75": round(percentile(deltas, 0.75), 6) if deltas else None,
            "p90": round(percentile(deltas, 0.90), 6) if deltas else None,
            "p95": round(percentile(deltas, 0.95), 6) if deltas else None,
            "within_22_5_deg_rate": round(sum(value <= 22.5 for value in deltas) / both, 6) if both else None,
            "within_45_deg_rate": round(sum(value <= 45 for value in deltas) / both, 6) if both else None,
            "same_45deg_direction_bin_rate": round(counters["same_45deg_direction_bin"] / both, 6)
            if both
            else None,
        },
    }


def write_markdown(path: Path, results: list[dict[str, Any]]) -> None:
    lines = [
        "# Direction-Source Portability Audit",
        "",
        "The analysis pipeline prefers segment-derived bearing and uses native COG only as a fallback. "
        "This audit therefore tests coverage and agreement rather than repeating a redundant COG-disable run.",
        "",
        "| Dataset | Moving states | Derived coverage | COG fallback | No direction | Derived/COG median delta | Same 45-deg bin |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        agreement = result["derived_vs_cog_delta_deg"]
        median = "n/a" if agreement["median"] is None else f"{agreement['median']:.2f} deg"
        same_bin = "n/a" if agreement["same_45deg_direction_bin_rate"] is None else f"{100 * agreement['same_45deg_direction_bin_rate']:.1f}%"
        lines.append(
            f"| {result['dataset_prefix']} | {result['moving_rows']:,} | {100 * result['derived_coverage_rate']:.1f}% | "
            f"{result['cog_fallback_used']:,} | {result['no_direction_available']:,} | {median} | {same_bin} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit derived bearing and native COG availability.")
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results = [
        audit_dataset(args.processed_dir, "sf_bay_ais", dt.date(2025, 5, 1), dt.date(2025, 5, 7)),
        audit_dataset(args.processed_dir, "tokyo_bay_ais", dt.date(2024, 8, 1), dt.date(2024, 8, 7)),
    ]
    payload = {
        "method": "derived-bearing coverage and native-COG agreement audit",
        "pipeline_direction_precedence": "bearing_deg then cog fallback",
        "datasets": results,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(args.output_md, results)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
