#!/usr/bin/env python3
"""Download public AIS daily files for the project.

The script intentionally downloads raw public files without transforming them.
Downstream scripts should crop the study area and convert data into analysis
formats such as Parquet.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import urllib.error
import urllib.request
from pathlib import Path


NOAA_HTDATA_BASE = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler"


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    days = (end - start).days
    return [start + dt.timedelta(days=i) for i in range(days + 1)]


def noaa_daily_url(day: dt.date) -> str:
    """Return the NOAA daily AIS URL for a date.

    NOAA introduced .csv.zst daily files for 2025. Older HTData pages commonly
    expose ZIP files named AIS_YYYY_MM_DD.zip.
    """
    year = day.year
    if year >= 2025:
        filename = f"ais-{day:%Y-%m-%d}.csv.zst"
    else:
        filename = f"AIS_{day:%Y_%m_%d}.zip"
    return f"{NOAA_HTDATA_BASE}/{year}/{filename}"


def download_file(url: str, output_path: Path, overwrite: bool = False) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        print(f"exists: {output_path}")
        return

    tmp_path = output_path.with_suffix(output_path.suffix + ".part")
    print(f"download: {url}", flush=True)
    try:
        bytes_written = 0
        with urllib.request.urlopen(url, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            expected_size = int(content_length) if content_length else None
            with tmp_path.open("wb") as dst:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
                    bytes_written += len(chunk)
        if expected_size is not None and bytes_written != expected_size:
            raise RuntimeError(
                f"incomplete download for {url}: got {bytes_written} bytes, expected {expected_size}"
            )
        tmp_path.replace(output_path)
    except (urllib.error.URLError, TimeoutError) as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(f"failed to download {url}: {exc}") from exc
    except RuntimeError:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def output_name(day: dt.date) -> str:
    if day.year >= 2025:
        return f"ais-{day:%Y-%m-%d}.csv.zst"
    return f"AIS_{day:%Y_%m_%d}.zip"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download public NOAA AIS daily files.")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", type=parse_date, required=True, help="End date, YYYY-MM-DD.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/raw/noaa"),
        help="Output directory for raw AIS files.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print URLs and output paths without downloading.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    for day in iter_dates(args.start, args.end):
        url = noaa_daily_url(day)
        output_path = args.out_dir / str(day.year) / output_name(day)
        if args.dry_run:
            print(f"{url} -> {output_path}")
            continue
        download_file(url, output_path, overwrite=args.overwrite)

    return 0


if __name__ == "__main__":
    sys.exit(main())
