#!/usr/bin/env python3
"""Compare screening-cell rankings against strict-future supported episodes."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from risk_hotspots import cell_id_for_point


def load_cells(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row.get("eligible_hotspot_cell", 0) or 0) != 1:
                continue
            rows.append(
                {
                    "cell_id": row["cell_id"],
                    "point_count": float(row.get("point_count", 0) or 0),
                    "anomaly_component": float(row.get("anomaly_component", 0) or 0),
                    "encounter_component": float(row.get("encounter_component", 0) or 0),
                }
            )
    return rows


def load_episodes(
    path: Path,
    bbox: tuple[float, float, float, float],
    cell_size_deg: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                lon = float(row["prediction_mid_lon"])
                lat = float(row["prediction_mid_lat"])
            except (KeyError, TypeError, ValueError):
                continue
            cell_id = cell_id_for_point(lon, lat, bbox, cell_size_deg)
            if cell_id is None:
                continue
            rows.append(
                {
                    "cell_id": cell_id,
                    "observable": int(row.get("observable_followup", 0) or 0),
                    "supported": int(row.get("supported_within_threshold", 0) or 0),
                }
            )
    return rows


def jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def ranking_scores(cells: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    scores: dict[str, dict[str, float]] = {
        "density_only": {},
        "anomaly_only": {},
        "encounter_only": {},
        "fused_w0.50": {},
        "fused_w0.75": {},
        "fused_w1.00": {},
    }
    for row in cells:
        cell_id = str(row["cell_id"])
        anomaly = float(row["anomaly_component"])
        encounter = float(row["encounter_component"])
        scores["density_only"][cell_id] = float(row["point_count"])
        scores["anomaly_only"][cell_id] = anomaly
        scores["encounter_only"][cell_id] = encounter
        for weight in (0.50, 0.75, 1.00):
            scores[f"fused_w{weight:.2f}"][cell_id] = anomaly + weight * encounter
    return scores


def top_cells(scores: dict[str, float], count: int) -> set[str]:
    ranked = sorted(scores, key=lambda cell_id: (-scores[cell_id], cell_id))
    return set(ranked[:count])


def evaluate_rankings(
    cells: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    top_fractions: tuple[float, ...] = (0.05, 0.10, 0.20),
) -> dict[str, Any]:
    score_sets = ranking_scores(cells)
    eligible_ids = {str(row["cell_id"]) for row in cells}
    eligible_episodes = [row for row in episodes if row["cell_id"] in eligible_ids]
    observable = [row for row in eligible_episodes if int(row["observable"])]
    supported = [row for row in observable if int(row["supported"])]
    global_support_rate = len(supported) / len(observable) if observable else None

    evaluations: dict[str, list[dict[str, Any]]] = {}
    top_sets_10: dict[str, set[str]] = {}
    for method, scores in score_sets.items():
        method_rows: list[dict[str, Any]] = []
        for fraction in top_fractions:
            count = max(1, int(len(cells) * fraction)) if cells else 0
            selected = top_cells(scores, count)
            selected_observable = [row for row in observable if row["cell_id"] in selected]
            selected_supported = [row for row in selected_observable if int(row["supported"])]
            selected_support_rate = (
                len(selected_supported) / len(selected_observable) if selected_observable else None
            )
            method_rows.append(
                {
                    "top_fraction": fraction,
                    "selected_cells": count,
                    "observable_episodes": len(selected_observable),
                    "supported_episodes": len(selected_supported),
                    "supported_episode_capture_rate": (
                        round(len(selected_supported) / len(supported), 6) if supported else None
                    ),
                    "support_rate_selected": (
                        round(selected_support_rate, 6) if selected_support_rate is not None else None
                    ),
                    "support_rate_enrichment": (
                        round(selected_support_rate / global_support_rate, 6)
                        if selected_support_rate is not None and global_support_rate
                        else None
                    ),
                }
            )
            if math.isclose(fraction, 0.10):
                top_sets_10[method] = selected
        evaluations[method] = method_rows

    methods = list(score_sets)
    jaccard_rows = []
    for index, method_a in enumerate(methods):
        for method_b in methods[index + 1 :]:
            jaccard_rows.append(
                {
                    "method_a": method_a,
                    "method_b": method_b,
                    "top_10_percent_jaccard": round(
                        jaccard(top_sets_10.get(method_a, set()), top_sets_10.get(method_b, set())), 6
                    ),
                }
            )

    return {
        "interpretation": "ranking enrichment for review prioritization; not accident or near-miss accuracy",
        "eligible_cells": len(cells),
        "episodes_assigned_to_eligible_cells": len(eligible_episodes),
        "observable_episodes_in_eligible_cells": len(observable),
        "strict_future_supported_episodes_in_eligible_cells": len(supported),
        "global_support_rate_in_eligible_cells": round(global_support_rate, 6) if global_support_rate is not None else None,
        "rankings": evaluations,
        "top_10_percent_jaccard": jaccard_rows,
    }


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Screening Ranking Baselines and Weight Sensitivity",
        "",
        "Top-ranked cells are review-priority units, not statistically significant risk clusters.",
        "Episode support uses the strict-future synchronized geometric check.",
        "",
        "| Ranking | Top cells | Supported episode capture | Selected-cell support rate | Enrichment |",
        "|---|---:|---:|---:|---:|",
    ]
    for method, rows in result["rankings"].items():
        row = next(item for item in rows if math.isclose(item["top_fraction"], 0.10))
        lines.append(
            f"| {method} | {row['selected_cells']:,} | {100 * row['supported_episode_capture_rate']:.1f}% | "
            f"{100 * row['support_rate_selected']:.1f}% | {row['support_rate_enrichment']:.3f}x |"
        )
    lines.extend(
        [
            "",
            "## Fusion-weight top-10% overlap",
            "",
            "| Weight pair | Jaccard |",
            "|---|---:|",
        ]
    )
    for row in result["top_10_percent_jaccard"]:
        if not (str(row["method_a"]).startswith("fused") and str(row["method_b"]).startswith("fused")):
            continue
        lines.append(
            f"| {row['method_a']} vs {row['method_b']} | {row['top_10_percent_jaccard']:.3f} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate screening-cell ranking baselines.")
    parser.add_argument("--hotspot-csv", type=Path, required=True)
    parser.add_argument("--backtest-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--bbox", type=float, nargs=4, required=True)
    parser.add_argument("--cell-size-deg", type=float, default=0.005)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cells = load_cells(args.hotspot_csv)
    episodes = load_episodes(args.backtest_csv, tuple(args.bbox), args.cell_size_deg)
    result = evaluate_rankings(cells, episodes)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(args.output_md, result)
    print(
        json.dumps(
            {
                "eligible_cells": result["eligible_cells"],
                "supported_episodes": result["strict_future_supported_episodes_in_eligible_cells"],
                "global_support_rate": result["global_support_rate_in_eligible_cells"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
