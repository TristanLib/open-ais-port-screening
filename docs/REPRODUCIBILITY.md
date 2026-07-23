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

## Frozen review-v9 method

`METHOD_PROTOCOL.md` fixes the causal common-time construction, complete
within-2-nm pair search, segment-safe ground-track course, strict-future
interpolation, primary observability, continuous minimum-distance solver,
matched controls, and dual-objective fusion gate. Do not tune those definitions
to reproduce a preferred headline value.

## San Francisco Bay review-v9 validation

After downloading the public NOAA files described in `configs/data_manifest.yml`,
run the daily pipeline and the review-v9 encounter chain:

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

.venv/bin/python src/geometric_control_evaluation.py \
  --opportunity-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_pair_opportunity_records.csv \
  --start 2025-05-01 --end 2025-05-07 --dataset-prefix sf_bay_ais \
  --min-common-fraction 0.70 --max-uncovered-gap-s 180 --predicted-time-tolerance-s 60 \
  --output-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_geometric_control_evaluation.json

.venv/bin/python src/dual_objective_rank_evaluation.py \
  --start 2025-05-01 --end 2025-05-07 --dataset-prefix sf_bay_ais \
  --anomaly-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_anomaly_points.csv \
  --encounter-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounters.csv \
  --pair-opportunity-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_pair_opportunities.csv \
  --backtest-csv outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_encounter_backtest.csv \
  --bbox -122.62 37.42 -121.92 38.18 \
  --output-json outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_dual_objective_rank_evaluation.json \
  --output-md outputs/tables/sf_bay_ais_2025-05-01_to_2025-05-07_dual_objective_rank_evaluation.md
```

Traffic-pattern, behavior-candidate, hotspot, and Web commands use the same
fixed paths and parameters recorded in the public manifests.

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
