#!/usr/bin/env python3
"""Normalize Luchtmeetnet exports to reference_station_observations.csv format.

This script converts a raw Luchtmeetnet CSV export (from API/batch download)
to the challenge schema used in data_exports/full/reference_station_observations.csv.

Expected output columns:
- reference_station_id
- timestamp_utc
- pollutant_name
- value_reference
- unit
- qc_flag_code
- data_provider

Usage:
	python data_scripts/luchtmeetnet.py \
		--input path/to/luchtmeetnet_raw.csv \
		--out data_exports/full/reference_station_observations.csv
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def _parse_iso_utc(value: str) -> Optional[str]:
	text = (value or "").strip()
	if not text:
		return None

	try:
		if text.endswith("Z"):
			dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
		else:
			dt = datetime.fromisoformat(text)
			if dt.tzinfo is None:
				dt = dt.replace(tzinfo=timezone.utc)
		return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
	except ValueError:
		return None


def _to_float(value: str) -> Optional[float]:
	text = (value or "").strip().replace(",", ".")
	if not text:
		return None
	try:
		return float(text)
	except ValueError:
		return None


def _first_present(headers: Iterable[str], candidates: List[str]) -> Optional[str]:
	lowered = {h.lower(): h for h in headers}
	for c in candidates:
		found = lowered.get(c.lower())
		if found:
			return found
	return None


def _detect_columns(headers: List[str]) -> Dict[str, Optional[str]]:
	return {
		"station": _first_present(headers, ["reference_station_id", "station_number", "device_id", "station_id"]),
		"timestamp": _first_present(headers, ["timestamp_utc", "timestamp", "timestamp_measured", "time"]),
		"pm25": _first_present(headers, ["pm25", "PM25", "lml_pm25"]),
		"pm10": _first_present(headers, ["pm10", "PM10", "lml_pm10"]),
		"no2": _first_present(headers, ["no2", "NO2"]),
	}


def _append_if_valid(
	rows_out: List[Dict[str, str]],
	station_id: str,
	timestamp_utc: str,
	pollutant_name: str,
	value: Optional[float],
	unit: str,
	provider: str,
) -> None:
	if value is None:
		return
	rows_out.append(
		{
			"reference_station_id": station_id,
			"timestamp_utc": timestamp_utc,
			"pollutant_name": pollutant_name,
			"value_reference": value,
			"unit": unit,
			"qc_flag_code": "",
			"data_provider": provider,
		}
	)


def normalize(input_path: Path, out_path: Path, provider: str) -> None:
	with input_path.open("r", encoding="utf-8", newline="") as f:
		reader = csv.DictReader(f)
		headers = list(reader.fieldnames or [])
		rows = list(reader)

	if not headers:
		raise ValueError(f"Input file has no header: {input_path}")

	cols = _detect_columns(headers)
	if not cols["station"] or not cols["timestamp"]:
		raise ValueError(
			"Could not detect required station/timestamp columns. "
			f"Detected: station={cols['station']} timestamp={cols['timestamp']}"
		)

	out_rows: List[Dict[str, str]] = []
	skipped = 0
	for row in rows:
		station_id = str(row.get(cols["station"] or "", "")).strip()
		ts = _parse_iso_utc(str(row.get(cols["timestamp"] or "", "")))
		if not station_id or ts is None:
			skipped += 1
			continue

		_append_if_valid(
			out_rows,
			station_id=station_id,
			timestamp_utc=ts,
			pollutant_name="PM2.5",
			value=_to_float(str(row.get(cols["pm25"] or "", ""))) if cols["pm25"] else None,
			unit="ug/m3",
			provider=provider,
		)
		_append_if_valid(
			out_rows,
			station_id=station_id,
			timestamp_utc=ts,
			pollutant_name="PM10",
			value=_to_float(str(row.get(cols["pm10"] or "", ""))) if cols["pm10"] else None,
			unit="ug/m3",
			provider=provider,
		)
		_append_if_valid(
			out_rows,
			station_id=station_id,
			timestamp_utc=ts,
			pollutant_name="NO2",
			value=_to_float(str(row.get(cols["no2"] or "", ""))) if cols["no2"] else None,
			unit="ug/m3",
			provider=provider,
		)

	out_path.parent.mkdir(parents=True, exist_ok=True)
	with out_path.open("w", encoding="utf-8", newline="") as f:
		writer = csv.DictWriter(
			f,
			fieldnames=[
				"reference_station_id",
				"timestamp_utc",
				"pollutant_name",
				"value_reference",
				"unit",
				"qc_flag_code",
				"data_provider",
			],
			extrasaction="ignore",
		)
		writer.writeheader()
		writer.writerows(out_rows)

	print(f"Input rows: {len(rows)}")
	print(f"Output rows: {len(out_rows)}")
	print(f"Skipped rows (missing station/time): {skipped}")
	print(f"Wrote: {out_path}")


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Convert Luchtmeetnet export to reference_station_observations.csv")
	parser.add_argument("--input", required=True, help="Raw Luchtmeetnet CSV export")
	parser.add_argument("--out", default="data_exports/full/reference_station_observations.csv")
	parser.add_argument("--provider", default="luchtmeetnet-rivm")
	return parser


def main() -> int:
	args = build_parser().parse_args()
	input_path = Path(args.input)
	out_path = Path(args.out)

	if not input_path.exists():
		print(f"Input file not found: {input_path}")
		return 2

	try:
		normalize(input_path=input_path, out_path=out_path, provider=args.provider)
	except Exception as exc:  # pylint: disable=broad-except
		print(f"Error: {exc}")
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())