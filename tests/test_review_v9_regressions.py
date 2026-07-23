from __future__ import annotations

import csv
import datetime as dt
import math
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import encounter_backtest
import encounter_risk
from features import build_features


def brute_force_pairs(states: list[dict[str, object]], threshold_nm: float) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for index, left in enumerate(states):
        for right in states[index + 1 :]:
            distance = encounter_risk.distance_nm(
                float(left["lon"]),
                float(left["lat"]),
                float(right["lon"]),
                float(right["lat"]),
            )
            if distance <= threshold_nm:
                pairs.add(tuple(sorted((str(left["mmsi"]), str(right["mmsi"])))))
    return pairs


class CompletePairConstructionTest(unittest.TestCase):
    def test_dynamic_index_exactly_matches_brute_force_at_density_and_bucket_edges(self) -> None:
        states: list[dict[str, object]] = [
            # These two are about 1.9 nm apart at San Francisco latitude but
            # fall three negative-longitude bucket indices apart with floor().
            {"mmsi": "edge-west", "lon": -122.4201, "lat": 37.8000},
            {"mmsi": "edge-east", "lon": -122.3799, "lat": 37.8000},
            # Latitude-boundary fixture plus a compact high-density cluster.
            {"mmsi": "lat-south", "lon": -122.4000, "lat": 37.7799},
            {"mmsi": "lat-north", "lon": -122.4000, "lat": 37.8101},
        ]
        for index in range(16):
            states.append(
                {
                    "mmsi": f"dense-{index:02d}",
                    "lon": -122.402 + (index % 4) * 0.001,
                    "lat": 37.798 + (index // 4) * 0.001,
                }
            )

        expected = brute_force_pairs(states, threshold_nm=2.0)
        actual = encounter_risk.indexed_pairs_within_distance(
            states,
            max_distance_nm=2.0,
            spatial_cell_deg=0.02,
        )

        self.assertEqual(actual, expected)
        self.assertIn(("edge-east", "edge-west"), actual)

    def test_causal_grid_pairs_reports_across_a_floor_minute_boundary(self) -> None:
        rows = [
            {
                "mmsi": "111",
                "timestamp": dt.datetime(2025, 5, 1, 12, 0, 59),
                "lon": -122.4000,
                "lat": 37.8000,
                "sog": 5.0,
                "bearing": 90.0,
                "track_id": "111_0001",
            },
            {
                "mmsi": "222",
                "timestamp": dt.datetime(2025, 5, 1, 12, 1, 0),
                "lon": -122.3990,
                "lat": 37.8000,
                "sog": 5.0,
                "bearing": 270.0,
                "track_id": "222_0001",
            },
        ]

        bins = encounter_risk.build_causal_grid_states(rows, time_bin_seconds=60, max_state_age_s=60)
        grid_time = dt.datetime(2025, 5, 1, 12, 1, 0)

        self.assertEqual(set(bins[grid_time]), {"111", "222"})
        self.assertEqual(bins[grid_time]["111"]["source_timestamp"], rows[0]["timestamp"])
        self.assertEqual(bins[grid_time]["222"]["source_timestamp"], rows[1]["timestamp"])
        self.assertEqual(bins[grid_time]["111"]["timestamp"], grid_time)

    def test_causal_grid_never_uses_future_and_keeps_latest_eligible_state(self) -> None:
        rows = [
            {"mmsi": "111", "timestamp": dt.datetime(2025, 5, 1, 12, 0, 0), "lon": 0.0, "lat": 0.0, "sog": 1.0, "bearing": 90.0},
            {"mmsi": "111", "timestamp": dt.datetime(2025, 5, 1, 12, 0, 1), "lon": 0.001, "lat": 0.0, "sog": 1.0, "bearing": 90.0},
            {"mmsi": "222", "timestamp": dt.datetime(2025, 5, 1, 12, 1, 1), "lon": 0.002, "lat": 0.0, "sog": 1.0, "bearing": 90.0},
        ]

        bins = encounter_risk.build_causal_grid_states(rows, 60, 60)
        at_1201 = bins[dt.datetime(2025, 5, 1, 12, 1, 0)]

        self.assertEqual(at_1201["111"]["source_timestamp"], dt.datetime(2025, 5, 1, 12, 0, 1))
        self.assertEqual(at_1201["111"]["state_age_s"], 59.0)
        self.assertNotIn("222", at_1201)
        self.assertNotIn("111", bins.get(dt.datetime(2025, 5, 1, 12, 2, 0), {}))

    def test_newest_directionless_segment_state_masks_older_segment_course(self) -> None:
        rows = [
            {"mmsi": "111", "timestamp": dt.datetime(2025, 5, 1, 12, 0, 30), "lon": 0.0, "lat": 0.0, "sog": 5.0, "bearing": 90.0, "track_id": "111_0001"},
            {"mmsi": "111", "timestamp": dt.datetime(2025, 5, 1, 12, 0, 59), "lon": 1.0, "lat": 1.0, "sog": 5.0, "bearing": None, "track_id": "111_0002"},
        ]

        bins = encounter_risk.build_causal_grid_states(rows, 60, 60)

        self.assertNotIn("111", bins.get(dt.datetime(2025, 5, 1, 12, 1, 0), {}))

    def test_compute_encounters_integrates_minute_boundary_and_far_bucket_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processed = root / "processed"
            processed.mkdir()
            feature_path = processed / "fixture_2025-05-01_features.csv"
            fields = [
                "mmsi",
                "base_date_time",
                "longitude",
                "latitude",
                "sog",
                "cog",
                "bearing_deg",
                "track_id",
                "point_index",
                "segment_break_reason",
            ]
            with feature_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(
                    [
                        {"mmsi": "111", "base_date_time": "2025-05-01 12:00:59", "longitude": "-122.4201", "latitude": "37.8", "sog": "20", "cog": "90", "bearing_deg": "", "track_id": "111_0001", "point_index": "0", "segment_break_reason": "new_mmsi"},
                        {"mmsi": "222", "base_date_time": "2025-05-01 12:01:00", "longitude": "-122.3799", "latitude": "37.8", "sog": "20", "cog": "270", "bearing_deg": "", "track_id": "222_0001", "point_index": "0", "segment_break_reason": "new_mmsi"},
                    ]
                )
            encounter_csv = root / "encounters.csv"
            opportunity_csv = root / "opportunities.csv"
            opportunity_records = root / "opportunity_records.csv"
            stats_path = root / "stats.json"

            stats = encounter_risk.compute_encounters(
                start=dt.date(2025, 5, 1),
                end=dt.date(2025, 5, 1),
                processed_dir=processed,
                output_csv=encounter_csv,
                output_geojson=root / "encounters.geojson",
                stats_json=stats_path,
                time_bin_seconds=60,
                spatial_cell_deg=0.02,
                min_sog=1.0,
                max_current_distance_nm=2.0,
                dcpa_threshold_nm=0.5,
                tcpa_threshold_min=15.0,
                geojson_limit=10,
                dataset_prefix="fixture",
                max_state_skew_s=60,
                opportunity_csv=opportunity_csv,
                opportunity_records_csv=opportunity_records,
                analysis_bbox=(-123.0, 37.0, -122.0, 38.5),
                analysis_cell_size_deg=0.005,
            )
            with opportunity_records.open("r", encoding="utf-8", newline="") as handle:
                records = list(csv.DictReader(handle))

        self.assertEqual(stats["pair_checks_within_distance"], 1)
        self.assertEqual(stats["encounters"], 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["reference_time"], "2025-05-01 12:01:00")
        self.assertEqual(records[0]["state_age_s_a"], "1.0")
        self.assertEqual(records[0]["state_age_s_b"], "0.0")
        self.assertEqual(records[0]["is_candidate"], "1")


class SegmentDirectionTest(unittest.TestCase):
    def test_segment_first_points_never_keep_cross_break_course(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clean.csv"
            output = root / "features.csv"
            stats = root / "stats.json"
            fieldnames = ["mmsi", "base_date_time", "longitude", "latitude", "sog", "cog"]
            rows = [
                {"mmsi": "111", "base_date_time": "2025-05-01 12:00:00", "longitude": "0", "latitude": "0", "sog": "5", "cog": "90"},
                {"mmsi": "111", "base_date_time": "2025-05-01 12:01:00", "longitude": "0.001", "latitude": "0", "sog": "5", "cog": "90"},
                # Long gap creates a new segment. Its cross-gap eastbound line must be discarded.
                {"mmsi": "111", "base_date_time": "2025-05-01 13:00:00", "longitude": "0.100", "latitude": "0", "sog": "5", "cog": "180"},
                {"mmsi": "111", "base_date_time": "2025-05-01 13:01:00", "longitude": "0.100", "latitude": "0.001", "sog": "5", "cog": "180"},
                {"mmsi": "222", "base_date_time": "2025-05-01 12:00:00", "longitude": "0", "latitude": "0", "sog": "5", "cog": "90"},
                {"mmsi": "222", "base_date_time": "2025-05-01 12:01:00", "longitude": "0.100", "latitude": "0", "sog": "5", "cog": "90"},
                {"mmsi": "333", "base_date_time": "2025-05-01 12:00:00", "longitude": "0", "latitude": "0", "sog": "5", "cog": "90"},
                {"mmsi": "333", "base_date_time": "2025-05-01 12:00:01", "longitude": "0.010", "latitude": "0", "sog": "5", "cog": "90"},
            ]
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            build_features(source, output, stats, 1800, 5.0, 60.0)
            with output.open("r", encoding="utf-8", newline="") as handle:
                result = list(csv.DictReader(handle))

        keyed = {(row["mmsi"], row["base_date_time"]): row for row in result}
        self.assertEqual(keyed[("111", "2025-05-01 12:00:00")]["bearing_deg"], "")
        self.assertNotEqual(keyed[("111", "2025-05-01 12:01:00")]["bearing_deg"], "")
        self.assertEqual(keyed[("111", "2025-05-01 13:00:00")]["segment_break_reason"], "time_gap")
        self.assertEqual(keyed[("111", "2025-05-01 13:00:00")]["bearing_deg"], "")
        self.assertAlmostEqual(float(keyed[("111", "2025-05-01 13:01:00")]["bearing_deg"]), 0.0, places=3)
        self.assertEqual(keyed[("222", "2025-05-01 12:01:00")]["segment_break_reason"], "distance_gap")
        self.assertEqual(keyed[("222", "2025-05-01 12:01:00")]["bearing_deg"], "")
        self.assertEqual(keyed[("333", "2025-05-01 12:00:01")]["segment_break_reason"], "implied_speed_gap")
        self.assertEqual(keyed[("333", "2025-05-01 12:00:01")]["bearing_deg"], "")

    def test_course_selector_ignores_cross_break_derived_value(self) -> None:
        sf_break_row = {
            "bearing_deg": "90",
            "cog": "180",
            "segment_break_reason": "distance_gap",
            "point_index": "0",
        }
        tokyo_break_row = {
            "bearing_deg": "90",
            "cog": "",
            "segment_break_reason": "implied_speed_gap",
            "point_index": "0",
        }

        self.assertEqual(encounter_risk.select_ground_track_course(sf_break_row), (180.0, "native_cog_fallback"))
        self.assertEqual(encounter_risk.select_ground_track_course(tokyo_break_row), (None, "unavailable"))


class StrictFutureContinuityTest(unittest.TestCase):
    def position(
        self,
        timestamp: dt.datetime,
        lon: float,
        lat: float,
        track_id: str,
    ) -> encounter_backtest.Position:
        return encounter_backtest.Position(timestamp, lon, lat, track_id)

    def episode(self, t0: dt.datetime, predicted_seconds: int = 30) -> dict[str, object]:
        return {
            "episode_id": "e1",
            "date": "2025-05-01",
            "mmsi_a": "111",
            "mmsi_b": "222",
            "start_time": t0,
            "end_time": t0,
            "record_count": 1,
            "prediction_time": t0,
            "representative_time_bin": t0,
            "predicted_closest_time": t0 + dt.timedelta(seconds=predicted_seconds),
            "predicted_dcpa_nm": 0.1,
            "predicted_tcpa_min": predicted_seconds / 60,
            "prediction_record_score": 0.5,
            "prediction_mid_lon": 0.0,
            "prediction_mid_lat": 0.0,
            "prediction_state_skew_s": 0.0,
            "max_record_score": 0.5,
            "min_record_dcpa_nm": 0.1,
            "min_current_distance_nm": 0.5,
        }

    def test_interpolation_rejects_pre_t0_post_window_and_cross_track_edges(self) -> None:
        t0 = dt.datetime(2025, 5, 1, 12, 0, 0)
        t1 = t0 + dt.timedelta(minutes=15)

        pre_t0 = [
            self.position(t0 - dt.timedelta(seconds=10), 0.0, 0.0, "a-1"),
            self.position(t0 + dt.timedelta(seconds=10), 0.01, 0.0, "a-1"),
        ]
        cross_track = [
            self.position(t0 + dt.timedelta(seconds=10), 0.0, 0.0, "a-1"),
            self.position(t0 + dt.timedelta(seconds=20), 0.01, 0.0, "a-2"),
        ]
        post_window = [
            self.position(t1 - dt.timedelta(seconds=10), 0.0, 0.0, "a-1"),
            self.position(t1 + dt.timedelta(seconds=10), 0.01, 0.0, "a-1"),
        ]
        long_gap = [
            self.position(t0 + dt.timedelta(seconds=10), 0.0, 0.0, "a-1"),
            self.position(t0 + dt.timedelta(seconds=191), 0.01, 0.0, "a-1"),
        ]

        self.assertIsNone(encounter_backtest.interpolate_position(pre_t0, t0 + dt.timedelta(seconds=5), 180, window_start=t0, window_end=t1))
        self.assertIsNone(encounter_backtest.interpolate_position(cross_track, t0 + dt.timedelta(seconds=15), 180, window_start=t0, window_end=t1))
        self.assertIsNone(encounter_backtest.interpolate_position(post_window, t1, 180, window_start=t0, window_end=t1))
        self.assertIsNone(encounter_backtest.interpolate_position(long_gap, t0 + dt.timedelta(seconds=100), 180, window_start=t0, window_end=t1))

    def test_daily_track_ids_are_qualified_before_cross_midnight_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            processed = Path(tmp)
            fields = ["mmsi", "base_date_time", "longitude", "latitude", "track_id"]
            fixtures = {
                "2025-05-01": [
                    {
                        "mmsi": "111",
                        "base_date_time": "2025-05-01 23:59:50",
                        "longitude": "0",
                        "latitude": "0",
                        "track_id": "111_0001",
                    }
                ],
                "2025-05-02": [
                    {
                        "mmsi": "111",
                        "base_date_time": "2025-05-02 00:00:10",
                        "longitude": "0.001",
                        "latitude": "0",
                        "track_id": "111_0001",
                    }
                ],
            }
            for day, rows in fixtures.items():
                path = processed / f"fixture_{day}_features.csv"
                with path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)

            loaded = encounter_backtest.load_positions(
                processed,
                dt.date(2025, 5, 1),
                dt.date(2025, 5, 1),
                lookahead_min=15,
                wanted_mmsi={"111"},
                dataset_prefix="fixture",
            )["111"]

        self.assertEqual(
            [position.track_id for position in loaded],
            ["2025-05-01::111_0001", "2025-05-02::111_0001"],
        )
        self.assertIsNone(
            encounter_backtest.interpolate_position(
                loaded,
                dt.datetime(2025, 5, 2, 0, 0, 0),
                180,
                window_start=dt.datetime(2025, 5, 1, 23, 59, 40),
                window_end=dt.datetime(2025, 5, 2, 0, 10, 0),
            )
        )

    def test_continuous_solver_finds_minimum_before_first_30_second_grid_point(self) -> None:
        t0 = dt.datetime(2025, 5, 1, 12, 0, 0)
        t1 = t0 + dt.timedelta(minutes=15)
        rows_a = [
            self.position(t0 + dt.timedelta(seconds=1), 0.0, 0.0, "a-1"),
            self.position(t0 + dt.timedelta(seconds=60), 0.0, 0.0, "a-1"),
        ]
        rows_b = [
            self.position(t0 + dt.timedelta(seconds=1), -0.01, 0.0, "b-1"),
            self.position(t0 + dt.timedelta(seconds=29), 0.01, 0.0, "b-1"),
            self.position(t0 + dt.timedelta(seconds=60), 0.02, 0.0, "b-1"),
        ]

        result = encounter_backtest.interpolated_future_min_distance(
            rows_a,
            rows_b,
            prediction_time=t0,
            window_end=t1,
            evaluation_step_s=30,
            max_interpolation_gap_s=180,
        )

        self.assertLess(float(result["actual_min_distance_nm"]), 0.001)
        self.assertLess(result["actual_min_time"], t0 + dt.timedelta(seconds=30))

    def test_one_common_future_point_is_not_primary_observable(self) -> None:
        t0 = dt.datetime(2025, 5, 1, 12, 0, 0)
        single_a = [self.position(t0 + dt.timedelta(seconds=30), 0.0, 0.0, "a-1")]
        single_b = [self.position(t0 + dt.timedelta(seconds=30), 0.001, 0.0, "b-1")]

        rows = encounter_backtest.backtest_episodes(
            [self.episode(t0)],
            {"111": single_a, "222": single_b},
            lookahead_min=15,
            evaluation_step_s=30,
            max_interpolation_gap_s=180,
            support_distance_nm=0.5,
            near_distance_nm=1.0,
        )

        self.assertEqual(rows[0]["synchronized_sample_count"], 1)
        self.assertEqual(rows[0]["observable_followup"], 0)

    def test_primary_observability_requires_and_accepts_frozen_coverage_rule(self) -> None:
        t0 = dt.datetime(2025, 5, 1, 12, 0, 0)
        rows_a = [
            self.position(t0 + dt.timedelta(seconds=step), 0.0, 0.0, "a-1")
            for step in range(30, 901, 30)
        ]
        rows_b = [
            self.position(t0 + dt.timedelta(seconds=step), 0.001, 0.0, "b-1")
            for step in range(30, 901, 30)
        ]

        rows = encounter_backtest.backtest_episodes(
            [self.episode(t0, predicted_seconds=450)],
            {"111": rows_a, "222": rows_b},
            lookahead_min=15,
            evaluation_step_s=30,
            max_interpolation_gap_s=180,
            support_distance_nm=0.5,
            near_distance_nm=1.0,
        )

        self.assertEqual(rows[0]["synchronized_sample_count"], 30)
        self.assertGreaterEqual(float(rows[0]["common_coverage_duration_s"]), 630.0)
        self.assertLessEqual(float(rows[0]["max_uncovered_gap_s"]), 180.0)
        self.assertEqual(rows[0]["predicted_time_window_covered"], 1)
        self.assertEqual(rows[0]["observable_followup"], 1)

    def test_primary_observability_is_not_conditioned_on_predicted_time_coverage(self) -> None:
        t0 = dt.datetime(2025, 5, 1, 12, 0, 0)
        rows_a = [
            self.position(t0 + dt.timedelta(seconds=step), 0.0, 0.0, "a-1")
            for step in range(30, 901, 30)
        ]
        rows_b = [
            self.position(t0 + dt.timedelta(seconds=step), 0.001, 0.0, "b-1")
            for step in range(30, 901, 30)
        ]
        episode = self.episode(t0, predicted_seconds=1200)

        result = encounter_backtest.backtest_episodes(
            [episode],
            {"111": rows_a, "222": rows_b},
            lookahead_min=15,
            evaluation_step_s=30,
            max_interpolation_gap_s=180,
            support_distance_nm=0.5,
            near_distance_nm=1.0,
        )[0]

        self.assertEqual(result["predicted_time_window_covered"], 0)
        self.assertEqual(result["predicted_time_error_eligible"], 0)
        self.assertEqual(result["observable_followup"], 1)


if __name__ == "__main__":
    unittest.main()
