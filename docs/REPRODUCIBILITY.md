# Reproduction Guide

## Environment

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-lock.txt
```

Python 3.12+ is recommended. The NOAA `.zst` workflow additionally requires
the `zstd` command-line tool.

## Offline Smoke Test

```bash
.venv/bin/python src/sample_pipeline_smoke.py --clean-output
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/verify_public_repository.py
```

The smoke sample is synthetic and exercises crop, cleaning, feature creation,
track filtering, and grid aggregation without network access.

## San Francisco Bay Pipeline

```bash
START=2025-05-01
END=2025-05-07

.venv/bin/python src/run_daily_pipeline.py \
  --start "$START" \
  --end "$END" \
  --bbox -122.62 37.42 -121.92 38.18 \
  --summary-json outputs/tables/sf_bay_pipeline_summary.json
```

Follow with the traffic-pattern, anomaly, encounter, backtest, hotspot, and Web
export CLIs in `src/`. Each command provides `--help`; fixed reference
parameters are recorded in `configs/data_manifest.yml`.

## Tokyo Bay Pipeline

```bash
.venv/bin/python src/tokyo_bay_adapter.py download
.venv/bin/python src/tokyo_bay_adapter.py inspect \
  --input data/raw/tokyo_bay/figshare_v2/ais_messages_tokyobay_2024.parquet
.venv/bin/python src/run_tokyo_bay_validation.py
```

Reference parameters and expected aggregate results are in
`configs/data_manifest_tokyo_bay.yml`.

## Verification Levels

- Unit tests verify dataset-prefix handling, Tokyo schema adaptation,
  synchronized CPA/TCPA state alignment, strict-future evaluation, evidence
  tiers, exposure normalization, and ranking comparisons.
- The sample smoke test verifies the lightweight local pipeline.
- The public-repository verifier checks release boundaries, synchronized
  reference configuration, and Web-data sanitization.
- A full reference reproduction requires the public external datasets and is
  substantially larger than CI.
