# Data Sources

## NOAA MarineCadastre AIS

- Product: Nationwide Automatic Identification System historical AIS
- Reference year: 2025
- Reference window: 2025-05-01 through 2025-05-07
- Study area: San Francisco Bay and Port of Oakland approaches
- Metadata: <https://www.fisheries.noaa.gov/inport/item/77594>
- Files: <https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2025/>

The repository does not redistribute NOAA source files. `src/download.py`
builds deterministic daily URLs, and the full workflow stores the downloads
under ignored `data/raw/` paths.

## Tokyo Bay Figshare v2

- Dataset: High-Resolution Mapping of Port Dynamics from Open-Access AIS Data in Tokyo Bay
- DOI: <https://doi.org/10.6084/m9.figshare.29037401.v2>
- File ID: `57954736`
- Expected size: `65,622,524` bytes
- Expected MD5: `460973e34735cb608289fc3e5438dbcd`
- License: CC BY 4.0
- Reference window: 2024-08-01 through 2024-08-07

`src/tokyo_bay_adapter.py` validates the file size and checksum before
canonicalization. The dataset lacks native COG and heading, so movement bearing
is derived from consecutive positions.

## Data Boundary

Both sources are historical and may contain reception gaps, irregular report
intervals, missing fields, and self-reported attributes. They are suitable for
offline research but must not be treated as live navigation or enforcement
feeds.
