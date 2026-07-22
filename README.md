# Open AIS Port Screening Explorer

[![CI](https://github.com/TristanLib/open-ais-port-screening/actions/workflows/ci.yml/badge.svg)](https://github.com/TristanLib/open-ais-port-screening/actions/workflows/ci.yml)

Research code and sanitized demonstration assets for converting public
historical AIS archives into an auditable queue of behavior and encounter
candidates for qualified human review.

The workflow is designed for retrospective research and first-pass evidence
screening. It does **not** produce accident probabilities, near-miss labels,
COLREG compliance findings, navigational advice, enforcement findings, or a
real-time VTS service.

## What The Pipeline Does

1. Downloads or adapts public AIS data.
2. Crops, cleans, segments, and featurizes vessel tracks.
3. Learns empirical traffic-pattern cells.
4. Scores transparent behavior-evidence indicators.
5. Synchronizes vessel states before CPA/TCPA candidate screening.
6. Evaluates candidates on a strict-future common time grid.
7. Normalizes behavior and encounter evidence by their matching exposures.
8. Produces review-priority cells and sanitized Web layers.

## Reference Runs

The repository records two seven-day reference runs. These values are
reproduction targets, not universal safety benchmarks.

| Study area | Window | Clean AIS points | Encounter episodes | Strict-future support | Review-priority cells |
|---|---|---:|---:|---:|---:|
| San Francisco Bay | 2025-05-01 to 2025-05-07 | 1,114,862 | 18,386 | 12,495 / 17,444 (71.6%) | 56 |
| Tokyo Bay | 2024-08-01 to 2024-08-07 | 475,530 | 8,929 | 5,428 / 8,178 (66.4%) | 39 |

`Strict-future support` means that an observable candidate episode reached an
interpolated separation of at most 0.5 nautical miles during the future
15-minute evaluation window. It is a geometric consistency check, not
prediction accuracy or expert validation.

## Quick Verification

Python 3.12 or newer is recommended. The sample workflow uses synthetic AIS
records and does not download external data.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python src/sample_pipeline_smoke.py --clean-output
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/verify_public_repository.py
```

## Full Data Workflows

### San Francisco Bay

The primary source is NOAA MarineCadastre historical AIS. Inspect the planned
URLs without downloading:

```bash
.venv/bin/python src/download.py \
  --start 2025-05-01 \
  --end 2025-05-07 \
  --dry-run
```

Run the daily download, crop, clean, feature, density, and track pipeline:

```bash
.venv/bin/python src/run_daily_pipeline.py \
  --start 2025-05-01 \
  --end 2025-05-07 \
  --bbox -122.62 37.42 -121.92 38.18 \
  --summary-json outputs/tables/sf_bay_pipeline_summary.json
```

NOAA source files use Zstandard compression, so the full workflow also needs
the `zstd` command-line tool.

### Tokyo Bay

The adapter downloads the checksum-pinned Figshare v2 source and writes the
same daily CSV contract used by the NOAA workflow:

```bash
.venv/bin/python src/tokyo_bay_adapter.py download
.venv/bin/python src/run_tokyo_bay_validation.py
```

The Tokyo source does not provide native COG or heading. Movement bearing is
derived from consecutive positions. This run demonstrates cross-source
executability and common output generation; it does not establish threshold
transfer or comparative port safety.

See [Reproduction](docs/REPRODUCIBILITY.md) and
[Data Sources](docs/DATA_SOURCES.md) for the complete commands and provenance
boundary.

## Web Demo

The tracked `web/data/` files are sanitized, lightweight derivatives. They do
not contain MMSI, track IDs, vessel names, callsigns, IMO numbers, vessel-pair
coordinates, or exact observation timestamps.

```bash
python3 -m http.server 5173 --directory web
```

Open `http://localhost:5173` and switch between San Francisco Bay and Tokyo Bay.

## Repository Layout

```text
configs/   Study areas, data provenance, parameters, and reference results
data/      Synthetic smoke sample and local data-workspace notes
docs/      Reproduction and public-data boundary documentation
scripts/   Public-repository verification
src/       Data, analysis, validation, sanitization, and Web-export code
tests/     Unit and bundle tests
web/       Static two-waterway demonstration with sanitized derived layers
```

## Public Boundary

This repository intentionally excludes manuscripts, abstracts, reviewer
materials, submission correspondence, raw and processed full-resolution AIS,
MMSI-level analytical tables, and local environments. Source data remain under
their providers' terms. See [Public Data Boundary](docs/PUBLIC_DATA_BOUNDARY.md).

## License

Code and repository documentation are available under the MIT License. NOAA
and Figshare source data are not relicensed by this repository.
