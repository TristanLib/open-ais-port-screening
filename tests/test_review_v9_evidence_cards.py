from __future__ import annotations

import csv
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evidence_cards import build_encounter_card_bundle, make_encounter_card, validate_public_card_bundle


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class ReviewV9EncounterEvidenceCardTest(unittest.TestCase):
    def test_web_loader_prefers_each_manifest_encounter_card_companion(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            "companion.encounter_evidence_cards || companion.evidence_cards",
            source,
        )
        self.assertIn("state.evidenceCards?.schema_version", source)
        self.assertIn("review-v10.encounter-evidence-card.v2", source)

    def test_builds_relative_deidentified_auditable_card(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            encounter_csv = root / "encounters.csv"
            backtest_csv = root / "backtest.csv"
            processed_dir = root / "processed"

            write_csv(
                encounter_csv,
                [
                    {
                        "date": "2025-05-01",
                        "time_bin": "2025-05-01 12:00:00",
                        "reference_time": "2025-05-01 12:00:00",
                        "timestamp_a": "2025-05-01 11:59:51",
                        "timestamp_b": "2025-05-01 11:59:58",
                        "state_skew_s": 7,
                        "state_age_s_a": 9,
                        "state_age_s_b": 2,
                        "mmsi_a": "111111111",
                        "mmsi_b": "222222222",
                        "lon_a": -122.004,
                        "lat_a": 37.0,
                        "lon_b": -121.996,
                        "lat_b": 37.0,
                        "sog_a": 12,
                        "sog_b": 12,
                        "bearing_a": 90,
                        "bearing_b": 270,
                        "dcpa_nm": 0.12,
                        "tcpa_min": 1,
                        "current_distance_nm": 0.42,
                        "encounter_risk_score": 0.88,
                        "track_id_a": "111111111_0001",
                        "track_id_b": "222222222_0001",
                    },
                    {
                        "date": "2025-05-01",
                        "time_bin": "2025-05-01 12:00:30",
                        "reference_time": "2025-05-01 12:00:30",
                        "timestamp_a": "2025-05-01 12:00:21",
                        "timestamp_b": "2025-05-01 12:00:28",
                        "state_skew_s": 7,
                        "state_age_s_a": 9,
                        "state_age_s_b": 2,
                        "mmsi_a": "111111111",
                        "mmsi_b": "222222222",
                        "lon_a": -122.002,
                        "lat_a": 37.0,
                        "lon_b": -121.998,
                        "lat_b": 37.0,
                        "sog_a": 12,
                        "sog_b": 12,
                        "bearing_a": 90,
                        "bearing_b": 270,
                        "dcpa_nm": 0.05,
                        "tcpa_min": 0.5,
                        "current_distance_nm": 0.2,
                        "encounter_risk_score": 0.95,
                        "track_id_a": "111111111_0001",
                        "track_id_b": "222222222_0001",
                    }
                ],
            )
            write_csv(
                backtest_csv,
                [
                    {
                        "episode_id": "enc_episode_00001",
                        "mmsi_a": "111111111",
                        "mmsi_b": "222222222",
                        "prediction_time": "2025-05-01 12:00:00",
                        "window_end": "2025-05-01 12:15:00",
                        "predicted_closest_time": "2025-05-01 12:01:00",
                        "predicted_dcpa_nm": 0.12,
                        "predicted_tcpa_min": 1,
                        "prediction_record_score": 0.88,
                        "prediction_state_skew_s": 7,
                        "prediction_current_distance_nm": 0.42,
                        "min_current_distance_nm": 0.2,
                        "actual_min_distance_nm": 0.2,
                        "actual_min_time": "2025-05-01 12:00:50",
                        "continuous_min_distance_nm": 0.2,
                        "continuous_min_time": "2025-05-01 12:00:50",
                        "synchronized_sample_count": 22,
                        "scheduled_sample_count": 30,
                        "common_coverage_duration_s": 660,
                        "common_coverage_fraction": 0.733333,
                        "max_uncovered_gap_s": 180,
                        "predicted_time_window_covered": 1,
                        "observable_followup": 1,
                        "supported_within_threshold": 1,
                        "near_supported_within_1nm": 1,
                        "grid_10s_common_sample_count": 66,
                        "grid_10s_min_distance_nm": 0.2,
                        "grid_30s_common_sample_count": 22,
                        "grid_30s_min_distance_nm": 0.21,
                        "grid_60s_common_sample_count": 11,
                        "grid_60s_min_distance_nm": 0.23,
                        "predicted_to_observed_abs_delta_min": 0.166667,
                        "predicted_dcpa_abs_error_nm": 0.08,
                        "backtest_status": "future_supported_within_threshold",
                    }
                ],
            )
            feature_rows = [
                # t0 and earlier observations must not be used in future geometry.
                {
                    "mmsi": "111111111",
                    "base_date_time": "2025-05-01 12:00:00",
                    "longitude": -122.004,
                    "latitude": 37.0,
                    "track_id": "111111111_0001",
                },
                {
                    "mmsi": "222222222",
                    "base_date_time": "2025-05-01 12:00:00",
                    "longitude": -121.996,
                    "latitude": 37.0,
                    "track_id": "222222222_0001",
                },
                {
                    "mmsi": "111111111",
                    "base_date_time": "2025-05-01 12:00:10",
                    "longitude": -122.003,
                    "latitude": 37.0,
                    "track_id": "111111111_0001",
                },
                {
                    "mmsi": "222222222",
                    "base_date_time": "2025-05-01 12:00:10",
                    "longitude": -121.997,
                    "latitude": 37.0,
                    "track_id": "222222222_0001",
                },
                {
                    "mmsi": "111111111",
                    "base_date_time": "2025-05-01 12:01:10",
                    "longitude": -122.001,
                    "latitude": 37.0,
                    "track_id": "111111111_0001",
                },
                {
                    "mmsi": "222222222",
                    "base_date_time": "2025-05-01 12:01:10",
                    "longitude": -121.999,
                    "latitude": 37.0,
                    "track_id": "222222222_0001",
                },
                # These points change track and cannot be joined to the first segment.
                {
                    "mmsi": "111111111",
                    "base_date_time": "2025-05-01 12:02:10",
                    "longitude": -121.99,
                    "latitude": 37.01,
                    "track_id": "111111111_0002",
                },
                {
                    "mmsi": "222222222",
                    "base_date_time": "2025-05-01 12:02:10",
                    "longitude": -122.01,
                    "latitude": 36.99,
                    "track_id": "222222222_0002",
                },
                # Points outside t1 must never enter the card.
                {
                    "mmsi": "111111111",
                    "base_date_time": "2025-05-01 12:15:10",
                    "longitude": -121.8,
                    "latitude": 37.2,
                    "track_id": "111111111_0002",
                },
                {
                    "mmsi": "222222222",
                    "base_date_time": "2025-05-01 12:15:10",
                    "longitude": -122.2,
                    "latitude": 36.8,
                    "track_id": "222222222_0002",
                },
            ]
            write_csv(processed_dir / "sf_bay_ais_2025-05-01_features.csv", feature_rows)

            bundle = build_encounter_card_bundle(
                encounter_csv=encounter_csv,
                backtest_csv=backtest_csv,
                processed_dir=processed_dir,
                dataset_prefix="sf_bay_ais",
                dataset_id="sf_bay",
                max_cards=4,
            )
            validate_public_card_bundle(bundle, expected_dataset_id="sf_bay")

            self.assertEqual(bundle["schema_version"], "review-v10.encounter-evidence-card.v2")
            self.assertEqual(bundle["card_count"], 1)
            card = bundle["cards"][0]
            self.assertEqual(card["case_id"], "sf-bay-enc-rv10-001")
            self.assertEqual(card["synchronized_t0"]["t_s"], 0)
            self.assertEqual(card["synchronized_t0"]["source_state_skew_s"], 7)
            self.assertEqual(card["prediction"]["closest_time_t_s"], 60)
            self.assertEqual(card["prediction"]["current_distance_nm"], 0.42)
            self.assertEqual(card["observation"]["continuous_min_time_t_s"], 50)
            self.assertEqual(card["observation"]["closest_time_abs_error_s"], 10)
            self.assertTrue(card["observation"]["closest_time_error_eligible"])
            self.assertEqual(card["coverage"]["common_sample_count"], 22)
            self.assertEqual(card["support"]["status"], "geometrically_supported_within_0_5_nm")

            relative_positions = card["synchronized_t0"]["relative_positions"]["vessels"]
            relative_distance = math.hypot(
                relative_positions[1]["x_nm"] - relative_positions[0]["x_nm"],
                relative_positions[1]["y_nm"] - relative_positions[0]["y_nm"],
            )
            self.assertAlmostEqual(
                card["prediction"]["current_distance_nm"],
                relative_distance,
                delta=0.05,
            )
            if card["prediction"]["tcpa_min"] > 0:
                self.assertLessEqual(
                    card["prediction"]["dcpa_nm"],
                    card["prediction"]["current_distance_nm"] + 1e-6,
                )

            segments = card["future_geometry"]["common_segments"]
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0]["start_t_s"], 10)
            self.assertEqual(segments[0]["end_t_s"], 70)
            points = [point for vessel in segments[0]["vessels"] for point in vessel["points"]]
            self.assertTrue(points)
            self.assertTrue(all(0 < point["t_s"] <= 900 for point in points))
            self.assertTrue(all(round(point["x_nm"], 2) == point["x_nm"] for point in points))
            self.assertTrue(all(round(point["y_nm"], 2) == point["y_nm"] for point in points))

            serialized = json.dumps(bundle, sort_keys=True)
            for sensitive_value in (
                "111111111",
                "222222222",
                "111111111_0001",
                "222222222_0001",
                "2025-05-01",
                "12:00:00",
                "-122.004",
                "-121.996",
            ):
                self.assertNotIn(sensitive_value, serialized)

            without_geometry = build_encounter_card_bundle(
                encounter_csv=encounter_csv,
                backtest_csv=backtest_csv,
                processed_dir=None,
                dataset_prefix="sf_bay_ais",
                dataset_id="sf_bay",
                max_cards=1,
            )
            self.assertEqual(without_geometry["selection"]["geometry_input_status"], "unavailable")
            self.assertEqual(without_geometry["cards"][0]["future_geometry"]["status"], "unavailable")
            self.assertEqual(without_geometry["cards"][0]["future_geometry"]["common_segments"], [])

    def test_zero_causal_distance_does_not_truthiness_fallback(self) -> None:
        prediction_time = "2025-05-01 12:00:00"
        card = make_encounter_card(
            rank=1,
            dataset_id="sf_bay",
            backtest_row={
                "mmsi_a": "111111111",
                "mmsi_b": "222222222",
                "prediction_time": prediction_time,
                "window_end": "2025-05-01 12:15:00",
                "predicted_closest_time": prediction_time,
                "predicted_dcpa_nm": 0,
                "predicted_tcpa_min": 0,
                "prediction_current_distance_nm": 0,
                "min_current_distance_nm": 0,
                "observable_followup": "0",
            },
            encounter_row={
                "mmsi_a": "111111111",
                "mmsi_b": "222222222",
                "current_distance_nm": "0.42",
            },
            positions={},
            lookahead_min=15,
            max_interpolation_gap_s=180,
            geometry_step_s=30,
            max_geometry_points=32,
        )

        self.assertEqual(card["prediction"]["current_distance_nm"], 0.0)

    def test_public_validator_rejects_noncausal_geometry_and_window_leaks(self) -> None:
        bundle = {
            "schema_version": "review-v10.encounter-evidence-card.v2",
            "dataset_id": "sf_bay",
            "publication_mode": "sanitized_research_demo",
            "card_count": 1,
            "cards": [
                {
                    "case_id": "sf-bay-enc-rv10-001",
                    "synchronized_t0": {
                        "t_s": 0,
                        "relative_positions": {
                            "vessels": [
                                {"label": "A", "x_nm": -0.2, "y_nm": 0.0},
                                {"label": "B", "x_nm": 0.2, "y_nm": 0.0},
                            ]
                        },
                    },
                    "prediction": {
                        "current_distance_nm": 0.8,
                        "dcpa_nm": 0.2,
                        "tcpa_min": 1.0,
                    },
                    "coverage": {"window_duration_s": 900},
                    "future_geometry": {"status": "unavailable", "common_segments": []},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "causal t0 distance"):
            validate_public_card_bundle(bundle, expected_dataset_id="sf_bay")

        bundle["cards"][0]["prediction"]["current_distance_nm"] = 0.4
        bundle["cards"][0]["future_geometry"] = {
            "status": "available",
            "common_segments": [
                {
                    "start_t_s": 0,
                    "end_t_s": 30,
                    "vessels": [
                        {"label": "A", "points": [{"t_s": 0, "x_nm": -0.2, "y_nm": 0.0}]},
                        {"label": "B", "points": [{"t_s": 0, "x_nm": 0.2, "y_nm": 0.0}]},
                    ],
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "strict-future window"):
            validate_public_card_bundle(bundle, expected_dataset_id="sf_bay")

    def test_public_validator_rejects_identity_and_exact_time_leaks(self) -> None:
        bundle = {
            "schema_version": "review-v10.encounter-evidence-card.v2",
            "dataset_id": "tokyo_bay",
            "publication_mode": "sanitized_research_demo",
            "card_count": 1,
            "cards": [{"case_id": "tokyo-bay-enc-rv10-001", "mmsi": "431000001"}],
        }
        with self.assertRaisesRegex(ValueError, "sensitive key"):
            validate_public_card_bundle(bundle, expected_dataset_id="tokyo_bay")

        bundle["cards"] = [{"case_id": "tokyo-bay-enc-rv10-001", "note": "2024-08-01 12:30:00"}]
        with self.assertRaisesRegex(ValueError, "exact timestamp"):
            validate_public_card_bundle(bundle, expected_dataset_id="tokyo_bay")


if __name__ == "__main__":
    unittest.main()
