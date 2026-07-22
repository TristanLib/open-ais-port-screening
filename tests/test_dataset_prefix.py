from __future__ import annotations

import csv
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anomaly_score import compute_anomalies
from encounter_backtest import load_positions
from encounter_risk import compute_encounters
from risk_hotspots import load_exposure
from traffic_patterns import learn_patterns


class DatasetPrefixTest(unittest.TestCase):
    def test_core_analysis_reads_custom_dataset_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            processed_dir = root / "processed"
            tables_dir = root / "tables"
            processed_dir.mkdir()
            tables_dir.mkdir()

            tracks_path = processed_dir / "tokyo_bay_ais_2024-08-01_tracks_min20.csv"
            with tracks_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "mmsi",
                        "track_id",
                        "longitude",
                        "latitude",
                        "sog",
                        "bearing_deg",
                        "cog",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "mmsi": "431000001",
                            "track_id": "431000001_0001",
                            "longitude": "139.80",
                            "latitude": "35.40",
                            "sog": "8.0",
                            "bearing_deg": "0.0",
                            "cog": "",
                        },
                        {
                            "mmsi": "431000002",
                            "track_id": "431000002_0001",
                            "longitude": "139.801",
                            "latitude": "35.401",
                            "sog": "9.0",
                            "bearing_deg": "47.0",
                            "cog": "",
                        },
                    ]
                )

            pattern_csv = tables_dir / "patterns.csv"
            stats = learn_patterns(
                start=dt.date(2024, 8, 1),
                end=dt.date(2024, 8, 1),
                processed_dir=processed_dir,
                output_csv=pattern_csv,
                output_geojson=root / "patterns.geojson",
                stats_json=root / "patterns.json",
                bbox=(139.62, 34.90, 140.13, 35.69),
                cell_size_deg=0.01,
                min_sog=1.0,
                min_points=1,
                min_tracks=1,
                dataset_prefix="tokyo_bay_ais",
                track_min_points=20,
            )
            self.assertEqual(stats["used_rows"], 2)

            features_path = processed_dir / "tokyo_bay_ais_2024-08-01_features.csv"
            with features_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
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
                        "vessel_type",
                        "length",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "mmsi": "431000001",
                            "track_id": "431000001_0001",
                            "base_date_time": "2024-08-01 00:00:01",
                            "longitude": "139.80",
                            "latitude": "35.40",
                            "sog": "8.0",
                            "bearing_deg": "0.0",
                            "cog": "",
                            "turn_rate_deg_per_min": "0",
                            "accel_kn_per_min": "0",
                            "implied_sog_kn": "8",
                            "vessel_type": "70",
                            "length": "",
                        },
                        {
                            "mmsi": "431000002",
                            "track_id": "431000002_0001",
                            "base_date_time": "2024-08-01 00:00:02",
                            "longitude": "139.801",
                            "latitude": "35.401",
                            "sog": "9.0",
                            "bearing_deg": "47.0",
                            "cog": "",
                            "turn_rate_deg_per_min": "0",
                            "accel_kn_per_min": "0",
                            "implied_sog_kn": "9",
                            "vessel_type": "70",
                            "length": "",
                        },
                    ]
                )

            anomaly_stats = compute_anomalies(
                start=dt.date(2024, 8, 1),
                end=dt.date(2024, 8, 1),
                processed_dir=processed_dir,
                patterns_csv=pattern_csv,
                output_csv=root / "anomalies.csv",
                output_geojson=root / "anomalies.geojson",
                stats_json=root / "anomalies.json",
                bbox=(139.62, 34.90, 140.13, 35.69),
                cell_size_deg=0.01,
                moving_sog=1.0,
                min_score=0.0,
                geojson_limit=10,
                dataset_prefix="tokyo_bay_ais",
            )
            self.assertEqual(anomaly_stats["counters"]["input_rows"], 2)

            encounter_stats = compute_encounters(
                start=dt.date(2024, 8, 1),
                end=dt.date(2024, 8, 1),
                processed_dir=processed_dir,
                output_csv=root / "encounters.csv",
                output_geojson=root / "encounters.geojson",
                stats_json=root / "encounters.json",
                time_bin_seconds=300,
                spatial_cell_deg=0.02,
                min_sog=1.0,
                max_current_distance_nm=2.0,
                dcpa_threshold_nm=0.5,
                tcpa_threshold_min=15.0,
                geojson_limit=10,
                dataset_prefix="tokyo_bay_ais",
            )
            self.assertEqual(encounter_stats["vessel_states"], 2)

            positions = load_positions(
                processed_dir,
                dt.date(2024, 8, 1),
                dt.date(2024, 8, 1),
                lookahead_min=15.0,
                wanted_mmsi={"431000001", "431000002"},
                dataset_prefix="tokyo_bay_ais",
            )
            self.assertEqual(len(positions["431000001"]), 1)

            density_path = tables_dir / "tokyo_bay_ais_2024-08-01_grid_density.csv"
            with density_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "cell_id",
                        "point_count",
                        "unique_mmsi_sum",
                        "unique_tracks_sum",
                        "moving_count",
                        "stationary_count",
                        "high_speed_count",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "cell_id": "r50_c18",
                        "point_count": "2",
                        "unique_mmsi_sum": "2",
                        "unique_tracks_sum": "2",
                        "moving_count": "2",
                        "stationary_count": "0",
                        "high_speed_count": "0",
                    }
                )

            exposure = load_exposure(
                dt.date(2024, 8, 1),
                dt.date(2024, 8, 1),
                tables_dir,
                dataset_prefix="tokyo_bay_ais",
            )
            self.assertEqual(exposure["r50_c18"]["point_count"], 2.0)


if __name__ == "__main__":
    unittest.main()
