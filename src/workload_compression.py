#!/usr/bin/env python3
"""Summarize evidence aggregation across the screening pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def pct(value: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return round(float(value) / float(denominator) * 100, 6)


def factor(denominator: int | float, value: int | float) -> float | None:
    if value == 0:
        return None
    return round(float(denominator) / float(value), 3)


def format_number(value: object) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value >= 100:
            return f"{value:,.1f}"
        return f"{value:.4f}"
    return "" if value is None else str(value)


def build_compression_summary(
    start: str,
    end: str,
    tables_dir: Path,
) -> dict[str, Any]:
    prefix = f"sf_bay_ais_{start}_to_{end}"
    pipeline = load_json(tables_dir / f"{prefix}_pipeline_summary.json")
    anomaly = load_json(tables_dir / f"{prefix}_anomaly_stats.json")
    encounters = load_json(tables_dir / f"{prefix}_encounters_stats.json")
    fused = load_json(tables_dir / f"{prefix}_fused_risk_hotspots_stats.json")
    cases = load_json(tables_dir / f"{prefix}_case_studies.json")
    backtest_path = tables_dir / f"{prefix}_encounter_backtest_stats.json"
    backtest = load_json(backtest_path) if backtest_path.exists() else {}
    evidence_cards_path = tables_dir / f"{prefix}_evidence_cards.json"
    evidence_cards = load_json(evidence_cards_path) if evidence_cards_path.exists() else {}

    clean_points = int(pipeline.get("totals", {}).get("clean_rows") or 0)
    anomaly_points = int(anomaly.get("counters", {}).get("anomaly_rows") or 0)
    encounter_records = int(encounters.get("encounters") or 0)
    encounter_episodes = int(encounters.get("deduplicated_encounter_episodes") or 0)
    risk_cells = int(fused.get("risk_cells") or 0)
    eligible_hotspot_cells = int(fused.get("eligible_hotspot_cells") or 0)
    fused_hotspots = int(fused.get("hotspot_cells") or 0)
    case_tracks = len(cases.get("cases", []))
    supported_episodes = int(backtest.get("supported_episodes_within_threshold") or 0)
    episodes_on_cards = int(evidence_cards.get("summary", {}).get("total_encounter_episodes_on_cards") or 0)

    stages = [
        {
            "stage_id": "clean_points",
            "stage": "Cleaned AIS observations",
            "review_object": "AIS point",
            "count": clean_points,
            "share_of_clean_points_percent": 100.0,
            "aggregation_ratio_from_clean_points": 1.0,
            "interpretation": "Starting point after quality control.",
        },
        {
            "stage_id": "anomaly_candidate_points",
            "stage": "Anomaly-candidate observations",
            "review_object": "candidate point",
            "count": anomaly_points,
            "share_of_clean_points_percent": pct(anomaly_points, clean_points),
            "aggregation_ratio_from_clean_points": factor(clean_points, anomaly_points),
            "interpretation": "Behavioral candidates selected for review, not event labels.",
        },
        {
            "stage_id": "encounter_candidate_records",
            "stage": "CPA/TCPA future encounter-candidate records",
            "review_object": "time-bin pair record",
            "count": encounter_records,
            "share_of_clean_points_percent": pct(encounter_records, clean_points),
            "aggregation_ratio_from_clean_points": factor(clean_points, encounter_records),
            "interpretation": "Pair records generated from future CPA/TCPA screening.",
        },
        {
            "stage_id": "encounter_audit_episodes",
            "stage": "Deduplicated encounter audit episodes",
            "review_object": "audit episode",
            "count": encounter_episodes,
            "share_of_clean_points_percent": pct(encounter_episodes, clean_points),
            "aggregation_ratio_from_clean_points": factor(clean_points, encounter_episodes),
            "interpretation": "Repeated time-bin records merged into pair-day audit episodes.",
        },
        {
            "stage_id": "backtest_supported_episodes",
            "stage": "Strict-future supported encounter episodes",
            "review_object": "future-supported episode",
            "count": supported_episodes,
            "share_of_clean_points_percent": pct(supported_episodes, clean_points),
            "aggregation_ratio_from_clean_points": factor(clean_points, supported_episodes),
            "interpretation": "Episodes with synchronized future AIS separation within the screening threshold.",
        },
        {
            "stage_id": "fused_hotspot_cells",
            "stage": "Fused evidence hotspot cells",
            "review_object": "hotspot cell",
            "count": fused_hotspots,
            "share_of_clean_points_percent": pct(fused_hotspots, clean_points),
            "aggregation_ratio_from_clean_points": factor(clean_points, fused_hotspots),
            "risk_cell_share_percent": pct(fused_hotspots, risk_cells),
            "eligible_cell_share_percent": pct(fused_hotspots, eligible_hotspot_cells),
            "interpretation": "Spatial triage layer for human review; cells are not incident labels.",
        },
        {
            "stage_id": "representative_case_tracks",
            "stage": "Representative case tracks",
            "review_object": "de-identified case track",
            "count": case_tracks,
            "share_of_clean_points_percent": pct(case_tracks, clean_points),
            "aggregation_ratio_from_clean_points": factor(clean_points, case_tracks),
            "interpretation": "Small explanation set for audit and presentation.",
        },
    ]

    return {
        "start": start,
        "end": end,
        "method": "evidence_aggregation_summary",
        "analyst_time_measured": False,
        "clean_ais_points": clean_points,
        "risk_cells": risk_cells,
        "eligible_hotspot_cells": eligible_hotspot_cells,
        "key_message": (
            f"The pipeline aggregates {clean_points:,} cleaned AIS observations into "
            f"{fused_hotspots:,} spatial review units containing {episodes_on_cards:,} "
            "candidate episodes; actual analyst review time was not measured."
        ),
        "stages": stages,
    }


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Evidence Aggregation Summary",
        "",
        summary["key_message"],
        "",
        "Ratios compare unlike aggregation units and are not measured reductions in analyst time. "
        "Candidates are not accidents, near misses, or enforcement findings.",
        "",
        "| Stage | Review object | Count | Share of cleaned AIS points | Aggregation ratio from cleaned points | Interpretation |",
        "|---|---|---:|---:|---:|---|",
    ]
    for stage in summary["stages"]:
        share = stage.get("share_of_clean_points_percent")
        aggregation_ratio = stage.get("aggregation_ratio_from_clean_points")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(stage["stage"]),
                    str(stage["review_object"]),
                    format_number(stage["count"]),
                    "-" if share is None else f"{share:.4f}%",
                    "-" if aggregation_ratio is None else f"{aggregation_ratio:,.1f}:1",
                    str(stage["interpretation"]),
                ]
            )
            + " |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(path: Path, summary: dict[str, Any]) -> None:
    import csv

    fieldnames = [
        "stage_id",
        "stage",
        "review_object",
        "count",
        "share_of_clean_points_percent",
        "aggregation_ratio_from_clean_points",
        "risk_cell_share_percent",
        "eligible_cell_share_percent",
        "interpretation",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for stage in summary["stages"]:
            writer.writerow({key: stage.get(key, "") for key in fieldnames})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build evidence aggregation summary.")
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument("--tables-dir", type=Path, default=Path("outputs/tables"), help="Tables directory.")
    parser.add_argument("--output-json", type=Path, required=True, help="Output JSON.")
    parser.add_argument("--output-md", type=Path, help="Optional Markdown output.")
    parser.add_argument("--output-csv", type=Path, help="Optional CSV output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = build_compression_summary(args.start, args.end, args.tables_dir)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.output_md:
        write_markdown(args.output_md, summary)
    if args.output_csv:
        write_csv(args.output_csv, summary)
    print(
        json.dumps(
            {
                "clean_ais_points": summary["clean_ais_points"],
                "fused_hotspot_cells": next(
                    item["count"] for item in summary["stages"] if item["stage_id"] == "fused_hotspot_cells"
                ),
                "representative_case_tracks": next(
                    item["count"] for item in summary["stages"] if item["stage_id"] == "representative_case_tracks"
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
