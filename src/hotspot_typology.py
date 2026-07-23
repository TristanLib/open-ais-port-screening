#!/usr/bin/env python3
"""Classify hotspot cells into interpretable evidence types."""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path
from typing import Any


def parse_float(value: str | int | float | None) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_int(value: str | int | float | None) -> int:
    return int(round(parse_float(value)))


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def daily_topk_counts(stability_json: Path) -> dict[str, dict[str, int]]:
    data = load_json(stability_json)
    counts: dict[str, dict[str, int]] = collections.defaultdict(lambda: collections.defaultdict(int))
    for variant, days in data.get("daily", {}).items():
        for day in days:
            for cell in day.get("top_cells", []):
                counts[str(cell["cell_id"])][variant] += 1
    return {cell_id: dict(value) for cell_id, value in counts.items()}


def density_percentiles(rows: list[dict[str, str]]) -> dict[str, float]:
    sorted_rows = sorted(rows, key=lambda row: parse_float(row.get("point_count")), reverse=True)
    total = max(1, len(sorted_rows) - 1)
    return {
        row["cell_id"]: round(1 - (index / total), 6) if total else 1.0
        for index, row in enumerate(sorted_rows)
    }


def classify_dominant_evidence(row: dict[str, str], encounter_weight: float) -> tuple[str, float, float]:
    anomaly_component = parse_float(row.get("anomaly_component"))
    encounter_component = parse_float(row.get("encounter_component")) * encounter_weight
    total = anomaly_component + encounter_component
    anomaly_share = anomaly_component / total if total else 0.0
    encounter_share = encounter_component / total if total else 0.0
    anomaly_count = parse_int(row.get("anomaly_count"))
    encounter_count = parse_int(row.get("encounter_count"))
    if anomaly_count > 0 and encounter_count > 0:
        if anomaly_share >= 0.65:
            evidence = "anomaly-dominated fused evidence"
        elif encounter_share >= 0.65:
            evidence = "encounter-dominated fused evidence"
        else:
            evidence = "balanced anomaly-encounter evidence"
    elif anomaly_count > 0:
        evidence = "behavior-component-only candidate evidence"
    elif encounter_count > 0:
        evidence = "encounter-only candidate evidence"
    else:
        evidence = "exposure-only context"
    return evidence, round(anomaly_share, 6), round(encounter_share, 6)


def classify_stability(
    cell_counts: dict[str, int],
    density_percentile: float,
    is_hotspot: bool,
    days: int,
) -> str:
    density_days = cell_counts.get("density_only", 0)
    fused_days = cell_counts.get("fused_risk", 0)
    stable_day_threshold = max(3, round(days * 0.5))
    if density_days >= stable_day_threshold and fused_days >= stable_day_threshold:
        return "stable exposure with persistent candidate evidence"
    if density_days >= stable_day_threshold:
        return "stable traffic-exposure hotspot"
    if fused_days >= stable_day_threshold:
        return "persistent candidate-evidence hotspot"
    if is_hotspot and (fused_days > 0 or density_percentile < 0.75):
        return "event-sensitive candidate hotspot"
    if density_percentile >= 0.90:
        return "high-exposure context cell"
    return "intermittent evidence cell"


def classify_hotspot_type(
    dominant_evidence: str,
    stability_class: str,
    density_percentile: float,
    is_hotspot: bool,
) -> str:
    if not is_hotspot:
        if density_percentile >= 0.90:
            return "non-hotspot high-exposure context"
        return "non-hotspot candidate context"
    if "stable traffic-exposure" in stability_class:
        return "stable-exposure review hotspot"
    if "persistent candidate" in stability_class:
        return "persistent candidate-evidence hotspot"
    if "event-sensitive" in stability_class:
        return "event-sensitive review hotspot"
    if "encounter-dominated" in dominant_evidence:
        return "encounter-dominated review hotspot"
    if "anomaly-dominated" in dominant_evidence:
        return "anomaly-dominated review hotspot"
    return "fused-evidence review hotspot"


def review_focus(row: dict[str, str], dominant_evidence: str, stability_class: str) -> str:
    reason = (row.get("dominant_reason") or "").replace("_", " ")
    if "stable traffic-exposure" in stability_class:
        return "Compare stable traffic exposure with candidate evidence; density alone is not a safety finding."
    if "encounter-dominated" in dominant_evidence:
        return "Review close-quarters candidate episodes and local crossing or meeting context."
    if "anomaly-dominated" in dominant_evidence:
        return f"Review behavioral deviation evidence, especially {reason or 'the dominant anomaly reason'}."
    if "balanced" in dominant_evidence:
        return "Review combined behavioral and encounter evidence before any operational interpretation."
    if reason:
        return f"Review {reason} evidence as a candidate screening cue."
    return "Use as contextual screening evidence for human audit."


def build_typology(
    fused_csv: Path,
    fused_stats_json: Path,
    stability_json: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = load_rows(fused_csv)
    stats = load_json(fused_stats_json)
    start = stats.get("start")
    end = stats.get("end")
    days = 7
    if start and end:
        import datetime as dt

        days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1
    encounter_weight = float(stats.get("encounter_weight") or 0.75)
    topk_counts = daily_topk_counts(stability_json)
    density_pct = density_percentiles(rows)

    enriched: list[dict[str, Any]] = []
    for row in rows:
        cell_id = row["cell_id"]
        dominant_evidence, anomaly_share, encounter_share = classify_dominant_evidence(row, encounter_weight)
        counts = topk_counts.get(cell_id, {})
        is_hotspot = parse_int(row.get("is_hotspot")) == 1
        percentile = density_pct.get(cell_id, 0.0)
        stability_class = classify_stability(counts, percentile, is_hotspot, days)
        hotspot_type = classify_hotspot_type(dominant_evidence, stability_class, percentile, is_hotspot)
        item: dict[str, Any] = {
            **row,
            "dominant_evidence": dominant_evidence,
            "hotspot_type": hotspot_type,
            "stability_class": stability_class,
            "density_percentile": percentile,
            "density_topk_days": counts.get("density_only", 0),
            "anomaly_topk_days": counts.get("anomaly_only", 0),
            "encounter_topk_days": counts.get("encounter_only", 0),
            "fused_topk_days": counts.get("fused_risk", 0),
            "anomaly_component_share": anomaly_share,
            "encounter_component_share": encounter_share,
            "review_focus": review_focus(row, dominant_evidence, stability_class),
        }
        enriched.append(item)

    hotspot_rows = [row for row in enriched if parse_int(row.get("is_hotspot")) == 1]
    stats_out = {
        "start": start,
        "end": end,
        "method": "hotspot_typology_by_evidence_contribution_and_daily_topk_stability",
        "cells": len(enriched),
        "hotspot_cells": len(hotspot_rows),
        "encounter_weight": encounter_weight,
        "counts_by_hotspot_type": dict(collections.Counter(str(row["hotspot_type"]) for row in hotspot_rows)),
        "counts_by_dominant_evidence": dict(collections.Counter(str(row["dominant_evidence"]) for row in hotspot_rows)),
        "counts_by_stability_class": dict(collections.Counter(str(row["stability_class"]) for row in hotspot_rows)),
        "top_hotspots": [
            compact_hotspot(row)
            for row in sorted(hotspot_rows, key=lambda item: parse_float(item.get("risk_score")), reverse=True)[:20]
        ],
    }
    return enriched, stats_out


def compact_hotspot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "cell_id": row["cell_id"],
        "risk_score": parse_float(row.get("risk_score")),
        "hotspot_type": row["hotspot_type"],
        "dominant_evidence": row["dominant_evidence"],
        "stability_class": row["stability_class"],
        "review_focus": row["review_focus"],
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "cell_id",
        "risk_score",
        "is_hotspot",
        "hotspot_type",
        "dominant_evidence",
        "stability_class",
        "review_focus",
        "density_percentile",
        "density_topk_days",
        "anomaly_topk_days",
        "encounter_topk_days",
        "fused_topk_days",
        "anomaly_component_share",
        "encounter_component_share",
        "anomaly_count",
        "corroborated_anomaly_count",
        "low_support_only_count",
        "weighted_anomaly_count",
        "encounter_count",
        "pair_opportunity_count",
        "point_count",
        "moving_count",
        "mean_anomaly_score",
        "weighted_mean_anomaly_score",
        "mean_encounter_score",
        "max_encounter_score",
        "dominant_reason",
        "anomaly_component",
        "encounter_component",
        "anomaly_rate",
        "encounter_rate",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_markdown(path: Path, stats: dict[str, Any]) -> None:
    lines = [
        "# Hotspot Typology",
        "",
        "Hotspot types separate stable traffic exposure from event-sensitive candidate evidence. "
        "The typology is an explanation layer for human review, not an incident classifier.",
        "",
        "## Dominant Evidence",
        "",
        "| Type | Hotspot cells |",
        "|---|---:|",
    ]
    for key, value in sorted(stats["counts_by_dominant_evidence"].items()):
        lines.append(f"| {key} | {value:,} |")
    lines.extend(["", "## Stability Class", "", "| Class | Hotspot cells |", "|---|---:|"])
    for key, value in sorted(stats["counts_by_stability_class"].items()):
        lines.append(f"| {key} | {value:,} |")
    lines.extend(["", "## Top Hotspots", "", "| Cell | Type | Dominant evidence | Review focus |", "|---|---|---|---|"])
    for row in stats["top_hotspots"][:10]:
        lines.append(
            f"| {row['cell_id']} | {row['hotspot_type']} | {row['dominant_evidence']} | {row['review_focus']} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def enrich_geojson(path: Path, rows_by_cell: dict[str, dict[str, Any]]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for feature in data.get("features", []):
        properties = feature.get("properties") or {}
        cell_id = str(properties.get("cell_id", ""))
        typology = rows_by_cell.get(cell_id)
        if not typology:
            continue
        properties.update(
            {
                "hotspot_type": typology["hotspot_type"],
                "dominant_evidence": typology["dominant_evidence"],
                "stability_class": typology["stability_class"],
                "review_focus": typology["review_focus"],
                "density_topk_days": typology["density_topk_days"],
                "fused_topk_days": typology["fused_topk_days"],
                "anomaly_component_share": typology["anomaly_component_share"],
                "encounter_component_share": typology["encounter_component_share"],
            }
        )
        feature["properties"] = properties
    data.setdefault("metadata", {})["hotspot_typology"] = "evidence_contribution_and_daily_topk_stability"
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify fused hotspot cells into interpretable types.")
    parser.add_argument("--fused-csv", type=Path, required=True, help="Fused hotspot CSV.")
    parser.add_argument("--fused-stats-json", type=Path, required=True, help="Fused hotspot stats JSON.")
    parser.add_argument("--stability-json", type=Path, required=True, help="Hotspot stability JSON.")
    parser.add_argument("--output-csv", type=Path, required=True, help="Output typology CSV.")
    parser.add_argument("--stats-json", type=Path, required=True, help="Output typology stats JSON.")
    parser.add_argument("--summary-md", type=Path, help="Optional Markdown summary.")
    parser.add_argument("--geojson", type=Path, nargs="*", default=[], help="GeoJSON files to enrich in place.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows, stats = build_typology(args.fused_csv, args.fused_stats_json, args.stability_json)
    write_csv(args.output_csv, rows)
    args.stats_json.parent.mkdir(parents=True, exist_ok=True)
    args.stats_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.summary_md:
        write_markdown(args.summary_md, stats)
    rows_by_cell = {str(row["cell_id"]): row for row in rows}
    for path in args.geojson:
        enrich_geojson(path, rows_by_cell)
    print(
        json.dumps(
            {
                "hotspot_cells": stats["hotspot_cells"],
                "counts_by_hotspot_type": stats["counts_by_hotspot_type"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
