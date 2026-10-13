#!/usr/bin/env python3
"""Retrieve wind data from KNMI EDR API for challenge datasets.

Important:
- Access uses the EDR API.
- The API key must be sent in the Authorization HTTP header.

Base endpoint for this dataset:
https://api.dataplatform.knmi.nl/edr/v1/collections/10-minute-in-situ-meteorological-observations

Examples:

1) Inspect collection metadata (parameters, etc):
   python data_scripts/knmi.py inspect

2) Fetch wind data for one point:
   python data_scripts/knmi.py fetch-point \
	 --lon 4.900 --lat 52.370 \
	 --start-time 2025-01-01T00:00:00Z --end-time 2025-01-02T00:00:00Z \
	 --parameter-names "wind_speed,wind_direction" \
	 --out-dir data_exports/knmi

3) Fetch wind data for each sensor from sensor_metadata.csv:
   python data_scripts/knmi.py fetch-from-sensor-metadata \
	 --sensor-metadata data_exports/full/sensor_metadata.csv \
	 --start-time 2025-01-01T00:00:00Z --end-time 2025-01-07T00:00:00Z \
	 --parameter-names "wind_speed,wind_direction" \
	 --out-dir data_exports/knmi
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv


DEFAULT_BASE_URL = "https://api.dataplatform.knmi.nl/edr/v1"
DEFAULT_COLLECTION = "10-minute-in-situ-meteorological-observations"


@dataclass
class WindSample:
	sensor_id: str
	timestamp_utc: str
	latitude: float
	longitude: float
	wind_speed_mps: Optional[float]
	wind_direction_deg: Optional[float]
	source: str


class ApiRequestError(RuntimeError):
	"""Structured API request error for clearer troubleshooting."""

	def __init__(
		self,
		status_code: int,
		reason: str,
		url: str,
		response_body: str,
		response_headers: Dict[str, str],
	) -> None:
		super().__init__(f"HTTP {status_code} {reason} for {url}")
		self.status_code = status_code
		self.reason = reason
		self.url = url
		self.response_body = response_body
		self.response_headers = response_headers


def _request_json(url: str, api_key: str, timeout_s: int = 60) -> Dict[str, Any]:
	return _request_json_with_auth_mode(url=url, api_key=api_key, auth_mode="authorization", timeout_s=timeout_s)


def _request_json_with_auth_mode(
	url: str,
	api_key: str,
	auth_mode: str,
	timeout_s: int = 60,
) -> Dict[str, Any]:
	# Per KNMI EDR docs, pass the API key in Authorization header.
	if auth_mode == "authorization-bearer":
		auth_headers = {"Authorization": f"Bearer {api_key}"}
	elif auth_mode == "x-api-key":
		auth_headers = {"X-Api-Key": api_key}
	else:
		auth_headers = {"Authorization": api_key}

	req = Request(
		url,
		headers={
			"Accept": "application/json",
			**auth_headers,
			"User-Agent": "HollandseLuchtenHackathon/knmi-client",
		},
	)
	try:
		with urlopen(req, timeout=timeout_s) as response:
			return json.loads(response.read().decode("utf-8"))
	except HTTPError as exc:
		body = ""
		try:
			body = exc.read().decode("utf-8", errors="replace")
		except Exception:  # pylint: disable=broad-except
			body = ""
		headers = {k: v for k, v in (exc.headers.items() if exc.headers else [])}
		raise ApiRequestError(
			status_code=exc.code,
			reason=str(exc.reason),
			url=url,
			response_body=body,
			response_headers=headers,
		) from exc


def _iso_utc(dt_text: str) -> str:
	dt = datetime.fromisoformat(dt_text.replace("Z", "+00:00"))
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=timezone.utc)
	return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _collection_url(base_url: str, collection: str) -> str:
	return f"{base_url.rstrip('/')}/collections/{collection}"


def _position_query_url(
	base_url: str,
	collection: str,
	lon: float,
	lat: float,
	start_time: str,
	end_time: str,
	parameter_names: str,
	output_format: str,
) -> str:
	endpoint = f"{_collection_url(base_url, collection)}/position"
	params = {
		"coords": f"POINT({lon} {lat})",
		"datetime": f"{_iso_utc(start_time)}/{_iso_utc(end_time)}",
		"parameter-name": parameter_names,
		"f": output_format,
	}
	return f"{endpoint}?{urlencode(params)}"


def _location_query_url(
	base_url: str,
	collection: str,
	station_id: str,
	start_time: str,
	end_time: str,
	parameter_names: str,
	output_format: str,
) -> str:
	"""Query by explicit station ID — more reliable than position snap."""
	endpoint = f"{_collection_url(base_url, collection)}/locations/{station_id}"
	params = {
		"datetime": f"{_iso_utc(start_time)}/{_iso_utc(end_time)}",
		"parameter-name": parameter_names,
		"f": output_format,
	}
	return f"{endpoint}?{urlencode(params)}"


def _extract_axis_values(coverage_json: Dict[str, Any], axis_name: str) -> List[Any]:
	axes = (coverage_json.get("domain") or {}).get("axes") or {}
	axis = axes.get(axis_name) or {}
	values = axis.get("values") or []
	if isinstance(values, list):
		return values
	return []


def _extract_parameter_units(coverage_json: Dict[str, Any]) -> Dict[str, str]:
	units: Dict[str, str] = {}
	parameters = coverage_json.get("parameters") or {}
	for name, info in parameters.items():
		unit_obj = info.get("unit") or {}
		symbol = unit_obj.get("symbol")
		label = unit_obj.get("label")
		if isinstance(symbol, dict):
			symbol = symbol.get("value") or ""
		units[name] = str(symbol or label or "")
	return units


def _extract_ranges(coverage_json: Dict[str, Any]) -> Dict[str, List[Any]]:
	ranges = coverage_json.get("ranges") or {}
	parsed: Dict[str, List[Any]] = {}
	for name, info in ranges.items():
		values = info.get("values")
		if isinstance(values, list):
			parsed[name] = values
	return parsed


@dataclass
class StationInfo:
	station_id: str
	name: str
	lat: float
	lon: float


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
	"""Approximate great-circle distance in kilometres."""
	import math
	R = 6371.0
	d_lat = math.radians(lat2 - lat1)
	d_lon = math.radians(lon2 - lon1)
	a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
	return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fetch_stations(base_url: str, collection: str, api_key: str, auth_mode: str) -> List[StationInfo]:
	url = f"{_collection_url(base_url, collection)}/locations?f=json"
	payload = _request_json_with_auth_mode(url, api_key=api_key, auth_mode=auth_mode)
	stations: List[StationInfo] = []
	for feature in payload.get("features") or []:
		props = feature.get("properties") or {}
		geo = feature.get("geometry") or {}
		coords = geo.get("coordinates") or []
		if len(coords) < 2:
			continue
		try:
			stations.append(
				StationInfo(
					station_id=str(feature.get("id", "")),
					name=str(props.get("name", "")),
					lon=float(coords[0]),
					lat=float(coords[1]),
				)
			)
		except (TypeError, ValueError):
			continue
	return stations


def _nearest_station(stations: List[StationInfo], lat: float, lon: float) -> Optional[StationInfo]:
	if not stations:
		return None
	return min(stations, key=lambda s: _haversine_km(lat, lon, s.lat, s.lon))


def _to_float(value: Any) -> Optional[float]:
	try:
		if value is None:
			return None
		return float(value)
	except (TypeError, ValueError):
		return None


def _guess_wind_param(parameter_names: List[str], role: str) -> Optional[str]:
	preferred_tokens = {
		"speed": ["windspeed", "wind_speed", "ff", "ff10"],
		"direction": ["winddirection", "wind_direction", "dd", "dd10"],
	}
	tokens = preferred_tokens[role]
	lowered = {name: "".join(ch for ch in name.lower() if ch.isalnum() or ch == "_") for name in parameter_names}

	for token in tokens:
		for original, normalized in lowered.items():
			if token in normalized:
				return original
	return None


def _coveragejson_to_long_rows(
	coverage_json: Dict[str, Any],
	sensor_id: str,
	lat: float,
	lon: float,
) -> List[Dict[str, Any]]:
	# Handle both Coverage (single) and CoverageCollection (list of coverages).
	coverage_type = coverage_json.get("type", "")
	if coverage_type == "CoverageCollection":
		coverages = coverage_json.get("coverages") or []
	else:
		coverages = [coverage_json]

	# Top-level parameters block (shared across coverages in a CoverageCollection).
	shared_units = _extract_parameter_units(coverage_json)
	rows: List[Dict[str, Any]] = []

	for coverage in coverages:
		t_values = _extract_axis_values(coverage, "t")
		ranges = _extract_ranges(coverage)
		# Coverage-level parameter block overrides shared one.
		units = _extract_parameter_units(coverage) or shared_units

		if not t_values or not ranges:
			continue

		for parameter_name, values in ranges.items():
			for i, ts in enumerate(t_values):
				value = values[i] if i < len(values) else None
				rows.append(
					{
						"sensor_id": sensor_id,
						"timestamp_utc": str(ts),
						"latitude": lat,
						"longitude": lon,
						"parameter_name": parameter_name,
						"value": value,
						"unit": units.get(parameter_name, ""),
						"source": "knmi-edr",
					}
				)
	return rows


def _long_to_wind_samples(
	long_rows: List[Dict[str, Any]],
	wind_speed_param: Optional[str] = None,
	wind_direction_param: Optional[str] = None,
) -> List[WindSample]:
	grouped: Dict[tuple[str, str], Dict[str, Any]] = {}
	param_names = sorted({str(r.get("parameter_name", "")) for r in long_rows if r.get("parameter_name")})

	speed_param = wind_speed_param or _guess_wind_param(param_names, "speed")
	direction_param = wind_direction_param or _guess_wind_param(param_names, "direction")

	for row in long_rows:
		sid = str(row.get("sensor_id", ""))
		ts = str(row.get("timestamp_utc", ""))
		key = (sid, ts)
		if key not in grouped:
			grouped[key] = {
				"sensor_id": sid,
				"timestamp_utc": ts,
				"latitude": _to_float(row.get("latitude")) or 0.0,
				"longitude": _to_float(row.get("longitude")) or 0.0,
				"wind_speed_mps": None,
				"wind_direction_deg": None,
				"source": "knmi-edr",
			}

		p_name = str(row.get("parameter_name", ""))
		val = _to_float(row.get("value"))
		if p_name == speed_param:
			grouped[key]["wind_speed_mps"] = val
		elif p_name == direction_param:
			grouped[key]["wind_direction_deg"] = val

	samples: List[WindSample] = []
	for item in grouped.values():
		samples.append(
			WindSample(
				sensor_id=item["sensor_id"],
				timestamp_utc=item["timestamp_utc"],
				latitude=item["latitude"],
				longitude=item["longitude"],
				wind_speed_mps=item["wind_speed_mps"],
				wind_direction_deg=item["wind_direction_deg"],
				source=item["source"],
			)
		)
	samples.sort(key=lambda s: (s.sensor_id, s.timestamp_utc))
	return samples


def _write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("w", encoding="utf-8", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
		writer.writeheader()
		for row in rows:
			writer.writerow(row)


def _write_json(path: Path, obj: Any) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(obj, indent=2, ensure_ascii=True), encoding="utf-8")


def run_list_stations(args: argparse.Namespace, api_key: str) -> None:
	stations = fetch_stations(args.base_url, args.collection, api_key=api_key, auth_mode=args.auth_mode)
	out = Path(args.out_dir)
	_write_csv(
		out / "knmi_stations.csv",
		[s.__dict__ for s in stations],
		["station_id", "name", "lat", "lon"],
	)
	print(f"Stations found: {len(stations)}")
	for s in stations:
		print(f"  {s.station_id}  {s.name:<25}  lat={s.lat}  lon={s.lon}")
	print(f"Wrote: {out / 'knmi_stations.csv'}")


def inspect_collection(args: argparse.Namespace, api_key: str) -> None:
	url = _collection_url(args.base_url, args.collection)
	payload = _request_json_with_auth_mode(url, api_key=api_key, auth_mode=args.auth_mode)
	out = Path(args.out_dir)
	_write_json(out / "collection_metadata.json", payload)

	parameters = payload.get("parameterNames") or payload.get("parameters") or {}
	if isinstance(parameters, dict):
		names = sorted(parameters.keys())
	elif isinstance(parameters, list):
		names = [str(x) for x in parameters]
	else:
		names = []

	print(f"Saved metadata: {out / 'collection_metadata.json'}")
	if names:
		print("Detected parameter names:")
		for name in names:
			print(f"- {name}")
	else:
		print("No parameter names detected in metadata response.")


def fetch_point(args: argparse.Namespace, api_key: str, sensor_id: str = "point_1") -> Dict[str, Any]:
	# Prefer location-ID query (reliable) over position snap; fall back when no station_id available.
	knmi_station_id: Optional[str] = getattr(args, "knmi_station_id", None)
	if knmi_station_id:
		query_url = _location_query_url(
			base_url=args.base_url,
			collection=args.collection,
			station_id=knmi_station_id,
			start_time=args.start_time,
			end_time=args.end_time,
			parameter_names=args.parameter_names,
			output_format=args.output_format,
		)
	else:
		query_url = _position_query_url(
			base_url=args.base_url,
			collection=args.collection,
			lon=args.lon,
			lat=args.lat,
			start_time=args.start_time,
			end_time=args.end_time,
			parameter_names=args.parameter_names,
			output_format=args.output_format,
		)
	payload = _request_json_with_auth_mode(query_url, api_key=api_key, auth_mode=args.auth_mode)

	long_rows = _coveragejson_to_long_rows(payload, sensor_id=sensor_id, lat=args.lat, lon=args.lon)
	wind_samples = _long_to_wind_samples(
		long_rows,
		wind_speed_param=args.wind_speed_param,
		wind_direction_param=args.wind_direction_param,
	)

	return {
		"query_url": query_url,
		"raw_payload": payload,
		"long_rows": long_rows,
		"wind_samples": wind_samples,
	}


def run_fetch_point(args: argparse.Namespace, api_key: str) -> None:
	result = fetch_point(args, api_key=api_key, sensor_id=args.sensor_id)
	out = Path(args.out_dir)

	_write_json(out / "point_query_response.json", result["raw_payload"])
	_write_csv(
		out / "knmi_wind_long.csv",
		result["long_rows"],
		["sensor_id", "timestamp_utc", "latitude", "longitude", "parameter_name", "value", "unit", "source"],
	)
	_write_csv(
		out / "knmi_wind_samples.csv",
		[s.__dict__ for s in result["wind_samples"]],
		["sensor_id", "timestamp_utc", "latitude", "longitude", "wind_speed_mps", "wind_direction_deg", "source"],
	)

	print(f"Query URL: {result['query_url']}")
	print(f"Wrote: {out / 'point_query_response.json'}")
	print(f"Wrote: {out / 'knmi_wind_long.csv'} rows={len(result['long_rows'])}")
	print(f"Wrote: {out / 'knmi_wind_samples.csv'} rows={len(result['wind_samples'])}")


def run_fetch_from_sensor_metadata(args: argparse.Namespace, api_key: str) -> None:
	metadata_path = Path(args.sensor_metadata)
	if not metadata_path.exists():
		raise FileNotFoundError(f"sensor metadata file not found: {metadata_path}")

	with metadata_path.open("r", encoding="utf-8", newline="") as f:
		rows = list(csv.DictReader(f))

	# Load all KNMI stations once — used for coordinate lookup and fallback nearest-snap.
	print("Fetching KNMI station list...")
	stations = fetch_stations(args.base_url, args.collection, api_key=api_key, auth_mode=args.auth_mode)
	station_by_id: Dict[str, StationInfo] = {s.station_id: s for s in stations}
	print(f"  Found {len(stations)} KNMI stations.")

	all_long_rows: List[Dict[str, Any]] = []
	all_samples: List[WindSample] = []

	# Avoid fetching the same KNMI station twice for nearby sensors.
	fetched_stations: Dict[str, List[WindSample]] = {}

	processed = 0
	for row in rows:
		sensor_id = (row.get("sensor_id") or "").strip()
		if not sensor_id:
			continue

		sensor_lat = _to_float(row.get("latitude"))
		sensor_lon = _to_float(row.get("longitude"))

		# --- Resolve KNMI station: prefer knmicode column over nearest-station snap ---
		knmicode = (row.get("knmicode") or "").strip()
		target_station: Optional[StationInfo] = None

		if knmicode:
			# Convert "knmi_06260" -> "0-20000-0-06260"
			numeric = knmicode.replace("knmi_", "").strip()
			candidate_id = f"0-20000-0-0{numeric}"
			target_station = station_by_id.get(candidate_id)
			if target_station is None:
				# Some stations use "0-528-0-" prefix (wind-farm stations)
				candidate_id2 = f"0-528-0-0{numeric}"
				target_station = station_by_id.get(candidate_id2)
			if target_station is None:
				print(f"  Warning: sensor {sensor_id} knmicode={knmicode} not found in station list; falling back to nearest.")

		if target_station is None:
			if sensor_lat is None or sensor_lon is None:
				print(f"  Skipping sensor {sensor_id}: no knmicode and no coordinates available.")
				continue
			target_station = _nearest_station(stations, sensor_lat, sensor_lon)
			if target_station is None:
				print(f"  Warning: no KNMI station found for sensor {sensor_id}; skipping.")
				continue
			dist_km = _haversine_km(sensor_lat, sensor_lon, target_station.lat, target_station.lon)
			if args.max_station_distance_km is not None and dist_km > args.max_station_distance_km:
				print(f"  Skipping sensor {sensor_id}: nearest station {target_station.name} is {dist_km:.1f} km away (limit {args.max_station_distance_km} km).")
				continue
		else:
			dist_km = None
			if sensor_lat is not None and sensor_lon is not None:
				dist_km = _haversine_km(sensor_lat, sensor_lon, target_station.lat, target_station.lon)

		nearest = target_station

		if nearest.station_id in fetched_stations:
			# Reuse already-fetched data; relabel to this sensor_id.
			cached = fetched_stations[nearest.station_id]
			for sample in cached:
				relabeled = WindSample(
					sensor_id=sensor_id,
					timestamp_utc=sample.timestamp_utc,
					latitude=nearest.lat,
					longitude=nearest.lon,
					wind_speed_mps=sample.wind_speed_mps,
					wind_direction_deg=sample.wind_direction_deg,
					source=sample.source,
				)
				all_samples.append(relabeled)
			processed += 1
			continue

		# Fetch from the resolved KNMI station.
		sensor_args = argparse.Namespace(**vars(args))
		sensor_args.lat = nearest.lat
		sensor_args.lon = nearest.lon
		sensor_args.sensor_id = nearest.station_id
		sensor_args.knmi_station_id = nearest.station_id

		try:
			result = fetch_point(sensor_args, api_key=api_key, sensor_id=nearest.station_id)
			fetched_stations[nearest.station_id] = result["wind_samples"]
			all_long_rows.extend(result["long_rows"])
			for sample in result["wind_samples"]:
				relabeled = WindSample(
					sensor_id=sensor_id,
					timestamp_utc=sample.timestamp_utc,
					latitude=nearest.lat,
					longitude=nearest.lon,
					wind_speed_mps=sample.wind_speed_mps,
					wind_direction_deg=sample.wind_direction_deg,
					source=f"knmi-edr:{nearest.station_id}",
				)
				all_samples.append(relabeled)
			processed += 1
			if dist_km is None:
				print(f"  sensor {sensor_id} -> station {nearest.name}  obs={len(result['wind_samples'])}")
			else:
				print(f"  sensor {sensor_id} -> station {nearest.name} ({dist_km:.1f} km)  obs={len(result['wind_samples'])}")
		except ApiRequestError as exc:
			print(f"  Warning: sensor {sensor_id} / station {nearest.name} failed: HTTP {exc.status_code} {exc.response_body[:200]}")
		except Exception as exc:  # pylint: disable=broad-except
			print(f"  Warning: sensor {sensor_id} failed: {exc}")

		if args.sleep_s > 0:
			time.sleep(args.sleep_s)
		if args.max_sensors is not None and processed >= args.max_sensors:
			break

	out = Path(args.out_dir)
	_write_csv(
		out / "knmi_wind_long.csv",
		all_long_rows,
		["sensor_id", "timestamp_utc", "latitude", "longitude", "parameter_name", "value", "unit", "source"],
	)
	_write_csv(
		out / "knmi_wind_samples.csv",
		[s.__dict__ for s in all_samples],
		["sensor_id", "timestamp_utc", "latitude", "longitude", "wind_speed_mps", "wind_direction_deg", "source"],
	)

	print(f"Processed sensors: {processed}")
	print(f"Wrote: {out / 'knmi_wind_long.csv'} rows={len(all_long_rows)}")
	print(f"Wrote: {out / 'knmi_wind_samples.csv'} rows={len(all_samples)}")


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="KNMI EDR wind-data fetcher")
	parser.add_argument("command", choices=["inspect", "list-stations", "fetch-point", "fetch-from-sensor-metadata"])
	parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
	parser.add_argument("--collection", default=DEFAULT_COLLECTION)
	parser.add_argument("--out-dir", default="data_exports/knmi")
	parser.add_argument("--output-format", default="CoverageJSON", help="EDR output format, e.g. CoverageJSON")
	parser.add_argument(
		"--auth-mode",
		choices=["authorization", "authorization-bearer", "x-api-key"],
		default="authorization",
		help="How to send KNMI_API in request headers",
	)

	parser.add_argument("--start-time", default="2025-01-01T00:00:00Z")
	parser.add_argument("--end-time", default="2025-01-02T00:00:00Z")
	parser.add_argument("--parameter-names", default="dd,ff", help="EDR parameter-name query value (KNMI: dd=wind_direction, ff=wind_speed)")

	parser.add_argument("--lat", type=float, default=52.370216)
	parser.add_argument("--lon", type=float, default=4.895168)
	parser.add_argument("--sensor-id", default="point_1", help="Synthetic sensor id for fetch-point mode")
	parser.add_argument("--knmi-station-id", default=None, help="Explicit KNMI station ID for fetch-point mode (preferred over position snap)")

	parser.add_argument("--wind-speed-param", default=None, help="Exact parameter name for wind speed in response")
	parser.add_argument("--wind-direction-param", default=None, help="Exact parameter name for wind direction in response")

	parser.add_argument("--sensor-metadata", default="data_exports/full/sensor_metadata.csv")
	parser.add_argument("--max-sensors", type=int, default=None)
	parser.add_argument("--sleep-s", type=float, default=0.0)
	parser.add_argument("--max-station-distance-km", type=float, default=None, help="Ignore sensors where nearest KNMI station is farther than this")
	return parser


def main() -> int:
	args = build_parser().parse_args()

	load_dotenv()
	api_key = os.getenv("KNMI_EDR")
	if not api_key:
		print("Missing KNMI_EDR in environment (.env).")
		return 2

	try:
		if args.command == "inspect":
			inspect_collection(args, api_key=api_key)
		elif args.command == "list-stations":
			run_list_stations(args, api_key=api_key)
		elif args.command == "fetch-point":
			run_fetch_point(args, api_key=api_key)
		else:
			run_fetch_from_sensor_metadata(args, api_key=api_key)
	except KeyboardInterrupt:
		print("Interrupted by user")
		return 130
	except ApiRequestError as exc:
		print(f"Error: HTTP {exc.status_code} {exc.reason}")
		print(f"URL: {exc.url}")
		trace_headers = ["x-request-id", "x-correlation-id", "x-amzn-requestid", "www-authenticate"]
		for key in trace_headers:
			val = exc.response_headers.get(key) or exc.response_headers.get(key.title())
			if val:
				print(f"{key}: {val}")
		if exc.response_body:
			trimmed = exc.response_body.strip()
			if len(trimmed) > 1200:
				trimmed = trimmed[:1200] + "..."
			print("Response body:")
			print(trimmed)
		return 1
	except Exception as exc:  # pylint: disable=broad-except
		print(f"Error: {exc}")
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
