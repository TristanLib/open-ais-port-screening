#!/usr/bin/env python3
"""Create lightweight audit summaries for cropped AIS CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path


NUMERIC_FIELDS = ["longitude", "latitude", "sog", "cog", "heading", "length", "width", "draft"]
KEY_FIELDS = [
    "mmsi",
    "base_date_time",
    "longitude",
    "latitude",
    "sog",
    "cog",
    "heading",
    "vessel_type",
    "status",
    "length",
    "width",
    "draft",
    "transceiver",
]


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def summarize_numeric(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "mean": None, "max": None}
    sorted_values = sorted(values)
    return {
        "count": len(values),
        "min": sorted_values[0],
        "mean": sum(sorted_values) / len(sorted_values),
        "p50": sorted_values[len(sorted_values) // 2],
        "p95": sorted_values[int((len(sorted_values) - 1) * 0.95)],
        "max": sorted_values[-1],
    }


def pct(count: int, total: int) -> float:
    return round(count / total * 100, 4) if total else 0.0


def audit_csv(input_path: Path) -> dict[str, object]:
    rows = 0
    unique_mmsi: set[str] = set()
    min_time: str | None = None
    max_time: str | None = None
    missing = Counter()
    numeric_values: dict[str, list[float]] = {field: [] for field in NUMERIC_FIELDS}
    vessel_type_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    transceiver_counts: Counter[str] = Counter()
    quality_flags = Counter()

    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []

        for row in reader:
            rows += 1
            mmsi = row.get("mmsi", "")
            if mmsi:
                unique_mmsi.add(mmsi)

            timestamp = row.get("base_date_time", "")
            if timestamp:
                if min_time is None or timestamp < min_time:
                    min_time = timestamp
                if max_time is None or timestamp > max_time:
                    max_time = timestamp

            for field in KEY_FIELDS:
                if row.get(field, "") == "":
                    missing[field] += 1

            for field in NUMERIC_FIELDS:
                value = parse_float(row.get(field))
                if value is not None:
                    numeric_values[field].append(value)

            vessel_type_counts[row.get("vessel_type", "") or "missing"] += 1
            status_counts[row.get("status", "") or "missing"] += 1
            transceiver_counts[row.get("transceiver", "") or "missing"] += 1

            sog = parse_float(row.get("sog"))
            cog = parse_float(row.get("cog"))
            heading = parse_float(row.get("heading"))
            if sog is None:
                quality_flags["missing_sog"] += 1
            elif sog < 0:
                quality_flags["negative_sog"] += 1
            elif sog > 60:
                quality_flags["sog_gt_60_kn"] += 1

            if cog is None:
                quality_flags["missing_cog"] += 1
            elif not 0 <= cog < 360:
                quality_flags["cog_outside_0_360"] += 1

            if heading is None:
                quality_flags["missing_heading"] += 1
            elif heading == 511 or not 0 <= heading <= 360:
                quality_flags["invalid_heading"] += 1

    return {
        "input_path": str(input_path),
        "rows": rows,
        "fields": fields,
        "unique_mmsi": len(unique_mmsi),
        "min_time": min_time,
        "max_time": max_time,
        "missing": {
            field: {"count": missing[field], "percent": pct(missing[field], rows)}
            for field in KEY_FIELDS
        },
        "numeric": {field: summarize_numeric(values) for field, values in numeric_values.items()},
        "vessel_type_top": vessel_type_counts.most_common(20),
        "status_top": status_counts.most_common(20),
        "transceiver_counts": transceiver_counts.most_common(),
        "quality_flags": {
            name: {"count": count, "percent": pct(count, rows)}
            for name, count in quality_flags.most_common()
        },
    }


def write_markdown(summary: dict[str, object], output_path: Path) -> None:
    rows = int(summary["rows"])
    lines: list[str] = []
    lines.append("# AIS Crop Audit")
    lines.append("")
    lines.append(f"Input: `{summary['input_path']}`")
    lines.append("")
    lines.append("## Basic Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Rows | {rows:,} |")
    lines.append(f"| Unique MMSI | {int(summary['unique_mmsi']):,} |")
    lines.append(f"| Min time | {summary['min_time']} |")
    lines.append(f"| Max time | {summary['max_time']} |")
    lines.append("")

    lines.append("## Missing Key Fields")
    lines.append("")
    lines.append("| Field | Missing rows | Percent |")
    lines.append("|---|---:|---:|")
    missing = summary["missing"]
    assert isinstance(missing, dict)
    for field, value in missing.items():
        assert isinstance(value, dict)
        lines.append(f"| `{field}` | {int(value['count']):,} | {value['percent']}% |")
    lines.append("")

    lines.append("## Numeric Ranges")
    lines.append("")
    lines.append("| Field | Count | Min | Mean | P50 | P95 | Max |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    numeric = summary["numeric"]
    assert isinstance(numeric, dict)
    for field, value in numeric.items():
        assert isinstance(value, dict)
        lines.append(
            f"| `{field}` | {int(value['count']):,} | {value['min']} | "
            f"{value.get('mean')} | {value.get('p50')} | {value.get('p95')} | {value['max']} |"
        )
    lines.append("")

    lines.append("## Vessel Type Top Counts")
    lines.append("")
    lines.append("| Vessel type | Rows | Percent |")
    lines.append("|---|---:|---:|")
    for vessel_type, count in summary["vessel_type_top"]:  # type: ignore[index]
        lines.append(f"| `{vessel_type}` | {count:,} | {pct(count, rows)}% |")
    lines.append("")

    lines.append("## Quality Flags")
    lines.append("")
    lines.append("| Flag | Rows | Percent |")
    lines.append("|---|---:|---:|")
    quality_flags = summary["quality_flags"]
    assert isinstance(quality_flags, dict)
    for flag, value in quality_flags.items():
        assert isinstance(value, dict)
        lines.append(f"| `{flag}` | {int(value['count']):,} | {value['percent']}% |")
    lines.append("")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit a cropped AIS CSV file.")
    parser.add_argument("--input", type=Path, required=True, help="Input cropped AIS CSV.")
    parser.add_argument("--json", type=Path, required=True, help="Output JSON summary.")
    parser.add_argument("--md", type=Path, required=True, help="Output Markdown summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = audit_csv(args.input)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.md.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(summary, args.md)
    print(json.dumps({"rows": summary["rows"], "unique_mmsi": summary["unique_mmsi"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
