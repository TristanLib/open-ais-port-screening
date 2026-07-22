# Sample Data

`smoke_ais.csv` is a tiny synthetic AIS-like file for pipeline smoke tests.

It is not used as research evidence and does not contain real vessel tracks.
Its purpose is only to verify that the basic local scripts can read, crop,
clean, segment, filter, and aggregate AIS-shaped data without downloading NOAA
raw files.

Run:

```bash
python3 src/sample_pipeline_smoke.py
```
