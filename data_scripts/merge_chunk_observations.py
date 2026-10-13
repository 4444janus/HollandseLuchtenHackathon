#!/usr/bin/env python3
"""Merge chunked monthly observation exports into one yearly CSV.

By default, this script reads files like:
  data_exports/chunks/<chunk-label>/full/sensor_observations_long.csv
and merges them into:
  data_exports/full_year/sensor_observations_long.csv

Chunk labels are sorted lexicographically, which works for labels such as:
  2025-01-01_2025-02-01

Usage example:
  python data_scripts/merge_chunk_observations.py

Custom example:
  python data_scripts/merge_chunk_observations.py \
    --chunks-root data_exports/chunks \
    --relative-file full/sensor_observations_long.csv \
    --out data_exports/full_year/sensor_observations_long.csv \
    --dedupe-keys sensor_id,timestamp_utc,pollutant_name,observation_id
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


def _iter_chunk_files(chunks_root: Path, relative_file: str) -> List[Path]:
    if not chunks_root.exists():
        raise FileNotFoundError(f"Chunks root not found: {chunks_root}")

    files: List[Path] = []
    for child in sorted(chunks_root.iterdir()):
        if not child.is_dir():
            continue
        candidate = child / relative_file
        if candidate.exists():
            files.append(candidate)

    return files


def _parse_csv_list(spec: str) -> List[str]:
    return [p.strip() for p in spec.split(",") if p.strip()]


def _build_key(row: Dict[str, str], key_cols: List[str]) -> Tuple[str, ...]:
    return tuple((row.get(col) or "").strip() for col in key_cols)


def _validate_required_headers(headers: Iterable[str], required: List[str]) -> None:
    present = set(headers)
    missing = [col for col in required if col not in present]
    if missing:
        raise ValueError(f"Missing required columns in input CSV header: {missing}")


def merge_chunk_csvs(
    chunk_files: List[Path],
    out_path: Path,
    dedupe_keys: Optional[List[str]],
) -> None:
    if not chunk_files:
        raise ValueError("No chunk files found to merge")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    seen: set[Tuple[str, ...]] = set()
    total_rows = 0
    written_rows = 0
    skipped_duplicates = 0

    with out_path.open("w", encoding="utf-8", newline="") as out_file:
        writer: Optional[csv.DictWriter] = None
        output_headers: List[str] = []

        for i, in_path in enumerate(chunk_files, start=1):
            with in_path.open("r", encoding="utf-8", newline="") as in_file:
                reader = csv.DictReader(in_file)
                headers = list(reader.fieldnames or [])
                if not headers:
                    print(f"Skipping empty/no-header file: {in_path}")
                    continue

                if writer is None:
                    output_headers = headers
                    if dedupe_keys:
                        _validate_required_headers(output_headers, dedupe_keys)
                    writer = csv.DictWriter(out_file, fieldnames=output_headers, extrasaction="ignore")
                    writer.writeheader()
                else:
                    # Keep strict schema consistency to avoid silent corruption.
                    if headers != output_headers:
                        raise ValueError(
                            "Header mismatch between chunk files. "
                            f"Expected {output_headers} but found {headers} in {in_path}"
                        )

                file_rows = 0
                file_written = 0
                for row in reader:
                    total_rows += 1
                    file_rows += 1

                    if dedupe_keys:
                        key = _build_key(row, dedupe_keys)
                        if key in seen:
                            skipped_duplicates += 1
                            continue
                        seen.add(key)

                    writer.writerow(row)
                    written_rows += 1
                    file_written += 1

                print(
                    f"[{i}/{len(chunk_files)}] {in_path}: read={file_rows} written={file_written}"
                )

    print("Merge complete")
    print(f"Chunk files processed: {len(chunk_files)}")
    print(f"Rows read: {total_rows}")
    print(f"Rows written: {written_rows}")
    if dedupe_keys:
        print(f"Duplicate rows skipped: {skipped_duplicates}")
        print(f"Dedupe key columns: {dedupe_keys}")
    print(f"Output: {out_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge chunked observation CSVs into a single file")
    parser.add_argument(
        "--chunks-root",
        default="data_exports/chunks",
        help="Root directory containing chunk subfolders",
    )
    parser.add_argument(
        "--relative-file",
        default="full/sensor_observations_long.csv",
        help="Path to merge inside each chunk folder",
    )
    parser.add_argument(
        "--out",
        default="data_exports/full_year/sensor_observations_long.csv",
        help="Output merged CSV path",
    )
    parser.add_argument(
        "--dedupe-keys",
        default="sensor_id,timestamp_utc,pollutant_name,observation_id",
        help="Comma-separated key columns for deduplication. Set empty string to disable.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    chunks_root = Path(args.chunks_root)
    out_path = Path(args.out)
    dedupe_keys = _parse_csv_list(args.dedupe_keys)
    if len(dedupe_keys) == 0:
        dedupe_keys_opt: Optional[List[str]] = None
    else:
        dedupe_keys_opt = dedupe_keys

    try:
        chunk_files = _iter_chunk_files(chunks_root, args.relative_file)
        if not chunk_files:
            print(
                f"No chunk files found at {chunks_root}/*/{args.relative_file}. "
                "Run chunk export first."
            )
            return 2

        merge_chunk_csvs(
            chunk_files=chunk_files,
            out_path=out_path,
            dedupe_keys=dedupe_keys_opt,
        )
        return 0
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
