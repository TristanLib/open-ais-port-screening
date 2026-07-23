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

    def test_control_exclusion_and_thinning_continue_across_utc_midnight(self) -> None:
        midnight = dt.datetime(2025, 5, 2, 0, 0)
        raw_rows = [
            # Candidate episodes intentionally retain the pair/day boundary.
            self.opportunity(
                midnight - dt.timedelta(minutes=1),
                "111",
                "222",
                is_candidate=True,
            ),
            # The prior-day control is 14 minutes before this candidate and
            # must be excluded even though the UTC date differs.
            self.opportunity(
                midnight + dt.timedelta(minutes=4),
                "111",
                "222",
                is_candidate=True,
            ),
            self.opportunity(
                midnight - dt.timedelta(minutes=10),
                "111",
                "222",
                is_candidate=False,
            ),
            # Thinning is also pair-continuous across midnight.
            self.opportunity(
                midnight - dt.timedelta(minutes=2),
                "333",
                "444",
                is_candidate=False,
            ),
            self.opportunity(
                midnight + dt.timedelta(minutes=5),
                "333",
                "444",
                is_candidate=False,
            ),
            self.opportunity(
                midnight + dt.timedelta(minutes=14),
                "333",
                "444",
                is_candidate=False,
            ),
        ]

        candidates, potential_controls, audit = controls.build_anchor_cohorts(
            controls.normalize_opportunity_rows(raw_rows)
        )

        self.assertEqual(len(candidates), 2)
        self.assertEqual(
            [row["reference_time"] for row in potential_controls],
            [midnight - dt.timedelta(minutes=2), midnight + dt.timedelta(minutes=14)],
        )
        self.assertEqual(audit["control_records_excluded_near_candidate"], 1)
        self.assertEqual(audit["control_records_removed_by_thinning"], 1)
        self.assertEqual(
            audit["control_exclusion_and_thinning_grouping"],
            "vessel_pair_continuous_across_utc_dates",
        )

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

    def test_primary_matching_applies_frozen_calipers_and_normalized_objective(self) -> None:
        base = dt.datetime(2025, 5, 1, 12, 0)
        raw_rows = [
            self.opportunity(
                base,
                "101",
                "102",
                is_candidate=True,
                current_distance_nm=0.75,
            ),
            # Closest by current distance, but farther under the six-term
            # normalized objective.
            self.opportunity(
                base + dt.timedelta(minutes=50),
                "201",
                "202",
                is_candidate=False,
                current_distance_nm=0.76,
            ),
            # Farther in current distance but much closer in time and motion.
            self.opportunity(
                base + dt.timedelta(minutes=10),
                "203",
                "204",
                is_candidate=False,
                current_distance_nm=0.95,
            ),
            # This otherwise attractive control violates the relative-speed
            # caliper by 0.1 kn and must be ineligible.
            self.opportunity(
                base + dt.timedelta(minutes=1),
                "205",
                "206",
                is_candidate=False,
                current_distance_nm=0.751,
            ),
        ]
        raw_rows[1]["relative_speed_kn"] = 12.9
        raw_rows[1]["closing_speed_kn"] = 6.4
        raw_rows[2]["relative_speed_kn"] = 8.1
        raw_rows[2]["closing_speed_kn"] = 4.1
        raw_rows[3]["relative_speed_kn"] = 13.1

        candidates, potential_controls, _ = controls.build_anchor_cohorts(
            controls.normalize_opportunity_rows(raw_rows)
        )
        matches, audit = controls.deterministic_exact_match(candidates, potential_controls)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["control"]["mmsi_a"], "203")
        self.assertLess(float(matches[0]["normalized_matching_distance"]), 1.0)
        self.assertEqual(audit["reference_time_caliper_min"], 60.0)
        self.assertEqual(audit["relative_speed_caliper_kn"], 5.0)
        self.assertEqual(audit["closing_speed_caliper_kn"], 2.5)
        self.assertEqual(
            audit["normalized_nearest_dimensions"],
            [
                "reference_time",
                "current_distance",
                "relative_speed",
                "closing_speed",
                "state_skew",
                "local_exposure",
            ],
        )

    def test_primary_matching_excludes_missing_required_values_and_reports_flow(self) -> None:
        base = dt.datetime(2025, 5, 1, 12, 0)
        raw_rows = [
            self.opportunity(base, "101", "102", is_candidate=True),
            self.opportunity(base + dt.timedelta(minutes=1), "201", "202", is_candidate=False),
        ]
        raw_rows[0]["state_skew_s"] = ""

        candidates, potential_controls, _ = controls.build_anchor_cohorts(
            controls.normalize_opportunity_rows(raw_rows)
        )
        matches, audit = controls.deterministic_exact_match(candidates, potential_controls)

        self.assertEqual(matches, [])
        self.assertEqual(audit["candidate_anchors_missing_primary_matching_values"], 1)
        self.assertEqual(
            audit["candidate_missing_primary_matching_values_by_field"]["state_skew_s"],
            1,
        )
        self.assertEqual(audit["candidate_anchors_eligible_for_primary_matching"], 0)

    def test_primary_matching_calipers_are_inclusive_and_individually_enforced(self) -> None:
        base = dt.datetime(2025, 5, 1, 12, 0)

        def matches_for(
            *,
            minute: float,
            relative_speed: float,
            closing_speed: float,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            candidate = self.opportunity(base, "101", "102", is_candidate=True)
            control = self.opportunity(
                base + dt.timedelta(minutes=minute),
                "201",
                "202",
                is_candidate=False,
            )
            control["relative_speed_kn"] = relative_speed
            control["closing_speed_kn"] = closing_speed
            candidates, potential_controls, _ = controls.build_anchor_cohorts(
                controls.normalize_opportunity_rows([candidate, control])
            )
            return controls.deterministic_exact_match(candidates, potential_controls)

        accepted, _ = matches_for(minute=60, relative_speed=13.0, closing_speed=6.5)
        self.assertEqual(len(accepted), 1)

        for label, values in (
            ("reference_time", {"minute": 61, "relative_speed": 8.0, "closing_speed": 4.0}),
            ("relative_speed", {"minute": 1, "relative_speed": 13.01, "closing_speed": 4.0}),
            ("closing_speed", {"minute": 1, "relative_speed": 8.0, "closing_speed": 6.51}),
        ):
            with self.subTest(caliper=label):
                rejected, audit = matches_for(**values)
                self.assertEqual(rejected, [])
                self.assertEqual(
                    audit["unmatched_eligible_candidates_without_caliper_eligible_control"],
                    1,
                )


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
        self.assertNotIn("full_cohort_capture", report_one)
        selected = report_one["selected_anchor_hit_composition"]["within_0_5_nm"]
        self.assertEqual(selected["candidate_share_of_selected_anchor_outcomes"], 1.0)
        self.assertFalse(selected["is_all_opportunity_capture_or_recall"])
        self.assertEqual(
            selected["cluster_bootstrap_95_ci"][
                "outcome_vessel_pair_day_clusters"
            ],
            1,
        )
        self.assertIn("schema_migration", report_one)
        self.assertEqual(
            report_one["matching"]["matched_candidate_anchors_observable"],
            1,
        )
        self.assertEqual(
            report_one["matching"]["matched_control_anchors_observable"],
            1,
        )
        self.assertEqual(
            report_one["matching_balance"]["post_match"]["pair_count"],
            1,
        )
        bootstrap = half_nm["cluster_bootstrap_95_ci"]
        self.assertEqual(bootstrap["dependency_components"], 1)
        self.assertEqual(
            bootstrap["dependency_unit"],
            "connected_component_of_physical_vessel_pair_day_match_graph",
        )
        self.assertEqual(
            report_one["matched_outcomes"]["within_0_5_nm"]["cluster_bootstrap_95_ci"],
            report_two["matched_outcomes"]["within_0_5_nm"]["cluster_bootstrap_95_ci"],
        )
        self.assertEqual(
            set(report_one["calibration"]),
            {"predicted_dcpa_nm", "predicted_tcpa_min", "state_skew_s", "current_distance_nm"},
        )

    def test_balance_reports_pre_post_smd_and_joint_observable_flow(self) -> None:
        base = dt.datetime(2025, 5, 1, 12, 0)

        def anchor(
            anchor_id: str,
            anchor_type: str,
            mmsi_a: str,
            mmsi_b: str,
            relative_speed: float,
            reference_minute: int,
        ) -> dict[str, object]:
            return {
                "anchor_id": anchor_id,
                "anchor_type": anchor_type,
                "date": "2025-05-01",
                "reference_time": base + dt.timedelta(minutes=reference_minute),
                "mmsi_a": mmsi_a,
                "mmsi_b": mmsi_b,
                "current_distance_nm": 0.75,
                "state_skew_s": 10.0,
                "relative_speed_kn": relative_speed,
                "closing_speed_kn": 4.0,
                "local_opportunity_exposure": 20,
            }

        candidates = [
            anchor("c1", "candidate", "101", "102", 1.0, 0),
            anchor("c2", "candidate", "103", "104", 3.0, 20),
        ]
        controls_only = [
            anchor("k1", "control", "201", "202", 1.0, 5),
            anchor("k2", "control", "201", "202", 1.0, 25),
        ]
        matches = [
            {"match_id": "m1", "candidate": candidates[0], "control": controls_only[0]},
            {"match_id": "m2", "candidate": candidates[1], "control": controls_only[1]},
        ]
        evaluated = [
            {
                **row,
                "observable_followup": int(row["anchor_id"] != "k2"),
                "actual_min_distance_nm": 0.4,
            }
            for row in candidates + controls_only
        ]

        report = controls.summarize_evaluation(
            candidates,
            controls_only,
            matches,
            evaluated,
            bootstrap_iterations=10,
        )

        flow = report["matching"]
        self.assertEqual(flow["matched_candidate_anchors_observable"], 2)
        self.assertEqual(flow["matched_control_anchors_observable"], 1)
        self.assertEqual(flow["matched_pairs_with_both_strict_future_observable"], 1)
        self.assertEqual(flow["matched_pairs_candidate_only_observable"], 1)
        self.assertEqual(flow["matched_pairs_control_only_observable"], 0)
        relative = report["matching_balance"]["post_match"]["variables"]["relative_speed_kn"]
        self.assertEqual(relative["complete_candidate_count"], 2)
        self.assertEqual(relative["complete_control_count"], 2)
        self.assertAlmostEqual(relative["standardized_mean_difference"], 1.0)

    def test_bootstrap_components_preserve_both_sides_pair_day_dependence(self) -> None:
        base = dt.datetime(2025, 5, 1, 12, 0)
        candidates = [
            {
                "anchor_id": "c1",
                "anchor_type": "candidate",
                "date": "2025-05-01",
                "reference_time": base,
                "mmsi_a": "101",
                "mmsi_b": "102",
                "current_distance_nm": 0.8,
                "state_skew_s": 10.0,
                "relative_speed_kn": 8.0,
                "closing_speed_kn": 4.0,
                "local_opportunity_exposure": 10,
            },
            {
                "anchor_id": "c2",
                "anchor_type": "candidate",
                "date": "2025-05-01",
                "reference_time": base + dt.timedelta(minutes=20),
                "mmsi_a": "103",
                "mmsi_b": "104",
                "current_distance_nm": 0.8,
                "state_skew_s": 10.0,
                "relative_speed_kn": 8.0,
                "closing_speed_kn": 4.0,
                "local_opportunity_exposure": 10,
            },
        ]
        controls_only = [
            {
                **candidates[0],
                "anchor_id": "k1",
                "anchor_type": "control",
                "mmsi_a": "201",
                "mmsi_b": "202",
            },
            {
                **candidates[1],
                "anchor_id": "k2",
                "anchor_type": "control",
                "mmsi_a": "201",
                "mmsi_b": "202",
            },
        ]
        matches = [
            {"match_id": "m1", "candidate": candidates[0], "control": controls_only[0]},
            {"match_id": "m2", "candidate": candidates[1], "control": controls_only[1]},
        ]
        evaluated = [
            {**row, "observable_followup": 1, "actual_min_distance_nm": 0.4}
            for row in candidates + controls_only
        ]

        report = controls.summarize_evaluation(
            candidates,
            controls_only,
            matches,
            evaluated,
            bootstrap_iterations=10,
        )
        bootstrap = report["matched_outcomes"]["within_0_5_nm"][
            "cluster_bootstrap_95_ci"
        ]

        self.assertEqual(bootstrap["candidate_vessel_pair_day_nodes"], 2)
        self.assertEqual(bootstrap["control_vessel_pair_day_nodes"], 1)
        self.assertEqual(bootstrap["dependency_components"], 1)
        self.assertEqual(bootstrap["matched_set_edges"], 2)

    def test_bootstrap_unifies_same_physical_pair_day_across_match_roles(self) -> None:
        base = dt.datetime(2025, 5, 1, 12, 0)

        def anchor(
            anchor_id: str,
            anchor_type: str,
            mmsi_a: str,
            mmsi_b: str,
            minute: int,
        ) -> dict[str, object]:
            return {
                "anchor_id": anchor_id,
                "anchor_type": anchor_type,
                "date": "2025-05-01",
                "reference_time": base + dt.timedelta(minutes=minute),
                "mmsi_a": mmsi_a,
                "mmsi_b": mmsi_b,
                "current_distance_nm": 0.8,
                "state_skew_s": 10.0,
                "relative_speed_kn": 8.0,
                "closing_speed_kn": 4.0,
                "local_opportunity_exposure": 10,
            }

        candidates = [
            anchor("c1", "candidate", "101", "102", 0),
            anchor("c2", "candidate", "103", "104", 20),
        ]
        controls_only = [
            anchor("k1", "control", "201", "202", 5),
            # The reversed MMSI order represents the same physical pair-day
            # as c1, despite appearing here in the control role.
            anchor("k2", "control", "102", "101", 25),
        ]
        matches = [
            {"match_id": "m1", "candidate": candidates[0], "control": controls_only[0]},
            {"match_id": "m2", "candidate": candidates[1], "control": controls_only[1]},
        ]
        evaluated = [
            {**row, "observable_followup": 1, "actual_min_distance_nm": 0.4}
            for row in candidates + controls_only
        ]

        report = controls.summarize_evaluation(
            candidates,
            controls_only,
            matches,
            evaluated,
            bootstrap_iterations=10,
        )
        bootstrap = report["matched_outcomes"]["within_0_5_nm"][
            "cluster_bootstrap_95_ci"
        ]

        self.assertEqual(bootstrap["dependency_components"], 1)
        self.assertEqual(bootstrap["physical_vessel_pair_day_nodes"], 3)
        self.assertEqual(bootstrap["cross_role_reused_vessel_pair_day_nodes"], 1)
        self.assertEqual(bootstrap["matched_set_edges"], 2)

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
