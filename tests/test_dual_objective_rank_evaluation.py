from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dual_objective_rank_evaluation as ranking


class DualObjectiveRankingTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
