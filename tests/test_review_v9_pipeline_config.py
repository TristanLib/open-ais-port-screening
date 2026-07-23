from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import build_web_datasets
import run_tokyo_bay_validation as tokyo


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def parse_simple_sections(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the simple two-level result sections used by public manifests."""
    sections: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        top = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", raw_line)
        if top and not raw_line.startswith(" "):
            key, value = top.groups()
            if value.strip():
                sections[key] = {"__value__": parse_scalar(value)}
                current = None
            else:
                sections[key] = {}
                current = key
            continue
        if current and raw_line.startswith("  "):
            child = re.match(r"^  ([A-Za-z0-9_]+):\s*(.*)$", raw_line)
            if child:
                key, value = child.groups()
                sections[current][key] = parse_scalar(value)
    return sections


class ReviewV9PipelineConfigTest(unittest.TestCase):
    def test_tokyo_backtest_explicitly_freezes_primary_observability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            encounter_csv = root / "encounters.csv"
            encounter_csv.write_text("date,mmsi_a,mmsi_b\n", encoding="utf-8")
            with (
                mock.patch.object(tokyo, "load_encounter_rows", return_value=[]),
                mock.patch.object(tokyo, "group_episodes", return_value=[]),
                mock.patch.object(tokyo, "load_positions", return_value={}),
                mock.patch.object(tokyo, "backtest_episodes", return_value=[]) as backtest,
                mock.patch.object(
                    tokyo,
                    "summarize_backtest",
                    return_value={"supported_episodes_within_threshold": 0},
                ) as summarize,
                mock.patch.object(tokyo, "write_backtest_csv"),
                mock.patch.object(tokyo, "write_backtest_markdown"),
            ):
                tokyo.build_backtest(
                    tokyo.DEFAULT_START,
                    tokyo.DEFAULT_START,
                    "tokyo_bay_ais",
                    root,
                    encounter_csv,
                    root / "backtest.csv",
                    root / "backtest.json",
                    root / "backtest.md",
                    overwrite=True,
                )

        self.assertEqual(backtest.call_args.kwargs["min_common_fraction"], 0.70)
        self.assertEqual(backtest.call_args.kwargs["max_uncovered_gap_s"], 180)
        self.assertEqual(backtest.call_args.kwargs["predicted_time_tolerance_s"], 60)
        self.assertEqual(summarize.call_args.kwargs["min_common_fraction"], 0.70)
        self.assertEqual(summarize.call_args.kwargs["max_uncovered_gap_s"], 180)
        self.assertEqual(summarize.call_args.kwargs["predicted_time_tolerance_s"], 60)

    def test_manifests_encode_review_v9_recomputation_contract(self) -> None:
        sf = (ROOT / "configs" / "data_manifest.yml").read_text(encoding="utf-8")
        tokyo_manifest = (ROOT / "configs" / "data_manifest_tokyo_bay.yml").read_text(
            encoding="utf-8"
        )
        full_workspace = (ROOT / "outputs" / "tables").is_dir()

        for text in (sf, tokyo_manifest):
            self.assertIn("time_bin_seconds: 60", text)
            self.assertIn("max_state_skew_s: 60", text)
            self.assertIn("min_common_fraction: 0.70", text)
            self.assertIn("max_uncovered_gap_s: 180", text)
            self.assertIn("predicted_time_tolerance_s: 60", text)
            if full_workspace:
                self.assertIn("encounter_evidence_cards:", text)
                self.assertIn("--opportunity-records-csv", text)
                self.assertIn("geometric_control_evaluation.py", text)
                self.assertIn("dual_objective_rank_evaluation.py", text)
                self.assertIn("--encounter-card-output-json", text)
            else:
                self.assertIn("encounter_evidence_card_count: 12", text)

        self.assertIn("status: review_v9_recomputed_verified", sf)
        self.assertIn("status: review_v9_recomputed_verified", tokyo_manifest)
        self.assertNotIn("time_bin_seconds: 300", tokyo_manifest)
        self.assertIn("verified_results:", tokyo_manifest)
        self.assertNotIn("pending_review_v9_recomputation", tokyo_manifest)
        if full_workspace:
            self.assertIn(
                "dist/open_ais_port_screening_public_review_v9_2026-07-22.zip",
                sf,
            )
        else:
            self.assertNotIn("dist/", sf)
        self.assertNotIn("internal_expert_review_bundle:", sf)
        self.assertNotIn("submission_package_review_v8", sf)

    def test_tokyo_web_summary_is_derived_from_current_review_v9_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tables = Path(tmp)
            prefix = f"tokyo_bay_ais_{build_web_datasets.TOKYO_RANGE}"

            def write(suffix: str, value: dict[str, object]) -> None:
                (tables / f"{prefix}_{suffix}").write_text(
                    json.dumps(value), encoding="utf-8"
                )

            write(
                "validation_summary.json",
                {
                    "headline_results": {
                        "converted_ais_points": 1000,
                        "clean_ais_points": 900,
                        "trajectory_segments": 80,
                        "min20_segments": 50,
                        "normal_traffic_pattern_cells": 12,
                        "high_confidence_pattern_cells": 5,
                        "anomaly_candidate_points": 90,
                        "encounter_candidate_records": 40,
                        "encounter_audit_episodes": 20,
                        "backtest_supported_episodes": 8,
                        "backtest_support_rate_observable": 0.5,
                        "fused_hotspot_cells": 4,
                    }
                },
            )
            write("traffic_patterns_stats.json", {"nonempty_moving_cells": 20, "min_sog": 1.0})
            write("anomaly_stats.json", {"counters": {"anomaly_rows": 90}})
            write(
                "encounters_stats.json",
                {
                    "encounters": 41,
                    "deduplicated_encounter_episodes": 21,
                    "pair_opportunity_records": 200,
                },
            )
            write(
                "encounter_backtest_stats.json",
                {
                    "method": "strict_future",
                    "future_only": True,
                    "encounter_candidate_records": 41,
                    "deduplicated_encounter_episodes": 21,
                    "episodes_with_aligned_followup": 16,
                    "episodes_without_aligned_followup": 4,
                    "supported_episodes_within_threshold": 8,
                    "support_rate_observable": 0.5,
                    "near_supported_episodes_within_1nm": 12,
                    "near_support_rate_observable": 0.75,
                    "observable_rate": 0.8,
                    "future_coverage": {"common_sample_count_30s": {"median": 25}},
                    "observability_sensitivity": {"70pct_primary": {"observable_episodes": 16}},
                    "minimum_distance_grid_sensitivity": {"30": {"supported_within_threshold": 8}},
                    "primary_observability": {"common_grid_fraction": 0.7},
                    "actual_min_distance_nm": {"median": 0.3},
                    "predicted_dcpa_abs_error_nm": {"median": 0.1},
                },
            )
            write("fused_risk_hotspots_stats.json", {"hotspot_cells": 4})
            controls = {
                "method": "review_v9_controls",
                "matching": {"matched_pairs_with_both_strict_future_observable": 6},
                "matched_outcomes": {"within_0_5_nm": {"lift": 2.0}},
            }
            ranking = {
                "interpretation": "dual evidence-target review prioritization; not operational accuracy",
                "leave_one_day_out": {
                    "fusion_claim_gate": {
                        "passes_incremental_value_gate": False,
                        "paper_position": "optional multi-evidence review view",
                    }
                },
            }
            write("geometric_control_evaluation.json", controls)
            write("dual_objective_rank_evaluation.json", ranking)
            write(
                "encounter_evidence_cards.json",
                {
                    "card_count": 12,
                    "schema_version": "review-v9.encounter-evidence-card.v1",
                    "publication_mode": "sanitized_research_demo",
                    "cards": [{} for _ in range(12)],
                },
            )

            summary = build_web_datasets.build_tokyo_summary(tables)

        self.assertEqual(summary["dataset"]["clean_ais_points"], 900)
        self.assertEqual(summary["encounter_risk"]["encounters"], 41)
        self.assertEqual(summary["encounter_risk"]["deduplicated_encounter_episodes"], 21)
        self.assertEqual(summary["encounter_backtest"]["future_coverage"], {"common_sample_count_30s": {"median": 25}})
        self.assertEqual(summary["geometric_controls"], controls)
        self.assertEqual(summary["ranking_evaluation"], ranking)
        self.assertEqual(summary["encounter_evidence_cards"]["card_count"], 12)
        self.assertEqual(
            summary["fusion_claim_gate"]["paper_position"],
            "optional multi-evidence review view",
        )

    def test_sf_web_ablation_and_legacy_cards_use_current_review_v9_counts(self) -> None:
        manifest = parse_simple_sections(ROOT / "configs" / "data_manifest.yml")["headline_results"]
        summary = json.loads((ROOT / "web" / "data" / "summary.json").read_text(encoding="utf-8"))
        ablation = {row["variant"]: row["result"] for row in summary["ablation"]}

        self.assertEqual(
            ablation["Anomaly-only"],
            f"{int(summary['hotspots']['anomaly_only_hotspots']):,} hotspot cells",
        )
        self.assertEqual(
            ablation["Encounter-only"],
            (
                f"{int(manifest['encounter_candidate_records']):,} records; "
                f"{int(manifest['encounter_audit_episodes']):,} episodes"
            ),
        )
        self.assertEqual(
            ablation["Fused screening"],
            f"{int(manifest['fused_hotspot_cells']):,} hotspot cells",
        )
        self.assertEqual(summary["evidence_cards"]["card_count"], manifest["fused_hotspot_cells"])

    def test_bundle_validation_accepts_arbitrary_internally_consistent_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            layer = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"screening_score": 0.5},
                        "geometry": {"type": "Point", "coordinates": [139.8, 35.3]},
                    }
                ],
            }
            for name in ("sf.geojson", "tokyo.geojson"):
                (target / name).write_text(json.dumps(layer), encoding="utf-8")
            (target / "summary.json").write_text(
                json.dumps({"dataset_id": "sf_bay"}), encoding="utf-8"
            )
            tokyo_summary = {
                "dataset_id": "tokyo_bay",
                "dataset": {"clean_ais_points": 900},
                "anomaly_detection": {"anomaly_points": 90},
                "encounter_risk": {"encounters": 40, "deduplicated_encounter_episodes": 20},
                "encounter_backtest": {
                    "encounter_candidate_records": 40,
                    "deduplicated_encounter_episodes": 20,
                    "episodes_with_aligned_followup": 16,
                    "episodes_without_aligned_followup": 4,
                    "supported_episodes_within_threshold": 8,
                },
                "hotspots": {"fused_hotspots": 1},
                "geometric_controls": {"method": "review_v9_controls"},
                "fusion_claim_gate": {"paper_position": "optional multi-evidence review view"},
            }
            (target / "summary_tokyo_bay.json").write_text(
                json.dumps(tokyo_summary), encoding="utf-8"
            )
            for dataset_id, manifest_name, summary_name, layer_name in (
                ("sf_bay", "manifest.json", "summary.json", "sf.geojson"),
                ("tokyo_bay", "manifest_tokyo_bay.json", "summary_tokyo_bay.json", "tokyo.geojson"),
            ):
                (target / manifest_name).write_text(
                    json.dumps(
                        {
                            "dataset_id": dataset_id,
                            "companion_data": {"summary": summary_name},
                            "layers": [{"id": "fused_risk_hotspots", "path": layer_name}],
                        }
                    ),
                    encoding="utf-8",
                )
            (target / "datasets.json").write_text(
                json.dumps(
                    {
                        "default_dataset": "sf_bay",
                        "datasets": [
                            {"id": "sf_bay", "manifest": "manifest.json"},
                            {"id": "tokyo_bay", "manifest": "manifest_tokyo_bay.json"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            build_web_datasets.validate_bundle(target)


if __name__ == "__main__":
    unittest.main()
