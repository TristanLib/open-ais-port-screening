#!/usr/bin/env python3
"""Download and normalize the public Tokyo Bay AIS dataset from Figshare."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, TextIO

from download import download_file

try:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
except ModuleNotFoundError:  # pragma: no cover - exercised through the CLI error.
    pa = None
    pc = None
    pq = None


FIGSHARE_ARTICLE_ID = 29037401
FIGSHARE_VERSION = 2
FIGSHARE_FILE_ID = 57954736
FIGSHARE_DOWNLOAD_URL = f"https://ndownloader.figshare.com/files/{FIGSHARE_FILE_ID}"
FIGSHARE_DOI = "10.6084/m9.figshare.29037401.v2"
EXPECTED_SIZE_BYTES = 65_622_524
EXPECTED_MD5 = "460973e34735cb608289fc3e5438dbcd"
DEFAULT_DATASET_PREFIX = "tokyo_bay_ais"
SOURCE_DATASET_LABEL = "figshare_tokyo_bay_2024_v2"
SOURCE_FIELDS = ["lat", "lon", "timestamp", "mmsi", "sog", "type", "destination"]
CANONICAL_FIELDS = [
    "mmsi",
    "base_date_time",
    "longitude",
    "latitude",
    "sog",
    "cog",
    "heading",
    "vessel_name",
    "imo",
    "call_sign",
    "vessel_type",
    "status",
    "length",
    "width",
    "draft",
    "cargo",
    "transceiver",
    "destination",
    "source_dataset",
]


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value}") from exc


def iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)]


def require_pyarrow() -> None:
    if pa is None or pc is None or pq is None:
        raise RuntimeError("PyArrow is required; install project requirements before reading Parquet.")


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_download(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(path)
    actual_size = path.stat().st_size
    actual_md5 = md5_file(path)
    if actual_size != EXPECTED_SIZE_BYTES or actual_md5 != EXPECTED_MD5:
        raise RuntimeError(
            f"Tokyo Bay dataset checksum mismatch: size={actual_size}, md5={actual_md5}"
        )
    return {
        "path": str(path),
        "size_bytes": actual_size,
        "md5": actual_md5,
        "figshare_file_id": FIGSHARE_FILE_ID,
        "figshare_version": FIGSHARE_VERSION,
        "doi": FIGSHARE_DOI,
    }


def download_tokyo_bay(path: Path, overwrite: bool = False) -> dict[str, object]:
    if path.exists() and not overwrite:
        return verify_download(path)
    download_file(FIGSHARE_DOWNLOAD_URL, path, overwrite=overwrite)
    return verify_download(path)


def format_float(value: float | None, digits: int = 6) -> str:
    if value is None or not math.isfinite(value):
        return ""
    if value == 0:
        return "0"
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def normalize_vessel_type(value: int | None) -> str:
    if value is None or not 0 <= value <= 99:
        return ""
    return str(value)


def timestamp_bounds(start: dt.date, end: dt.date) -> tuple[int, int]:
    start_time = dt.datetime.combine(start, dt.time.min, tzinfo=dt.timezone.utc)
    end_time = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, tzinfo=dt.timezone.utc)
    return int(start_time.timestamp()), int(end_time.timestamp())


def row_group_overlaps(metadata: Any, timestamp_index: int, start_epoch: int, end_epoch: int) -> bool:
    column = metadata.column(timestamp_index)
    statistics = column.statistics
    if statistics is None or not statistics.has_min_max:
        return True
    return int(statistics.max) >= start_epoch and int(statistics.min) < end_epoch


def convert_parquet(
    input_path: Path,
    output_dir: Path,
    start: dt.date,
    end: dt.date,
    bbox: tuple[float, float, float, float],
    stats_path: Path | None = None,
    dataset_prefix: str = DEFAULT_DATASET_PREFIX,
    overwrite: bool = False,
    batch_size: int = 131_072,
) -> dict[str, object]:
    """Convert a date/bbox slice to the canonical daily AIS CSV contract.

    Source timestamps are POSIX seconds and are rendered in UTC. COG and
    heading are unavailable in the source; downstream trajectory features
    derive bearing from consecutive positions.
    """

    require_pyarrow()
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    days = iter_dates(start, end)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {day: output_dir / f"{dataset_prefix}_{day.isoformat()}.csv" for day in days}
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"output already exists; use --overwrite: {existing[0]}")

    parquet_file = pq.ParquetFile(input_path)
    missing_fields = [field for field in SOURCE_FIELDS if field not in parquet_file.schema_arrow.names]
    if missing_fields:
        raise RuntimeError(f"Tokyo Bay Parquet is missing required fields: {missing_fields}")

    start_epoch, end_epoch = timestamp_bounds(start, end)
    min_lon, min_lat, max_lon, max_lat = bbox
    timestamp_index = parquet_file.schema_arrow.names.index("timestamp")
    daily_rows = {day.isoformat(): 0 for day in days}
    source_rows_scanned = 0
    rows_in_date_range = 0
    outside_bbox_rows = 0
    output_rows = 0
    row_groups_scanned = 0
    part_paths = {day: path.with_suffix(path.suffix + ".part") for day, path in outputs.items()}
    handles: dict[dt.date, TextIO] = {}
    writers: dict[dt.date, csv.DictWriter] = {}

    try:
        for day, part_path in part_paths.items():
            part_path.unlink(missing_ok=True)
            handle = part_path.open("w", encoding="utf-8", newline="")
            handles[day] = handle
            writer = csv.DictWriter(handle, fieldnames=CANONICAL_FIELDS)
            writer.writeheader()
            writers[day] = writer

        for row_group_index in range(parquet_file.metadata.num_row_groups):
            row_group_metadata = parquet_file.metadata.row_group(row_group_index)
            if not row_group_overlaps(row_group_metadata, timestamp_index, start_epoch, end_epoch):
                continue
            row_groups_scanned += 1

            for batch in parquet_file.iter_batches(
                batch_size=batch_size,
                row_groups=[row_group_index],
                columns=SOURCE_FIELDS,
            ):
                source_rows_scanned += batch.num_rows
                table = pa.Table.from_batches([batch])
                timestamps = table["timestamp"]
                date_mask = pc.and_(
                    pc.greater_equal(timestamps, pa.scalar(start_epoch, type=timestamps.type)),
                    pc.less(timestamps, pa.scalar(end_epoch, type=timestamps.type)),
                )
                table = table.filter(date_mask)
                rows_in_date_range += table.num_rows
                if not table.num_rows:
                    continue

                columns = table.to_pydict()
                for lat_value, lon_value, timestamp_value, mmsi_value, sog_value, type_value, destination in zip(
                    columns["lat"],
                    columns["lon"],
                    columns["timestamp"],
                    columns["mmsi"],
                    columns["sog"],
                    columns["type"],
                    columns["destination"],
                    strict=True,
                ):
                    lat = float(lat_value)
                    lon = float(lon_value)
                    if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
                        outside_bbox_rows += 1
                        continue

                    timestamp = int(timestamp_value)
                    timestamp_utc = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc)
                    day = timestamp_utc.date()
                    if day not in writers:
                        continue
                    writers[day].writerow(
                        {
                            "mmsi": str(int(mmsi_value)),
                            "base_date_time": timestamp_utc.strftime("%Y-%m-%d %H:%M:%S"),
                            "longitude": format_float(lon),
                            "latitude": format_float(lat),
                            "sog": format_float(float(sog_value)) if sog_value is not None else "",
                            "cog": "",
                            "heading": "",
                            "vessel_name": "",
                            "imo": "",
                            "call_sign": "",
                            "vessel_type": normalize_vessel_type(
                                int(type_value) if type_value is not None else None
                            ),
                            "status": "",
                            "length": "",
                            "width": "",
                            "draft": "",
                            "cargo": "",
                            "transceiver": "",
                            "destination": str(destination or ""),
                            "source_dataset": SOURCE_DATASET_LABEL,
                        }
                    )
                    daily_rows[day.isoformat()] += 1
                    output_rows += 1
    except Exception:
        for handle in handles.values():
            handle.close()
        for part_path in part_paths.values():
            part_path.unlink(missing_ok=True)
        raise
    else:
        for handle in handles.values():
            handle.close()
        for day, output_path in outputs.items():
            part_paths[day].replace(output_path)

    stats: dict[str, object] = {
        "source": {
            "provider": "Figshare",
            "doi": FIGSHARE_DOI,
            "article_id": FIGSHARE_ARTICLE_ID,
            "version": FIGSHARE_VERSION,
            "file_id": FIGSHARE_FILE_ID,
            "license": "CC BY 4.0",
        },
        "input_path": str(input_path),
        "dataset_prefix": dataset_prefix,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": "UTC",
        "bbox": {
            "min_lon": min_lon,
            "min_lat": min_lat,
            "max_lon": max_lon,
            "max_lat": max_lat,
        },
        "source_rows_total": parquet_file.metadata.num_rows,
        "source_rows_scanned": source_rows_scanned,
        "row_groups_scanned": row_groups_scanned,
        "rows_in_date_range": rows_in_date_range,
        "outside_bbox_rows": outside_bbox_rows,
        "output_rows": output_rows,
        "daily_rows": daily_rows,
        "output_files": [str(outputs[day]) for day in days],
        "field_notes": {
            "sog": "self-reported source value; missing values remain blank and are removed by cleaning",
            "cog": "not provided by source",
            "heading": "not provided by source",
            "bearing": "derived downstream from consecutive positions",
            "vessel_type": "negative or out-of-range source values normalized to blank",
        },
    }
    if stats_path is not None:
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stats


def inspect_parquet(path: Path) -> dict[str, object]:
    require_pyarrow()
    parquet_file = pq.ParquetFile(path)
    return {
        "path": str(path),
        "rows": parquet_file.metadata.num_rows,
        "row_groups": parquet_file.metadata.num_row_groups,
        "schema": {field.name: str(field.type) for field in parquet_file.schema_arrow},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the public Tokyo Bay AIS validation dataset.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser("download", help="Download and verify the Figshare Parquet.")
    download_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw/tokyo_bay/figshare_v2/ais_messages_tokyobay_2024.parquet"),
    )
    download_parser.add_argument("--overwrite", action="store_true")

    inspect_parser = subparsers.add_parser("inspect", help="Print Parquet schema and row counts.")
    inspect_parser.add_argument("--input", type=Path, required=True)

    convert_parser = subparsers.add_parser("convert", help="Convert a date slice to canonical daily CSV files.")
    convert_parser.add_argument("--input", type=Path, required=True)
    convert_parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    convert_parser.add_argument("--start", type=parse_date, required=True)
    convert_parser.add_argument("--end", type=parse_date, required=True)
    convert_parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        required=True,
    )
    convert_parser.add_argument("--stats-json", type=Path)
    convert_parser.add_argument("--dataset-prefix", default=DEFAULT_DATASET_PREFIX)
    convert_parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "download":
        result = download_tokyo_bay(args.output, overwrite=args.overwrite)
    elif args.command == "inspect":
        result = inspect_parquet(args.input)
    else:
        result = convert_parquet(
            input_path=args.input,
            output_dir=args.output_dir,
            start=args.start,
            end=args.end,
            bbox=tuple(args.bbox),
            stats_path=args.stats_json,
            dataset_prefix=args.dataset_prefix,
            overwrite=args.overwrite,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
