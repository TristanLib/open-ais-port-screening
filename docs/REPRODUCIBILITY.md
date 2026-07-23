# Reproduction Guide

## Environment and offline checks

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python src/sample_pipeline_smoke.py --clean-output
.venv/bin/python -m unittest discover -s tests -v
```

The synthetic smoke sample exercises crop, cleaning, feature construction,
track filtering, and grid aggregation without downloading external AIS data.

## Frozen review-v10 method

`METHOD_PROTOCOL.md` fixes the causal common-time construction, complete
within-2-nm pair search, segment-safe ground-track course, strict-future
interpolation, primary observability, continuous minimum-distance solver,
matched controls, and dual-objective fusion gate. Do not tune those definitions
to reproduce a preferred headline value.

Review-v10 additionally removes selected-anchor composition from any
all-opportunity recall interpretation, uses prespecified control calipers and a
bipartite pair/day dependency bootstrap, performs a six-day traffic-pattern
refit in every leave-one-day-out fold, and binds evidence-card current distance
to the causal prediction state.

## San Francisco Bay review-v10 validation

After downloading the public NOAA files described in `configs/data_manifest.yml`,
run the daily pipeline and the review-v10 encounter chain:

```bash
.venv/bin/python src/run_daily_pipeline.py --start 2025-05-01 --end 2025-05-07 \
  --bbox -122.62 37.42 -121.92 38.18 \
  --summary-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_pipeline_summary.json

.venv/bin/python src/encounter_risk.py --start 2025-05-01 --end 2025-05-07 \
  --time-bin-seconds 60 --max-state-skew-s 60 \
  --analysis-bbox -122.62 37.42 -121.92 38.18 --analysis-cell-size-deg 0.005 \
  --output-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounters.csv \
  --opportunity-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_pair_opportunities.csv \
  --opportunity-records-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_pair_opportunity_records.csv \
  --output-geojson outputs/web/sf_bay_encounters_2025-05-01_to_2025-05-07.geojson \
  --stats-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounters_stats.json

.venv/bin/python src/encounter_backtest.py --start 2025-05-01 --end 2025-05-07 \
  --encounter-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounters.csv \
  --lookahead-min 15 --evaluation-step-s 30 --max-interpolation-gap-s 180 \
  --min-common-fraction 0.70 --max-uncovered-gap-s 180 --predicted-time-tolerance-s 60 \
  --output-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounter_backtest.csv \
  --stats-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounter_backtest_stats.json

.venv/bin/python src/traffic_patterns.py --start 2025-05-01 --end 2025-05-07 \
  --bbox -122.62 37.42 -121.92 38.18 \
  --output-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_traffic_patterns.csv \
  --output-geojson outputs/web/sf_bay_traffic_patterns_2025-05-01_to_2025-05-07.geojson \
  --stats-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_traffic_patterns_stats.json

.venv/bin/python src/anomaly_score.py --start 2025-05-01 --end 2025-05-07 \
  --bbox -122.62 37.42 -121.92 38.18 \
  --patterns-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_traffic_patterns.csv \
  --output-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_anomaly_points.csv \
  --output-geojson outputs/web/sf_bay_anomaly_points_2025-05-01_to_2025-05-07.geojson \
  --stats-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_anomaly_stats.json

.venv/bin/python src/geometric_control_evaluation.py \
  --opportunity-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_pair_opportunity_records.csv \
  --processed-dir data/processed --start 2025-05-01 --end 2025-05-07 \
  --dataset-prefix sf_bay_ais --episode-gap-min 15 \
  --control-exclusion-min 15 --control-thinning-min 15 \
  --reference-time-caliper-min 60 --relative-speed-caliper-kn 5 \
  --closing-speed-caliper-kn 2.5 --bootstrap-iterations 2000 \
  --bootstrap-seed 20260722 --lookahead-min 15 --evaluation-step-s 30 \
  --max-interpolation-gap-s 180 --min-common-fraction 0.70 \
  --max-uncovered-gap-s 180 --predicted-time-tolerance-s 60 \
  --output-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_geometric_control_anchors.csv \
  --output-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_geometric_control_evaluation.json

.venv/bin/python src/dual_objective_rank_evaluation.py \
  --start 2025-05-01 --end 2025-05-07 --dataset-prefix sf_bay_ais \
  --processed-dir data/processed --tables-dir outputs/tables \
  --anomaly-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_anomaly_points.csv \
  --encounter-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounters.csv \
  --pair-opportunity-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_pair_opportunities.csv \
  --backtest-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounter_backtest.csv \
  --bbox -122.62 37.42 -121.92 38.18 --cell-size-deg 0.005 \
  --pattern-min-sog 1.0 --pattern-min-points 50 --pattern-min-tracks 5 \
  --track-min-points 20 --moving-sog 1.0 --min-anomaly-score 0.35 \
  --low-support-weight 0.25 \
  --output-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_dual_objective_rank_evaluation.json \
  --output-md outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_dual_objective_rank_evaluation.md

.venv/bin/python src/evidence_cards.py \
  --encounter-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounters.csv \
  --backtest-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounter_backtest.csv \
  --processed-dir data/processed --dataset-prefix sf_bay_ais --dataset-id sf_bay \
  --max-encounter-cards 12 \
  --encounter-card-output-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounter_evidence_cards.json \
  --encounter-card-web-json outputs/web/encounter_evidence_cards.json web/data/encounter_evidence_cards.json

.venv/bin/python src/risk_hotspots.py --start 2025-05-01 --end 2025-05-07 \
  --bbox -122.62 37.42 -121.92 38.18 \
  --anomaly-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_anomaly_points.csv \
  --encounter-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounters.csv \
  --pair-opportunity-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_pair_opportunities.csv \
  --output-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_fused_risk_hotspots.csv \
  --output-geojson outputs/web/sf_bay_fused_risk_hotspots_2025-05-01_to_2025-05-07.geojson \
  --stats-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_fused_risk_hotspots_stats.json

.venv/bin/python src/export_web_layers.py --start 2025-05-01 --end 2025-05-07 \
  --bbox -122.62 37.42 -121.92 38.18
.venv/bin/python src/build_web_datasets.py
```

The public manifests record the fixed study windows, bounding boxes, thresholds,
input URLs, and checksums. Generated full-resolution tables remain local.

## Tokyo Bay and Web

```bash
.venv/bin/python src/tokyo_bay_adapter.py download
.venv/bin/python src/run_tokyo_bay_validation.py --overwrite
.venv/bin/python src/build_web_datasets.py
python3 -m http.server 5173 --directory web
```

Tokyo Bay is a cross-source executability and common-output-schema check only.
It is not evidence of threshold transfer, cross-port performance, or comparative
port safety.

## Output boundary

Public input URLs, checksums, study windows, bounding boxes, and fixed method
parameters are recorded under `configs/`. Full runs write large local artifacts
under ignored `data/raw/`, `data/processed/`, and `outputs/` directories. Those
artifacts are not included in this archive.
