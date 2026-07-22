from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WEB_DATA = ROOT / "web" / "data"
sys.path.insert(0, str(ROOT / "src"))

from build_web_datasets import TOKYO_BBOX, validate_bundle


def iter_coordinate_pairs(value: Any):
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        yield float(value[0]), float(value[1])
        return
    if isinstance(value, list):
        for item in value:
            yield from iter_coordinate_pairs(item)


class WebBundleTest(unittest.TestCase):
    def test_tracked_bundle_is_complete_and_sanitized(self) -> None:
        validate_bundle(WEB_DATA)

    def test_catalog_exposes_two_study_areas(self) -> None:
        catalog = json.loads((WEB_DATA / "datasets.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["default_dataset"], "sf_bay")
        self.assertEqual({item["id"] for item in catalog["datasets"]}, {"sf_bay", "tokyo_bay"})

    def test_tokyo_layers_use_tokyo_bay_coordinates(self) -> None:
        manifest = json.loads((WEB_DATA / "manifest_tokyo_bay.json").read_text(encoding="utf-8"))
        min_lon, min_lat, max_lon, max_lat = TOKYO_BBOX
        coordinate_count = 0
        for layer in manifest["layers"]:
            data = json.loads((WEB_DATA / layer["path"]).read_text(encoding="utf-8"))
            for feature in data["features"]:
                for lon, lat in iter_coordinate_pairs(feature["geometry"]["coordinates"]):
                    coordinate_count += 1
                    self.assertGreaterEqual(lon, min_lon)
                    self.assertLessEqual(lon, max_lon)
                    self.assertGreaterEqual(lat, min_lat)
                    self.assertLessEqual(lat, max_lat)
        self.assertGreater(coordinate_count, 100)


if __name__ == "__main__":
    unittest.main()
