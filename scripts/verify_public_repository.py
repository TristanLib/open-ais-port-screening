#!/usr/bin/env python3
"""Verify that this checkout satisfies the code-only public-release boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TOP_LEVEL = {"paper", "review", "dist", "output", "outputs", "要求"}
FORBIDDEN_PATH_TERMS = {"external_review", "submission"}
IGNORED_SCAN_PARTS = {".git", ".venv", "__pycache__", "dist", "output", "outputs", "tmp"}
TEXT_SUFFIXES = {".cff", ".css", ".html", ".js", ".json", ".md", ".py", ".txt", ".yml", ".yaml"}
SENSITIVE_WEB_KEYS = {
    "mmsi",
    "mmsi_a",
    "mmsi_b",
    "imo",
    "call_sign",
    "vessel_name",
    "track_id",
    "track_id_a",
    "track_id_b",
    "base_date_time",
    "time_bin",
    "start_time",
    "end_time",
    "reference_time",
    "timestamp_a",
    "timestamp_b",
    "lon_a",
    "lat_a",
    "lon_b",
    "lat_b",
    "bearing_a",
    "bearing_b",
    "sog_a",
    "sog_b",
}


def iter_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from iter_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_keys(child)


def fail(messages: list[str]) -> int:
    for message in messages:
        print(f"FAIL: {message}")
    return 1


def main() -> int:
    errors: list[str] = []
    top_level = {path.name for path in ROOT.iterdir() if path.name != ".git"}
    leaked_top_level = sorted(top_level & FORBIDDEN_TOP_LEVEL)
    if leaked_top_level:
        errors.append(f"forbidden top-level paths: {', '.join(leaked_top_level)}")

    text_tokens = (
        "li.bo" + "@",
        "cmaritime" + ".com.cn",
        "/Users" + "/",
        "/private" + "/tmp/",
        "Scholar" + "One",
        "anc" + "2026",
        "paper" + "/",
        "review" + "/",
    )
    for path in ROOT.rglob("*"):
        if not path.is_file() or set(path.relative_to(ROOT).parts) & IGNORED_SCAN_PARTS:
            continue
        relative = path.relative_to(ROOT)
        lowered_parts = {part.lower() for part in relative.parts}
        if lowered_parts & FORBIDDEN_PATH_TERMS:
            errors.append(f"forbidden publication path: {relative}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in text_tokens:
            if token.lower() in text.lower():
                errors.append(f"forbidden public text token in {relative}: {token}")

    tokyo_manifest = (ROOT / "configs/data_manifest_tokyo_bay.yml").read_text(encoding="utf-8")
    required_tokyo_values = (
        "time_bin_seconds: 60",
        "max_state_skew_s: 60",
        "encounter_candidate_records: 26389",
        "encounter_audit_episodes: 8929",
        "backtest_supported_episodes: 5428",
        "fused_hotspot_cells: 39",
    )
    for value in required_tokyo_values:
        if value not in tokyo_manifest:
            errors.append(f"Tokyo reference manifest is missing: {value}")

    for path in sorted((ROOT / "web/data").glob("*.json")) + sorted((ROOT / "web/data").glob("*.geojson")):
        data = json.loads(path.read_text(encoding="utf-8"))
        leaked_keys = sorted(set(iter_keys(data)) & SENSITIVE_WEB_KEYS)
        if leaked_keys:
            errors.append(f"sensitive Web keys in {path.relative_to(ROOT)}: {', '.join(leaked_keys)}")

    if errors:
        return fail(errors)
    print("Public repository verification: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
