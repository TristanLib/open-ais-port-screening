from __future__ import annotations

import csv
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tokyo_bay_adapter import CANONICAL_FIELDS, convert_parquet


class TokyoBayAdapterTest(unittest.TestCase):
    def test_convert_parquet_writes_canonical_daily_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            parquet_path = root / "tokyo.parquet"
            output_dir = root / "processed"
            stats_path = root / "stats.json"

            table = pa.table(
                {
                    "lat": [35.40, 35.41, 36.00],
                    "lon": [139.80, 139.81, 139.80],
                    "timestamp": [1722470401, 1722470461, 1722470521],
                    "mmsi": [431000001, 431000002, 431000003],
                    "sog": [8.5, None, 7.0],
                    "type": [70, -1, 80],
                    "destination": ["JP TYO", "JP CHB", "OUTSIDE"],
                }
            )
            pq.write_table(table, parquet_path)

            stats = convert_parquet(
                input_path=parquet_path,
                output_dir=output_dir,
                start=dt.date(2024, 8, 1),
                end=dt.date(2024, 8, 1),
                bbox=(139.62, 34.90, 140.13, 35.69),
                stats_path=stats_path,
                overwrite=True,
            )

            output_path = output_dir / "tokyo_bay_ais_2024-08-01.csv"
            with output_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(stats["output_rows"], 2)
            self.assertEqual(stats["outside_bbox_rows"], 1)
            self.assertEqual(list(rows[0]), CANONICAL_FIELDS)
            self.assertEqual(rows[0]["base_date_time"], "2024-08-01 00:00:01")
            self.assertEqual(rows[0]["vessel_type"], "70")
            self.assertEqual(rows[0]["cog"], "")
            self.assertEqual(rows[1]["sog"], "")
            self.assertEqual(rows[1]["vessel_type"], "")
            self.assertEqual(rows[1]["destination"], "JP CHB")


if __name__ == "__main__":
    unittest.main()
