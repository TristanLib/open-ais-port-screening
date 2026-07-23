from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anomaly_score import score_row
from encounter_backtest import Position, build_episode, interpolated_future_min_distance
from encounter_risk import synchronize_pair_states
from risk_hotspots import calculate_cell_components
from screening_rank_evaluation import evaluate_rankings


class EncounterSynchronizationTest(unittest.TestCase):
    def test_pair_states_are_propagated_to_a_common_reference_time(self) -> None:
        earlier = {
            "timestamp": "2025-05-01 12:00:00",
            "lon": -122.40,
            "lat": 37.80,
            "sog": 12.0,
            "bearing": 90.0,
        }
        later = {
            "timestamp": "2025-05-01 12:00:30",
            "lon": -122.39,
            "lat": 37.80,
            "sog": 8.0,
            "bearing": 180.0,
        }

        synchronized = synchronize_pair_states(earlier, later, max_state_skew_s=60)

        self.assertIsNotNone(synchronized)
        aligned_a, aligned_b, reference_time, state_skew_s = synchronized  # type: ignore[misc]
        self.assertEqual(reference_time, dt.datetime(2025, 5, 1, 12, 0, 30))
        self.assertEqual(state_skew_s, 30.0)
        self.assertGreater(float(aligned_a["lon"]), float(earlier["lon"]))
        self.assertAlmostEqual(float(aligned_b["lon"]), float(later["lon"]), places=8)
        self.assertAlmostEqual(float(aligned_b["lat"]), float(later["lat"]), places=8)

    def test_pair_states_beyond_skew_limit_are_rejected(self) -> None:
        a = {
            "timestamp": "2025-05-01 12:00:00",
            "lon": -122.40,
            "lat": 37.80,
            "sog": 12.0,
            "bearing": 90.0,
        }
        b = {
            "timestamp": "2025-05-01 12:01:01",
            "lon": -122.39,
            "lat": 37.80,
            "sog": 8.0,
            "bearing": 180.0,
        }

        self.assertIsNone(synchronize_pair_states(a, b, max_state_skew_s=60))


class FutureOnlyBacktestTest(unittest.TestCase):
    def test_episode_prediction_uses_first_causal_record(self) -> None:
        t0 = dt.datetime(2025, 5, 1, 12, 0)
        records = [
            {
                "time_bin": t0,
                "reference_time": t0,
                "dcpa_nm": 0.40,
                "tcpa_min": 10.0,
                "current_distance_nm": 1.0,
                "encounter_risk_score": 0.30,
                "predicted_closest_time": t0 + dt.timedelta(minutes=10),
            },
            {
                "time_bin": t0 + dt.timedelta(minutes=1),
                "reference_time": t0 + dt.timedelta(minutes=1),
                "dcpa_nm": 0.05,
                "tcpa_min": 2.0,
                "current_distance_nm": 0.5,
                "encounter_risk_score": 0.95,
                "predicted_closest_time": t0 + dt.timedelta(minutes=3),
            },
        ]

        episode = build_episode("2025-05-01", "111", "222", records)

        self.assertEqual(episode["prediction_time"], t0)
        self.assertEqual(episode["predicted_dcpa_nm"], 0.40)
        self.assertEqual(episode["predicted_tcpa_min"], 10.0)

    def test_geometric_check_uses_only_times_after_prediction(self) -> None:
        t0 = dt.datetime(2025, 5, 1, 12, 0)
        rows_a = [
            Position(t0 - dt.timedelta(seconds=30), 0.0, 0.0, "a-1"),
            Position(t0, 0.0, 0.0, "a-1"),
            Position(t0 + dt.timedelta(seconds=60), 0.0, 0.0, "a-1"),
        ]
        rows_b = [
            Position(t0 - dt.timedelta(seconds=30), 0.0, 0.0, "b-1"),
            Position(t0, 0.02, 0.0, "b-1"),
            Position(t0 + dt.timedelta(seconds=60), 0.02, 0.0, "b-1"),
        ]

        result = interpolated_future_min_distance(
            rows_a,
            rows_b,
            prediction_time=t0,
            window_end=t0 + dt.timedelta(seconds=60),
            evaluation_step_s=30,
            max_interpolation_gap_s=120,
        )

        # The edge from t0 to t0+60 is intentionally excluded because the
        # frozen review-v9 rule requires both endpoints to be post-candidate.
        self.assertEqual(result["synchronized_sample_count"], 1)
        self.assertGreater(float(result["actual_min_distance_nm"]), 1.0)
        self.assertGreater(result["actual_min_time"], t0)  # type: ignore[operator]


class EvidenceDefinitionTest(unittest.TestCase):
    def test_low_route_support_is_separated_from_corroborated_candidate(self) -> None:
        base_row = {
            "mmsi": "111",
            "base_date_time": "2025-05-01 12:00:00",
            "longitude": "0.5",
            "latitude": "0.5",
            "sog": "8.0",
            "bearing_deg": "90.0",
            "track_id": "track-1",
            "turn_rate_deg_per_min": "0",
            "accel_kn_per_min": "0",
            "implied_sog_kn": "8",
        }

        support_only = score_row(base_row, {}, (0.0, 0.0, 1.0, 1.0), 0.1, 1.0)
        self.assertEqual(support_only["evidence_tier"], "low_support_only")  # type: ignore[index]
        self.assertEqual(support_only["corroborated_candidate"], 0)  # type: ignore[index]
        self.assertIn("low_empirical_route_support", support_only["reasons"])  # type: ignore[operator,index]

        high_speed_row = {**base_row, "sog": "16.0", "implied_sog_kn": "16.0"}
        corroborated = score_row(high_speed_row, {}, (0.0, 0.0, 1.0, 1.0), 0.1, 1.0)
        self.assertEqual(corroborated["evidence_tier"], "corroborated_candidate")  # type: ignore[index]
        self.assertEqual(corroborated["corroborated_candidate"], 1)  # type: ignore[index]

    def test_encounter_rate_uses_pair_opportunity_exposure(self) -> None:
        components = calculate_cell_components(
            anomaly_count=20,
            weighted_anomaly_count=10.0,
            weighted_anomaly_score_sum=5.0,
            point_count=10_000,
            moving_count=1_000,
            encounter_count=10,
            encounter_score_sum=6.0,
            pair_opportunity_count=100,
        )

        self.assertAlmostEqual(components["encounter_rate"], 0.10)
        self.assertAlmostEqual(components["anomaly_rate"], 0.01)

    def test_ranking_evaluation_compares_fusion_with_simple_baselines(self) -> None:
        cells = [
            {"cell_id": "a", "point_count": 100, "anomaly_component": 0.1, "encounter_component": 2.0},
            {"cell_id": "b", "point_count": 200, "anomaly_component": 0.5, "encounter_component": 0.1},
            {"cell_id": "c", "point_count": 300, "anomaly_component": 0.1, "encounter_component": 0.1},
        ]
        episodes = [
            {"cell_id": "a", "observable": 1, "supported": 1},
            {"cell_id": "b", "observable": 1, "supported": 0},
            {"cell_id": "c", "observable": 1, "supported": 0},
        ]

        result = evaluate_rankings(cells, episodes, top_fractions=(0.10,))

        fused = result["rankings"]["fused_w0.75"][0]
        density = result["rankings"]["density_only"][0]
        self.assertEqual(fused["supported_episodes"], 1)
        self.assertEqual(density["supported_episodes"], 0)


if __name__ == "__main__":
    unittest.main()
