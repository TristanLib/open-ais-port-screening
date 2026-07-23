from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dual_objective_rank_evaluation as ranking


class DualObjectiveRankingTest(unittest.TestCase):
    @staticmethod
    def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_rankings_report_both_targets_and_pareto_front(self) -> None:
        cells = [
            {"cell_id": "a", "point_count": 90.0, "anomaly_component": 0.0, "encounter_component": 10.0},
            {"cell_id": "b", "point_count": 80.0, "anomaly_component": 9.0, "encounter_component": 8.0},
            {"cell_id": "c", "point_count": 70.0, "anomaly_component": 8.0, "encounter_component": 0.0},
            {"cell_id": "d", "point_count": 100.0, "anomaly_component": 0.0, "encounter_component": 0.0},
        ]
        encounter_targets = [{"cell_id": "a"}, {"cell_id": "a"}, {"cell_id": "b"}]
        behavior_targets = [{"cell_id": "b"}, {"cell_id": "b"}, {"cell_id": "c"}]

        result = ranking.evaluate_rankings(cells, encounter_targets, behavior_targets, top_fractions=(0.25,))

        encounter = result["rankings"]["encounter_only"][0]
        fused = result["rankings"]["fused"][0]
        self.assertEqual(encounter["supported_encounter_capture_count"], 2)
        self.assertEqual(encounter["corroborated_behavior_capture_count"], 0)
        self.assertEqual(fused["supported_encounter_capture_count"], 1)
        self.assertEqual(fused["corroborated_behavior_capture_count"], 2)
        self.assertEqual(set(result["pareto_front_by_budget"]["0.25"]), {"encounter_only", "behavior_only", "fused"})

    def test_claim_gate_downgrades_when_fusion_loses_too_much_encounter_capture(self) -> None:
        fold_rows = []
        for day in ("d1", "d2", "d3"):
            fold_rows.extend(
                [
                    {"held_out_date": day, "top_fraction": fraction, "method": "encounter_only", "supported_encounter_capture_rate": 0.80, "corroborated_behavior_capture_rate": 0.10}
                    for fraction in (0.05, 0.10, 0.20)
                ]
            )
            fold_rows.extend(
                [
                    {"held_out_date": day, "top_fraction": fraction, "method": "fused", "supported_encounter_capture_rate": 0.60, "corroborated_behavior_capture_rate": 0.30}
                    for fraction in (0.05, 0.10, 0.20)
                ]
            )

        gate = ranking.evaluate_fusion_claim_gate(fold_rows)

        self.assertFalse(gate["passes_incremental_value_gate"])
        self.assertEqual(gate["required_encounter_retention"], 0.95)
        self.assertEqual(gate["paper_position"], "optional multi-evidence review view")

    def test_targets_outside_eligible_cells_remain_in_capture_denominator(self) -> None:
        cells = [
            {"cell_id": "a", "point_count": 10.0, "anomaly_component": 1.0, "encounter_component": 1.0}
        ]
        result = ranking.evaluate_rankings(
            cells,
            [{"cell_id": "a"}, {"cell_id": "outside"}],
            [{"cell_id": "a"}, {"cell_id": "outside"}],
            top_fractions=(1.0,),
        )

        fused = result["rankings"]["fused"][0]
        self.assertEqual(result["supported_encounter_targets"], 2)
        self.assertEqual(result["supported_encounter_targets_in_eligible_cells"], 1)
        self.assertEqual(fused["supported_encounter_capture_rate"], 0.5)
        self.assertEqual(fused["corroborated_behavior_capture_rate"], 0.5)

    def test_fold_specific_behavior_model_excludes_held_out_day_from_patterns_and_training_scores(self) -> None:
        track_fields = ["mmsi", "track_id", "longitude", "latitude", "sog", "bearing_deg", "cog"]
        feature_fields = [
            "mmsi",
            "track_id",
            "base_date_time",
            "longitude",
            "latitude",
            "sog",
            "bearing_deg",
            "cog",
            "turn_rate_deg_per_min",
            "accel_kn_per_min",
            "implied_sog_kn",
        ]
        training_dates = ["2025-01-01", "2025-01-02"]
        held_out = "2025-01-03"
        bbox = (0.0, 0.0, 1.0, 1.0)

        def populate(root: Path, held_out_bearing: float, held_out_speed: float) -> None:
            for index, day in enumerate(training_dates):
                self.write_csv(
                    root / f"fixture_{day}_tracks_min20.csv",
                    track_fields,
                    [
                        {
                            "mmsi": f"train-{index}-{row_index}",
                            "track_id": f"train-{index}-{row_index}",
                            "longitude": 0.101,
                            "latitude": 0.101,
                            "sog": 5.0,
                            "bearing_deg": 0.0,
                            "cog": "",
                        }
                        for row_index in range(3)
                    ],
                )
                self.write_csv(
                    root / f"fixture_{day}_features.csv",
                    feature_fields,
                    [
                        {
                            "mmsi": f"train-{index}",
                            "track_id": f"train-{index}",
                            "base_date_time": f"{day}T12:00:00",
                            "longitude": 0.101,
                            "latitude": 0.101,
                            "sog": 16.0,
                            "bearing_deg": 180.0,
                            "cog": "",
                            "turn_rate_deg_per_min": 35.0,
                            "accel_kn_per_min": 0.0,
                            "implied_sog_kn": 16.0,
                        }
                    ],
                )

            self.write_csv(
                root / f"fixture_{held_out}_tracks_min20.csv",
                track_fields,
                [
                    {
                        "mmsi": f"held-{row_index}",
                        "track_id": f"held-{row_index}",
                        "longitude": 0.101,
                        "latitude": 0.101,
                        "sog": held_out_speed,
                        "bearing_deg": held_out_bearing,
                        "cog": "",
                    }
                    for row_index in range(20)
                ],
            )
            self.write_csv(
                root / f"fixture_{held_out}_features.csv",
                feature_fields,
                [
                    {
                        "mmsi": "held",
                        "track_id": "held",
                        "base_date_time": f"{held_out}T12:00:00",
                        "longitude": 0.101,
                        "latitude": 0.101,
                        "sog": held_out_speed,
                        "bearing_deg": held_out_bearing,
                        "cog": "",
                        "turn_rate_deg_per_min": 35.0,
                        "accel_kn_per_min": 4.0,
                        "implied_sog_kn": held_out_speed,
                    }
                ],
            )

        with tempfile.TemporaryDirectory() as left_tmp, tempfile.TemporaryDirectory() as right_tmp:
            left = Path(left_tmp)
            right = Path(right_tmp)
            populate(left, held_out_bearing=90.0, held_out_speed=5.0)
            populate(right, held_out_bearing=270.0, held_out_speed=20.0)

            kwargs = {
                "training_dates": training_dates,
                "held_out_date": held_out,
                "dataset_prefix": "fixture",
                "bbox": bbox,
                "cell_size_deg": 0.1,
                "pattern_min_sog": 1.0,
                "pattern_min_points": 1,
                "pattern_min_tracks": 1,
                "track_min_points": 20,
                "moving_sog": 1.0,
                "min_anomaly_score": 0.35,
            }
            left_fold = ranking.build_fold_specific_behavior_evidence(processed_dir=left, **kwargs)
            right_fold = ranking.build_fold_specific_behavior_evidence(processed_dir=right, **kwargs)

        self.assertEqual(
            left_fold["provenance"]["pattern_model_sha256"],
            right_fold["provenance"]["pattern_model_sha256"],
        )
        self.assertEqual(
            left_fold["provenance"]["training_behavior_evidence_sha256"],
            right_fold["provenance"]["training_behavior_evidence_sha256"],
        )
        self.assertEqual(
            left_fold["provenance"]["training_behavior_targets_sha256"],
            right_fold["provenance"]["training_behavior_targets_sha256"],
        )
        self.assertEqual(
            left_fold["provenance"]["pattern_stats"],
            right_fold["provenance"]["pattern_stats"],
        )
        self.assertNotEqual(
            left_fold["provenance"]["held_out_behavior_evidence_sha256"],
            right_fold["provenance"]["held_out_behavior_evidence_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
