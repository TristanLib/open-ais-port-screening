from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import encounter_backtest
import geometric_control_evaluation as controls


class GeometricControlCohortTest(unittest.TestCase):
    def opportunity(
        self,
        timestamp: dt.datetime,
        mmsi_a: str,
        mmsi_b: str,
        *,
        is_candidate: bool,
        current_distance_nm: float = 0.75,
        dcpa_nm: float = 0.25,
        tcpa_min: float = 5.0,
        lon_mid: float = -122.40,
        lat_mid: float = 37.80,
    ) -> dict[str, object]:
        return {
            "date": timestamp.date().isoformat(),
            "reference_time": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "mmsi_a": mmsi_a,
            "mmsi_b": mmsi_b,
            "current_distance_nm": current_distance_nm,
            "dcpa_nm": dcpa_nm,
            "tcpa_min": tcpa_min,
            "is_candidate": int(is_candidate),
            "analysis_cell_id": "r1_c1",
            "lon_mid": lon_mid,
            "lat_mid": lat_mid,
            "state_skew_s": 12.0,
            "relative_speed_kn": 8.0,
            "closing_speed_kn": 4.0,
        }

    def test_current_opportunity_schema_aliases_are_normalized(self) -> None:
        timestamp = dt.datetime(2025, 5, 1, 12, 0)
        raw = self.opportunity(timestamp, "222", "111", is_candidate=True)
        raw.pop("analysis_cell_id")
        raw.pop("lon_mid")
        raw.pop("lat_mid")
        raw.pop("state_skew_s")
        raw.update(
            {
                "cell_id": "r7_c8",
                "source_state_skew_s": "17.5",
                "lon_a": "-122.42",
                "lon_b": "-122.38",
                "lat_a": "37.79",
                "lat_b": "37.81",
            }
        )

        rows = controls.normalize_opportunity_rows([raw])

        self.assertEqual((rows[0]["mmsi_a"], rows[0]["mmsi_b"]), ("111", "222"))
        self.assertEqual(rows[0]["analysis_cell_id"], "r7_c8")
        self.assertEqual(rows[0]["state_skew_s"], 17.5)
        self.assertAlmostEqual(float(rows[0]["lon_mid"]), -122.40)
        self.assertAlmostEqual(float(rows[0]["lat_mid"]), 37.80)

    def test_candidate_episode_and_control_exclusion_thinning_rules(self) -> None:
        base = dt.datetime(2025, 5, 1, 12, 0)
        raw_rows = [
            self.opportunity(base, "111", "222", is_candidate=True),
            self.opportunity(base + dt.timedelta(minutes=10), "111", "222", is_candidate=True),
            self.opportunity(base + dt.timedelta(minutes=26), "111", "222", is_candidate=True),
            # Exactly 15 minutes before a candidate is excluded; 16 minutes is retained.
            self.opportunity(base - dt.timedelta(minutes=15), "111", "222", is_candidate=False),
            self.opportunity(base - dt.timedelta(minutes=16), "111", "222", is_candidate=False),
            self.opportunity(base + dt.timedelta(minutes=40), "111", "222", is_candidate=False),
            self.opportunity(base, "333", "444", is_candidate=False),
            self.opportunity(base + dt.timedelta(minutes=10), "333", "444", is_candidate=False),
            self.opportunity(base + dt.timedelta(minutes=15), "333", "444", is_candidate=False),
            self.opportunity(base + dt.timedelta(minutes=31), "333", "444", is_candidate=False),
        ]

        candidates, potential_controls, audit = controls.build_anchor_cohorts(
            controls.normalize_opportunity_rows(raw_rows)
        )

        self.assertEqual(
            [row["reference_time"] for row in candidates],
            [base, base + dt.timedelta(minutes=26)],
        )
        self.assertEqual(int(candidates[0]["episode_record_count"]), 2)
        self.assertEqual(
            [row["reference_time"] for row in potential_controls],
            [
                base - dt.timedelta(minutes=16),
                base,
                base + dt.timedelta(minutes=15),
                base + dt.timedelta(minutes=31),
            ],
        )
        self.assertEqual(audit["control_records_excluded_near_candidate"], 2)
        self.assertEqual(audit["control_records_removed_by_thinning"], 1)

    def test_local_exposure_tertile_uses_analysis_cells_inside_coarse_spatial_block(self) -> None:
        base = dt.datetime(2025, 5, 1, 12, 0)
        raw_rows = [
            self.opportunity(base, "101", "102", is_candidate=True),
            self.opportunity(base + dt.timedelta(minutes=1), "201", "202", is_candidate=False),
            self.opportunity(base + dt.timedelta(minutes=2), "203", "204", is_candidate=False),
            self.opportunity(base + dt.timedelta(minutes=3), "205", "206", is_candidate=False),
        ]
        raw_rows[0]["analysis_cell_id"] = "fine-low"
        for row in raw_rows[1:]:
            row["analysis_cell_id"] = "fine-high"

        prepared = controls.assign_matching_strata(
            controls.normalize_opportunity_rows(raw_rows)
        )

        self.assertEqual(
            {(row["spatial_block_lon"], row["spatial_block_lat"]) for row in prepared},
            {(prepared[0]["spatial_block_lon"], prepared[0]["spatial_block_lat"])},
        )
        self.assertNotEqual(prepared[0]["local_exposure_tertile"], prepared[1]["local_exposure_tertile"])
        self.assertEqual(prepared[0]["local_opportunity_exposure"], 1)
        self.assertEqual(prepared[1]["local_opportunity_exposure"], 3)

    def test_exact_stratum_matching_is_nearest_deterministic_and_without_replacement(self) -> None:
        base = dt.datetime(2025, 5, 1, 12, 0)
        raw_rows = [
            self.opportunity(base, "101", "102", is_candidate=True, current_distance_nm=0.55),
            self.opportunity(base + dt.timedelta(minutes=1), "103", "104", is_candidate=True, current_distance_nm=0.95),
            self.opportunity(base + dt.timedelta(hours=6), "105", "106", is_candidate=True, current_distance_nm=0.75),
            self.opportunity(base + dt.timedelta(minutes=2), "201", "202", is_candidate=False, current_distance_nm=0.56),
            self.opportunity(base + dt.timedelta(minutes=3), "203", "204", is_candidate=False, current_distance_nm=0.94),
        ]
        candidates, potential_controls, _ = controls.build_anchor_cohorts(
            controls.normalize_opportunity_rows(raw_rows)
        )

        matches, audit = controls.deterministic_exact_match(candidates, potential_controls)

        self.assertEqual(len(matches), 2)
        self.assertEqual(audit["unmatched_candidate_anchors"], 1)
        self.assertEqual(
            [float(match["control"]["current_distance_nm"]) for match in matches],
            [0.56, 0.94],
        )
        self.assertEqual(len({match["control"]["anchor_id"] for match in matches}), 2)


class GeometricControlOutcomeTest(unittest.TestCase):
    def position_series(
        self,
        t0: dt.datetime,
        lon: float,
        mmsi: str,
    ) -> list[encounter_backtest.Position]:
        return [
            encounter_backtest.Position(
                t0 + dt.timedelta(seconds=seconds),
                lon,
                0.0,
                f"{mmsi}-track-1",
            )
            for seconds in range(30, 901, 30)
        ]

    def test_identical_strict_future_evaluator_and_cluster_bootstrap_report(self) -> None:
        t0 = dt.datetime(2025, 5, 1, 12, 0)
        raw_rows = [
            {
                "date": "2025-05-01",
                "reference_time": "2025-05-01 12:00:00",
                "mmsi_a": "111",
                "mmsi_b": "222",
                "current_distance_nm": 0.75,
                "dcpa_nm": 0.20,
                "tcpa_min": 5.0,
                "is_candidate": 1,
                "cell_id": "r1_c1",
                "lon_mid": -122.40,
                "lat_mid": 37.80,
                "source_state_skew_s": 10,
                "relative_speed_kn": 8,
                "closing_speed_kn": 4,
            },
            {
                "date": "2025-05-01",
                "reference_time": "2025-05-01 12:00:00",
                "mmsi_a": "333",
                "mmsi_b": "444",
                "current_distance_nm": 0.75,
                "dcpa_nm": 0.80,
                "tcpa_min": 5.0,
                "is_candidate": 0,
                "cell_id": "r1_c1",
                "lon_mid": -122.40,
                "lat_mid": 37.80,
                "source_state_skew_s": 10,
                "relative_speed_kn": 8,
                "closing_speed_kn": 4,
            },
        ]
        candidates, potential_controls, _ = controls.build_anchor_cohorts(
            controls.normalize_opportunity_rows(raw_rows)
        )
        matches, matching_audit = controls.deterministic_exact_match(candidates, potential_controls)
        positions = {
            "111": self.position_series(t0, 0.0000, "111"),
            "222": self.position_series(t0, 0.0010, "222"),
            "333": self.position_series(t0, 0.0000, "333"),
            "444": self.position_series(t0, 0.0200, "444"),
        }

        evaluated = controls.evaluate_anchors(candidates + potential_controls, positions)
        report_one = controls.summarize_evaluation(
            candidates,
            potential_controls,
            matches,
            evaluated,
            matching_audit=matching_audit,
            bootstrap_iterations=100,
            bootstrap_seed=20260722,
        )
        report_two = controls.summarize_evaluation(
            candidates,
            potential_controls,
            matches,
            evaluated,
            matching_audit=matching_audit,
            bootstrap_iterations=100,
            bootstrap_seed=20260722,
        )

        self.assertTrue(all(int(row["observable_followup"]) == 1 for row in evaluated))
        half_nm = report_one["matched_outcomes"]["within_0_5_nm"]
        self.assertEqual(half_nm["candidate_rate"], 1.0)
        self.assertEqual(half_nm["control_rate"], 0.0)
        self.assertEqual(half_nm["risk_difference"], 1.0)
        self.assertIsNone(half_nm["lift"])
        self.assertTrue(half_nm["control_rate_zero"])
        self.assertEqual(report_one["full_cohort_capture"]["within_0_5_nm"]["capture_rate"], 1.0)
        self.assertEqual(
            report_one["matched_outcomes"]["within_0_5_nm"]["cluster_bootstrap_95_ci"],
            report_two["matched_outcomes"]["within_0_5_nm"]["cluster_bootstrap_95_ci"],
        )
        self.assertEqual(
            set(report_one["calibration"]),
            {"predicted_dcpa_nm", "predicted_tcpa_min", "state_skew_s", "current_distance_nm"},
        )

    def test_undefined_control_tcpa_does_not_define_future_track_observability(self) -> None:
        t0 = dt.datetime(2025, 5, 1, 12, 0)
        normalized = controls.normalize_opportunity_rows(
            [
                {
                    "date": "2025-05-01",
                    "reference_time": "2025-05-01 12:00:00",
                    "mmsi_a": "333",
                    "mmsi_b": "444",
                    "current_distance_nm": 0.75,
                    "dcpa_nm": 0.80,
                    "tcpa_min": "",
                    "is_candidate": 0,
                    "cell_id": "r1_c1",
                    "lon_mid": -122.40,
                    "lat_mid": 37.80,
                    "source_state_skew_s": 10,
                }
            ]
        )
        _, controls_only, _ = controls.build_anchor_cohorts(normalized)
        evaluated = controls.evaluate_anchors(
            controls_only,
            {
                "333": self.position_series(t0, 0.0000, "333"),
                "444": self.position_series(t0, 0.0200, "444"),
            },
        )[0]

        self.assertEqual(evaluated["predicted_closest_time_defined"], 0)
        self.assertEqual(evaluated["predicted_time_error_eligible"], 0)
        self.assertEqual(evaluated["observable_followup"], 1)
        self.assertIn("future_geometry_observable", evaluated["backtest_status"])


if __name__ == "__main__":
    unittest.main()
