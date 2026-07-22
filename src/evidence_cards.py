#!/usr/bin/env python3
"""Build de-identified hotspot evidence cards for the web prototype."""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any


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
    parser.add_argument("--typology-csv", type=Path, required=True, help="Hotspot typology CSV.")
    parser.add_argument("--encounter-csv", type=Path, required=True, help="Encounter candidate CSV.")
    parser.add_argument("--backtest-csv", type=Path, required=True, help="Encounter backtest CSV.")
    parser.add_argument("--case-json", type=Path, required=True, help="Case studies JSON.")
    parser.add_argument("--bbox", type=float, nargs=4, required=True, help="Study area bbox.")
    parser.add_argument("--cell-size-deg", type=float, default=0.005, help="Grid cell size.")
    parser.add_argument("--episode-gap-min", type=float, default=15.0, help="Encounter episode gap.")
    parser.add_argument("--output-json", type=Path, required=True, help="Output evidence cards JSON.")
    parser.add_argument("--output-csv", type=Path, help="Optional table CSV.")
    parser.add_argument("--web-json", type=Path, nargs="*", default=[], help="Additional JSON paths to write.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    print(
        json.dumps(
            {
                "card_count": cards["card_count"],
                "total_encounter_episodes_on_cards": cards["summary"]["total_encounter_episodes_on_cards"],
                "total_backtest_supported_episodes_on_cards": cards["summary"][
                    "total_backtest_supported_episodes_on_cards"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
