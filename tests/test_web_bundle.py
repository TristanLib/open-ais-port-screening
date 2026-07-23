from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WEB_DATA = ROOT / "web" / "data"
OUTPUTS_WEB = ROOT / "outputs" / "web"
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

    def test_output_bundle_is_complete_and_sanitized(self) -> None:
        if not OUTPUTS_WEB.is_dir():
            self.skipTest("public package intentionally omits the duplicate outputs/web tree")
        validate_bundle(OUTPUTS_WEB)

    def test_catalog_exposes_two_study_areas(self) -> None:
        catalog = json.loads((WEB_DATA / "datasets.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["default_dataset"], "sf_bay")
        self.assertEqual({item["id"] for item in catalog["datasets"]}, {"sf_bay", "tokyo_bay"})

    def test_v10_card_and_behavior_component_contract(self) -> None:
        app_source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("仅行为证据分量对照网格", app_source)
        self.assertNotIn("仅异常证据热区网格", app_source)
        for manifest_name in ("manifest.json", "manifest_tokyo_bay.json"):
            manifest = json.loads((WEB_DATA / manifest_name).read_text(encoding="utf-8"))
            card_path = WEB_DATA / manifest["companion_data"]["encounter_evidence_cards"]
            cards = json.loads(card_path.read_text(encoding="utf-8"))
            self.assertEqual(cards["schema_version"], "review-v10.encounter-evidence-card.v2")
            self.assertEqual(cards["dataset_id"], manifest["dataset_id"])
            self.assertEqual(cards["card_count"], len(cards["cards"]))
        sf_manifest = json.loads((WEB_DATA / "manifest.json").read_text(encoding="utf-8"))
        risk_layer = next(item for item in sf_manifest["layers"] if item["id"] == "risk_hotspots")
        self.assertEqual(risk_layer["label"], "Behavior-component-only comparison cells")
        summary = json.loads((WEB_DATA / "summary.json").read_text(encoding="utf-8"))
        variants = [row["variant"] for row in summary["ablation"]]
        self.assertIn("Behavior-component-only", variants)
        self.assertNotIn("Anomaly-only", variants)

    def test_published_cards_and_headlines_match_authoritative_tables(self) -> None:
        if not (ROOT / "outputs" / "tables").is_dir():
            self.skipTest("public package intentionally omits full analytical tables")
        datasets = {
            "sf_bay": {
                "prefix": "sf_bay_ais_2025-05-01_to_2025-05-07",
                "card_name": "encounter_evidence_cards.json",
                "summary_name": "summary.json",
            },
            "tokyo_bay": {
                "prefix": "tokyo_bay_ais_2024-08-01_to_2024-08-07",
                "card_name": "encounter_evidence_cards_tokyo_bay.json",
                "summary_name": "summary_tokyo_bay.json",
            },
        }
        for dataset_id, config in datasets.items():
            prefix = config["prefix"]
            table_cards = json.loads(
                (ROOT / "outputs" / "tables" / f"{prefix}_encounter_evidence_cards.json").read_text(
                    encoding="utf-8"
                )
            )
            published_cards = json.loads((WEB_DATA / config["card_name"]).read_text(encoding="utf-8"))
            output_cards = json.loads((OUTPUTS_WEB / config["card_name"]).read_text(encoding="utf-8"))
            self.assertEqual(table_cards, published_cards)
            self.assertEqual(table_cards, output_cards)

            summary = json.loads((WEB_DATA / config["summary_name"]).read_text(encoding="utf-8"))
            encounter = json.loads(
                (ROOT / "outputs" / "tables" / f"{prefix}_encounters_stats.json").read_text(
                    encoding="utf-8"
                )
            )
            backtest = json.loads(
                (ROOT / "outputs" / "tables" / f"{prefix}_encounter_backtest_stats.json").read_text(
                    encoding="utf-8"
                )
            )
            hotspots = json.loads(
                (ROOT / "outputs" / "tables" / f"{prefix}_fused_risk_hotspots_stats.json").read_text(
                    encoding="utf-8"
                )
            )
            with self.subTest(dataset_id=dataset_id):
                self.assertEqual(summary["encounter_risk"]["encounters"], encounter["encounters"])
                self.assertEqual(
                    summary["encounter_risk"]["deduplicated_encounter_episodes"],
                    encounter["deduplicated_encounter_episodes"],
                )
                self.assertEqual(
                    summary["encounter_backtest"]["episodes_with_aligned_followup"],
                    backtest["episodes_with_aligned_followup"],
                )
                self.assertEqual(
                    summary["encounter_backtest"]["supported_episodes_within_threshold"],
                    backtest["supported_episodes_within_threshold"],
                )
                self.assertEqual(summary["hotspots"]["fused_hotspots"], hotspots["hotspot_cells"])

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
