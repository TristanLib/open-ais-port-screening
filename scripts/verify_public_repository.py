#!/usr/bin/env python3
"""Verify that this checkout satisfies the code-only public-release boundary."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TOP_LEVEL = {"paper", "review", "dist", "output", "outputs", "要求"}
FORBIDDEN_PATH_TERMS = {"external_review", "submission"}
IGNORED_SCAN_PARTS = {".git", ".venv", "__pycache__", "dist", "output", "outputs", "tmp"}
TEXT_SUFFIXES = {".cff", ".css", ".html", ".js", ".json", ".md", ".py", ".txt", ".yml", ".yaml"}
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
ORCID_RE = re.compile(r"\b(?:\d{4}-){3}\d{3}[\dX]\b", re.IGNORECASE)
CONFERENCE_RE = re.compile(r"\bANC\s*2026\b", re.IGNORECASE)
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
        "/Users" + "/",
        "/private" + "/tmp/",
        "Scholar" + "One",
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
        if EMAIL_RE.search(text):
            errors.append(f"email address in public text: {relative}")
        if ORCID_RE.search(text):
            errors.append(f"ORCID in public text: {relative}")
        if CONFERENCE_RE.search(text):
            errors.append(f"submission-specific conference reference in public text: {relative}")

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    if 'name: "Open AIS Port Screening contributors"' not in citation:
        errors.append("CITATION.cff does not use the non-identifying contributor credit")
    for private_field in ("family-names:", "given-names:", "orcid:"):
        if private_field in citation.casefold():
            errors.append(f"private author field in CITATION.cff: {private_field}")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    if "Open AIS Port Screening contributors" not in license_text:
        errors.append("LICENSE does not use the non-identifying contributor credit")

    tokyo_manifest = (ROOT / "configs/data_manifest_tokyo_bay.yml").read_text(encoding="utf-8")
    required_tokyo_values = (
        "review_version: review-v10",
        "status: review_v10_recomputed_verified",
        "time_bin_seconds: 60",
        "max_state_skew_s: 60",
        "evaluated_pair_opportunities: 180585",
        "encounter_candidate_records: 28515",
        "encounter_audit_episodes: 9389",
        "encounter_backtest_observable_episodes: 3178",
        "encounter_backtest_supported_episodes: 2220",
        "fused_hotspot_cells: 40",
    )
    for value in required_tokyo_values:
        if value not in tokyo_manifest:
            errors.append(f"Tokyo reference manifest is missing: {value}")
    for private_reference in (
        "baseline_commit:",
        "REVIEW_V10_METHOD_CHANGES.md",
        "REVIEW_V10_RESULTS_AUDIT.md",
    ):
        if private_reference in tokyo_manifest:
            errors.append(f"private Tokyo manifest reference remains: {private_reference}")
    if "protocol: docs/METHOD_PROTOCOL.md" not in tokyo_manifest:
        errors.append("Tokyo public manifest does not reference docs/METHOD_PROTOCOL.md")

    sf_manifest = (ROOT / "configs/data_manifest.yml").read_text(encoding="utf-8")
    required_sf_values = (
        "review_version: review-v10",
        "status: review_v10_recomputed_verified",
        "time_bin_seconds: 60",
        "max_state_skew_s: 60",
        "evaluated_pair_opportunities: 300904",
        "encounter_candidate_records: 56221",
        "encounter_audit_episodes: 19805",
        "encounter_backtest_observable_episodes: 11530",
        "encounter_backtest_supported_episodes: 7945",
        "fused_hotspot_cells: 57",
    )
    for value in required_sf_values:
        if value not in sf_manifest:
            errors.append(f"San Francisco reference manifest is missing: {value}")
    for private_reference in (
        "baseline_commit:",
        "REVIEW_V10_METHOD_CHANGES.md",
        "REVIEW_V10_RESULTS_AUDIT.md",
    ):
        if private_reference in sf_manifest:
            errors.append(f"private San Francisco manifest reference remains: {private_reference}")
    if "protocol: docs/METHOD_PROTOCOL.md" not in sf_manifest:
        errors.append("San Francisco public manifest does not reference docs/METHOD_PROTOCOL.md")

    method_protocol = (ROOT / "docs/METHOD_PROTOCOL.md").read_text(encoding="utf-8")
    required_method_terms = (
        "Indexed and brute-force pair sets must be identical",
        "same non-empty `track_id`",
        "at least 21 of the 30 scheduled 30 s common samples",
        "at least 630 s of common continuous coverage",
        "continuous minimum over overlapping",
        "cross-source executability and common output-schema",
    )
    for term in required_method_terms:
        if term not in method_protocol:
            errors.append(f"review-v10 method contract is missing: {term}")

    for summary_name in ("summary.json", "summary_tokyo_bay.json"):
        summary_path = ROOT / "web/data" / summary_name
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("encounter_evidence_cards", {}).get("schema_version") != "review-v10.encounter-evidence-card.v2":
            errors.append(f"unexpected evidence-card schema in {summary_path.relative_to(ROOT)}")
        if summary.get("ranking_evaluation", {}).get("protocol") != "docs/METHOD_PROTOCOL.md":
            errors.append(f"non-public protocol path in {summary_path.relative_to(ROOT)}")

    sf_summary = json.loads((ROOT / "web/data/summary.json").read_text(encoding="utf-8"))
    expected_ablation = {
        "Behavior-component-only": "35 hotspot cells",
        "Encounter-only": "56,221 records; 19,805 episodes",
        "Fused screening": "57 hotspot cells",
    }
    actual_ablation = {row.get("variant"): row.get("result") for row in sf_summary.get("ablation", [])}
    for variant, expected in expected_ablation.items():
        if actual_ablation.get(variant) != expected:
            errors.append(f"stale SF Web ablation for {variant}: {actual_ablation.get(variant)!r}")
    if sf_summary.get("evidence_cards", {}).get("card_count") != 57:
        errors.append("SF legacy hotspot evidence-card count is not 57")

    point_layer_contracts = {
        "sf_bay_anomaly_points_2025-05-01_to_2025-05-07.geojson": 47867,
        "sf_bay_encounters_2025-05-01_to_2025-05-07.geojson": 56221,
        "tokyo_bay_anomaly_points_2024-08-01_to_2024-08-07.geojson": 41736,
        "tokyo_bay_encounters_2024-08-01_to_2024-08-07.geojson": 28515,
    }
    for filename, source_count in point_layer_contracts.items():
        data = json.loads((ROOT / "web/data" / filename).read_text(encoding="utf-8"))
        metadata = data.get("metadata", {})
        if metadata.get("source_count") != source_count:
            errors.append(f"incorrect source_count metadata in web/data/{filename}")
        if metadata.get("published_count") != len(data.get("features", [])):
            errors.append(f"incorrect published_count metadata in web/data/{filename}")
        if metadata.get("geojson_limit") != 2000 or not metadata.get("selection_rule"):
            errors.append(f"missing point-layer selection contract in web/data/{filename}")

    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()
    locked = (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").casefold()
    for publication_dependency in ("python-docx", "lxml"):
        if publication_dependency in requirements or publication_dependency in locked:
            errors.append(f"publication-only dependency remains public: {publication_dependency}")

    public_sources = (ROOT / "docs/DATA_SOURCES.md").read_text(encoding="utf-8")
    for stale_reference in (
        "docs/TOKYO_BAY_VALIDATION.md",
        "流程可迁移",
        "transferability evidence",
        "port risk analysis",
    ):
        if stale_reference.casefold() in public_sources.casefold():
            errors.append(f"stale public source wording or link: {stale_reference}")

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
