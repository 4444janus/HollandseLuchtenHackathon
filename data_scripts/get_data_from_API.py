#!/usr/bin/env python3
"""Retrieve sample and full datasets from the RIVM Samen Meten SensorThings API.

API root:
https://api-samenmeten.rivm.nl/v1.0

Docs:
https://gost1.docs.apiary.io/#reference/0/root/get-sensorthings-resource-endpoints
https://www.samenmeten.nl/dataportaal/api-application-programming-interface

Usage examples:

1) Fetch quick samples from all discovered endpoints:
   python get_data_from_API.py sample --sample-size 20

2) Build full challenge-ready datasets (CSV) for a time window:
   python get_data_from_API.py full --start-time 2025-01-01T00:00:00Z --end-time 2025-03-01T00:00:00Z

3) Build full datasets, but cap observation load during first run:
   python get_data_from_API.py full --max-datastreams 20 --max-observations-per-datastream 5000
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, quote
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api-samenmeten.rivm.nl/v1.0"

# HTTP status codes that are transient and worth retrying.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _json_request(url: str, timeout_s: int = 60, max_retries: int = 5) -> Dict[str, Any]:
   """Fetch JSON with exponential backoff retry on transient HTTP errors."""
   delay = 2.0
   for attempt in range(max_retries + 1):
      try:
         req = Request(url, headers={"Accept": "application/json"})
         with urlopen(req, timeout=timeout_s) as response:
            data = response.read().decode("utf-8")
         return json.loads(data)
      except HTTPError as exc:
         if exc.code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
            wait = delay * (2 ** attempt)
            print(f"[retry] HTTP {exc.code} on attempt {attempt + 1}/{max_retries}; retrying in {wait:.0f}s")
            time.sleep(wait)
            continue
         raise
      except OSError:
         # Covers socket/connection timeouts
         if attempt < max_retries:
            wait = delay * (2 ** attempt)
            print(f"[retry] connection error on attempt {attempt + 1}/{max_retries}; retrying in {wait:.0f}s")
            time.sleep(wait)
            continue
         raise
   raise RuntimeError(f"Exhausted {max_retries} retries for {url}")  # unreachable


def _build_url(base_or_endpoint_url: str, params: Optional[Dict[str, Any]] = None) -> str:
   if not params:
      return base_or_endpoint_url
   filtered = {k: v for k, v in params.items() if v is not None and v != ""}
   if not filtered:
      return base_or_endpoint_url
   # Use quote_via=quote with safe chars so OData system query options are not
   # percent-encoded. $, (, ), /, = and ' must all be preserved so that expressions
   # like $filter=startswith(Thing/name,'HLL') and $expand=Thing($expand=Locations)
   # reach the server literally.
   _ODATA_SAFE = "$,()/'="
   return f"{base_or_endpoint_url}?{urlencode(filtered, quote_via=quote, safe=_ODATA_SAFE)}"


def discover_endpoints(base_url: str) -> List[Dict[str, str]]:
   payload = _json_request(base_url)
   return payload.get("value", [])


def iter_collection(
   endpoint_url: str,
   params: Optional[Dict[str, Any]] = None,
   max_pages: Optional[int] = None,
   sleep_s: float = 0.0,
) -> Iterator[Dict[str, Any]]:
   """Iterate through SensorThings collection using @iot.nextLink pagination."""
   page = 0
   next_url = _build_url(endpoint_url, params)

   while next_url:
      payload = _json_request(next_url)
      items = payload.get("value", [])
      for item in items:
         yield item

      page += 1
      if max_pages is not None and page >= max_pages:
         break

      raw_next = payload.get("@iot.nextLink") or payload.get("nextLink")
      if not raw_next:
         break
      next_url = urljoin(endpoint_url, raw_next)

      if sleep_s > 0:
         time.sleep(sleep_s)


def write_json(path: Path, obj: Any) -> None:
   path.parent.mkdir(parents=True, exist_ok=True)
   path.write_text(json.dumps(obj, indent=2, ensure_ascii=True), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
   path.parent.mkdir(parents=True, exist_ok=True)
   with path.open("w", encoding="utf-8") as f:
      for row in rows:
         f.write(json.dumps(row, ensure_ascii=True) + "\n")


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
   path.parent.mkdir(parents=True, exist_ok=True)
   with path.open("w", encoding="utf-8", newline="") as f:
      writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
      writer.writeheader()
      for row in rows:
         writer.writerow(row)


def parse_observation_time(phenomenon_time: Optional[str]) -> Optional[str]:
   """Use interval start if phenomenonTime is an ISO 8601 interval."""
   if not phenomenon_time:
      return None
   if "/" in phenomenon_time:
      return phenomenon_time.split("/", 1)[0]
   return phenomenon_time


def _safe_get_coordinates(location: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
   """Extract lon/lat from GeoJSON-style location object if present."""
   geo = location.get("location") or {}
   coordinates = geo.get("coordinates", [])
   if not isinstance(coordinates, list) or len(coordinates) < 2:
      return None, None
   lon, lat = coordinates[0], coordinates[1]
   try:
      return float(lon), float(lat)
   except (TypeError, ValueError):
      return None, None


def _extract_thing_property(thing: Dict[str, Any], keys: List[str]) -> str:
   """Extract first non-empty Thing property value for a list of candidate keys."""
   props = thing.get("properties") or {}
   if not isinstance(props, dict):
      return ""

   for key in keys:
      value = props.get(key)
      if value is None:
         continue
      text = str(value).strip()
      if text:
         return text
   return ""


@dataclass
class DatastreamIndexRow:
   datastream_id: str
   datastream_name: str
   thing_id: str
   thing_name: str
   sensor_id: str
   sensor_name: str
   observed_property_id: str
   observed_property_name: str
   unit_name: str
   unit_symbol: str
   location_id: str
   latitude: Optional[float]
   longitude: Optional[float]
   knmicode: str
   intended_reference_station_id: str
   municipality: str


def _build_datastream_api_filter(
   thing_name_prefixes: List[str],
) -> Optional[str]:
   """Build a SensorThings OData $filter expression for server-side filtering.

   Only filters on Thing/name prefix — the most reliable server-side filter
   supported by the RIVM Samen Meten SensorThings API.
   Observed-property filtering is done client-side after index fetch.
   """
   if not thing_name_prefixes:
      return None

   if len(thing_name_prefixes) == 1:
      return f"startswith(Thing/name,'{thing_name_prefixes[0]}')"

   sub = " or ".join(f"startswith(Thing/name,'{p}')" for p in thing_name_prefixes)
   return f"({sub})"


def build_datastream_index(
   base_url: str,
   page_size: int,
   max_pages: Optional[int],
   max_datastreams: Optional[int],
   sleep_s: float,
   thing_name_prefixes: Optional[List[str]] = None,
   observed_property_tokens: Optional[List[str]] = None,
) -> List[DatastreamIndexRow]:
   """Fetch datastream index from the API.

   When thing_name_prefixes is set, queries the Things endpoint with a
   startswith filter (which the API supports) and expands Datastreams.
   This is much faster than loading all 55k+ datastreams unfiltered.
   """
   rows: List[DatastreamIndexRow] = []
   count = 0

   if thing_name_prefixes:
      # --- Fast path: query Things by name prefix, expand Datastreams inline ---
      endpoint = f"{base_url}/Things"
      if len(thing_name_prefixes) == 1:
         thing_filter = f"startswith(name,'{thing_name_prefixes[0]}')"
      else:
         sub = " or ".join(f"startswith(name,'{p}')" for p in thing_name_prefixes)
         thing_filter = f"({sub})"

      params = {
         "$top": page_size,
         "$filter": thing_filter,
         "$expand": "Locations($top=1;$select=location),Datastreams($expand=Sensor,ObservedProperty)",
      }

      for thing in iter_collection(endpoint, params=params, max_pages=max_pages, sleep_s=sleep_s):
         locations = thing.get("Locations") or []
         first_location = locations[0] if locations else {}
         lon, lat = _safe_get_coordinates(first_location)
         thing_id = str(thing.get("@iot.id", ""))
         thing_name = str(thing.get("name", ""))
         knmicode = _extract_thing_property(thing, ["knmicode"])
         intended_ref = _extract_thing_property(thing, ["pm25closecode", "luchtmeetnetstation"])
         municipality = _extract_thing_property(thing, ["gemeente", "codegemeente"])

         for ds in (thing.get("Datastreams") or []):
            if max_datastreams is not None and count >= max_datastreams:
               return rows

            sensor = ds.get("Sensor") or {}
            observed = ds.get("ObservedProperty") or {}
            unit = ds.get("unitOfMeasurement") or {}
            datastream_id = str(ds.get("@iot.id", ""))

            rows.append(
               DatastreamIndexRow(
                  datastream_id=datastream_id,
                  datastream_name=str(ds.get("name", "")),
                  thing_id=thing_id,
                  thing_name=thing_name,
                  sensor_id=thing_id,
                  sensor_name=str(sensor.get("name", "")),
                  observed_property_id=str(observed.get("@iot.id", "")),
                  observed_property_name=str(observed.get("name", "")),
                  unit_name=str(unit.get("name", "")),
                  unit_symbol=str(unit.get("symbol", "")),
                  location_id=str(first_location.get("@iot.id", "")) if first_location else "",
                  latitude=lat,
                  longitude=lon,
                  knmicode=knmicode,
                  intended_reference_station_id=intended_ref,
                  municipality=municipality,
               )
            )
            count += 1

   else:
      # --- Unfiltered path: iterate all Datastreams (slow for full network) ---
      endpoint = f"{base_url}/Datastreams"
      params = {
         "$top": page_size,
         "$expand": "Thing($expand=Locations),Sensor,ObservedProperty",
      }

      for i, ds in enumerate(iter_collection(endpoint, params=params, max_pages=max_pages, sleep_s=sleep_s)):
         if max_datastreams is not None and i >= max_datastreams:
            break

         thing = ds.get("Thing") or {}
         sensor = ds.get("Sensor") or {}
         observed = ds.get("ObservedProperty") or {}
         locations = thing.get("Locations") or []
         first_location = locations[0] if locations else {}
         lon, lat = _safe_get_coordinates(first_location)
         unit = ds.get("unitOfMeasurement") or {}
         datastream_id = str(ds.get("@iot.id", ""))
         thing_id = str(thing.get("@iot.id", ""))

         rows.append(
            DatastreamIndexRow(
               datastream_id=datastream_id,
               datastream_name=str(ds.get("name", "")),
               thing_id=thing_id,
               thing_name=str(thing.get("name", "")),
               sensor_id=thing_id or datastream_id,
               sensor_name=str(sensor.get("name", "")),
               observed_property_id=str(observed.get("@iot.id", "")),
               observed_property_name=str(observed.get("name", "")),
               unit_name=str(unit.get("name", "")),
               unit_symbol=str(unit.get("symbol", "")),
               location_id=str(first_location.get("@iot.id", "")) if first_location else "",
               latitude=lat,
               longitude=lon,
               knmicode=_extract_thing_property(thing, ["knmicode"]),
               intended_reference_station_id=_extract_thing_property(thing, ["pm25closecode"]),
               municipality=_extract_thing_property(thing, ["gemeente"]),
            )
         )

   return rows


def _build_observation_filter(start_time: Optional[str], end_time: Optional[str]) -> Optional[str]:
   clauses: List[str] = []
   if start_time:
      clauses.append(f"phenomenonTime ge {start_time}")
   if end_time:
      clauses.append(f"phenomenonTime le {end_time}")
   if not clauses:
      return None
   return " and ".join(clauses)


def _to_float(value: Any) -> Optional[float]:
   try:
      return float(value)
   except (TypeError, ValueError):
      return None


def _normalize_property_name(name: str) -> str:
   lowered = name.lower().strip()
   normalized = "".join(ch for ch in lowered if ch.isalnum())
   return normalized


def _property_role(observed_property_name: str) -> str:
   normalized = _normalize_property_name(observed_property_name)
   direction_tokens = ["winddirection", "windrichting", "wd"]
   speed_tokens = ["windspeed", "windsnelheid", "ws"]

   if any(token in normalized for token in direction_tokens):
      return "wind_direction"
   if any(token in normalized for token in speed_tokens):
      return "wind_speed"
   return "pollutant"


def _parse_iso_utc(ts: str) -> Optional[datetime]:
   if not ts:
      return None
   try:
      if ts.endswith("Z"):
         return datetime.fromisoformat(ts.replace("Z", "+00:00"))
      parsed = datetime.fromisoformat(ts)
      if parsed.tzinfo is None:
         return parsed.replace(tzinfo=timezone.utc)
      return parsed
   except ValueError:
      return None


def _period_bounds(ts: datetime, granularity: str) -> tuple[datetime, datetime]:
   if granularity == "hourly":
      start = ts.replace(minute=0, second=0, microsecond=0)
      end = start + timedelta(hours=1)
   elif granularity == "weekly":
      # ISO week starts Monday
      start = (ts - timedelta(days=ts.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
      end = start + timedelta(days=7)
   else:
      start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
      end = start + timedelta(days=1)
   return start, end


def _period_iso(ts: datetime) -> str:
   return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hour_bucket(ts: datetime) -> datetime:
   return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _speed_bin(value: float, edges: List[float]) -> tuple[float, float]:
   for i in range(len(edges) - 1):
      lo, hi = edges[i], edges[i + 1]
      if lo <= value < hi:
         return lo, hi
   return edges[-2], edges[-1]


def build_wind_rose_aggregates(
   obs_rows: List[Dict[str, Any]],
   direction_bin_size_deg: float,
   speed_bin_edges: List[float],
   granularity: str,
) -> List[Dict[str, Any]]:
   # Gather wind context by sensor and hour bucket to tolerate timestamp offsets.
   # Supports two layouts:
   #   1. Merged layout: wind_speed_mps / wind_direction_deg columns inline in the same row as pollutant.
   #   2. Separate-stream layout: distinct rows whose pollutant_name resolves to wind via _property_role().
   wind_dir_bucket: Dict[tuple[str, str], List[float]] = defaultdict(list)
   wind_speed_bucket: Dict[tuple[str, str], List[float]] = defaultdict(list)
   pollutant_rows: List[Dict[str, Any]] = []

   for row in obs_rows:
      sensor_id = str(row.get("sensor_id", ""))
      timestamp_utc = str(row.get("timestamp_utc", ""))
      dt = _parse_iso_utc(timestamp_utc)
      if dt is None:
         continue
      bucket = _period_iso(_hour_bucket(dt))

      # --- Merged layout: inline wind columns populated by merge_wind.py ---
      inline_speed = _to_float(row.get("wind_speed_mps"))
      inline_dir = _to_float(row.get("wind_direction_deg"))
      if inline_speed is not None:
         wind_speed_bucket[(sensor_id, bucket)].append(max(inline_speed, 0.0))
      if inline_dir is not None:
         wind_dir_bucket[(sensor_id, bucket)].append(inline_dir % 360.0)

      # --- Separate-stream layout: role detected via pollutant_name ---
      observed_property_name = str(row.get("pollutant_name", ""))
      role = _property_role(observed_property_name)
      value_raw = _to_float(row.get("value_raw"))

      if role == "wind_direction" and value_raw is not None and inline_dir is None:
         wind_dir_bucket[(sensor_id, bucket)].append(value_raw % 360.0)
      elif role == "wind_speed" and value_raw is not None and inline_speed is None:
         wind_speed_bucket[(sensor_id, bucket)].append(max(value_raw, 0.0))
      elif role == "pollutant":
         pollutant_rows.append(row)

   # Group pollutant rows by wind-direction and wind-speed bins.
   grouped: Dict[tuple[str, str, str, float, float, float, float], Dict[str, Any]] = {}
   for row in pollutant_rows:
      sensor_id = str(row.get("sensor_id", ""))
      timestamp_utc = str(row.get("timestamp_utc", ""))
      pollutant_name = str(row.get("pollutant_name", ""))
      value = _to_float(row.get("value_raw"))
      if value is None:
         continue

      dt = _parse_iso_utc(timestamp_utc)
      if dt is None:
         continue
      bucket = _period_iso(_hour_bucket(dt))

      directions = wind_dir_bucket.get((sensor_id, bucket), [])
      speeds = wind_speed_bucket.get((sensor_id, bucket), [])
      if not directions or not speeds:
         continue

      # Circular mean is more correct for angles, but scalar mean is acceptable for coarse bins.
      wind_dir = sum(directions) / len(directions)
      wind_speed = sum(speeds) / len(speeds)

      period_start, period_end = _period_bounds(dt, granularity)

      direction_bin_start = math.floor(wind_dir / direction_bin_size_deg) * direction_bin_size_deg
      direction_bin_end = direction_bin_start + direction_bin_size_deg
      speed_bin_start, speed_bin_end = _speed_bin(wind_speed, speed_bin_edges)

      key = (
         sensor_id,
         pollutant_name,
         _period_iso(period_start),
         direction_bin_start,
         direction_bin_end,
         speed_bin_start,
         speed_bin_end,
      )

      if key not in grouped:
         grouped[key] = {
            "sum": 0.0,
            "count": 0,
            "period_end": _period_iso(period_end),
         }
      grouped[key]["sum"] += value
      grouped[key]["count"] += 1

   results: List[Dict[str, Any]] = []
   for key, agg in grouped.items():
      (
         sensor_id,
         pollutant_name,
         period_start_utc,
         direction_bin_deg_start,
         direction_bin_deg_end,
         speed_bin_mps_start,
         speed_bin_mps_end,
      ) = key
      sample_count = int(agg["count"])
      concentration_mean = agg["sum"] / sample_count if sample_count > 0 else None

      results.append(
         {
            "sensor_id": sensor_id,
            "pollutant_name": pollutant_name,
            "period_start_utc": period_start_utc,
            "period_end_utc": agg["period_end"],
            "direction_bin_deg_start": round(direction_bin_deg_start, 4),
            "direction_bin_deg_end": round(direction_bin_deg_end, 4),
            "speed_bin_mps_start": speed_bin_mps_start,
            "speed_bin_mps_end": speed_bin_mps_end,
            "concentration_mean": concentration_mean,
            "sample_count": sample_count,
            "granularity_label": granularity,
         }
      )

   return sorted(
      results,
      key=lambda r: (
         r["sensor_id"],
         r["pollutant_name"],
         r["period_start_utc"],
         r["direction_bin_deg_start"],
         r["speed_bin_mps_start"],
      ),
   )


def _parse_speed_bin_edges(spec: str) -> List[float]:
   raw = [part.strip() for part in spec.split(",") if part.strip()]
   values: List[float] = []
   for part in raw:
      if part.lower() in {"inf", "infinity"}:
         values.append(float("inf"))
      else:
         values.append(float(part))
   if len(values) < 2:
      raise ValueError("--wind-speed-bin-edges must contain at least two values")
   if values != sorted(values):
      raise ValueError("--wind-speed-bin-edges must be sorted ascending")
   return values


def _to_utc_iso_or_raise(text: str) -> str:
   parsed = _parse_iso_utc(text)
   if parsed is None:
      raise ValueError(f"Invalid timestamp: {text}")
   return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _add_months_utc(dt: datetime, months: int) -> datetime:
   # Keep day if possible, otherwise clamp to month-end.
   year = dt.year + ((dt.month - 1 + months) // 12)
   month = ((dt.month - 1 + months) % 12) + 1

   # Find last day of target month by stepping to next month then minus one day.
   if month == 12:
      next_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
   else:
      next_month = datetime(year, month + 1, 1, tzinfo=timezone.utc)
   month_end_day = (next_month - timedelta(days=1)).day

   day = min(dt.day, month_end_day)
   return dt.replace(year=year, month=month, day=day)


def _split_time_windows(start_iso: str, end_iso: str, months: int) -> List[tuple[str, str]]:
   if months <= 0:
      return [(start_iso, end_iso)]

   start_dt = _parse_iso_utc(start_iso)
   end_dt = _parse_iso_utc(end_iso)
   if start_dt is None or end_dt is None:
      raise ValueError("--start-time and --end-time must be valid ISO timestamps for chunk mode")

   start_dt = start_dt.astimezone(timezone.utc)
   end_dt = end_dt.astimezone(timezone.utc)
   if not start_dt < end_dt:
      raise ValueError("--start-time must be earlier than --end-time")

   windows: List[tuple[str, str]] = []
   cur = start_dt
   while cur < end_dt:
      nxt = _add_months_utc(cur, months)
      if nxt <= cur:
         raise ValueError("Chunk split did not advance; check --chunk-months")
      if nxt > end_dt:
         nxt = end_dt
      windows.append(
         (
            cur.isoformat().replace("+00:00", "Z"),
            nxt.isoformat().replace("+00:00", "Z"),
         )
      )
      cur = nxt

   return windows


def _parse_csv_tokens(spec: Optional[str]) -> List[str]:
   if not spec:
      return []
   return [part.strip().lower() for part in spec.split(",") if part.strip()]


def _load_sensor_id_filter(path_text: Optional[str]) -> Optional[set[str]]:
   if not path_text:
      return None

   path = Path(path_text)
   if not path.exists():
      raise FileNotFoundError(f"Sensor id filter file not found: {path}")

   sensor_ids: set[str] = set()
   text = path.read_text(encoding="utf-8")
   for line in text.splitlines():
      cleaned = line.strip()
      if not cleaned or cleaned.startswith("#"):
         continue
      # Allow both one-id-per-line and comma-separated lists.
      for part in cleaned.split(","):
         sid = part.strip()
         if sid:
            sensor_ids.add(sid)

   return sensor_ids


def _filter_datastreams(ds_index: List[DatastreamIndexRow], args: argparse.Namespace) -> List[DatastreamIndexRow]:
   include_tokens = _parse_csv_tokens(args.observed_property_contains)
   exclude_tokens = _parse_csv_tokens(args.exclude_observed_property_contains)
   sensor_substrings = _parse_csv_tokens(args.sensor_id_contains)
   sensor_id_filter = _load_sensor_id_filter(args.sensor_ids_file)

   if not include_tokens and not exclude_tokens and not sensor_substrings and sensor_id_filter is None:
      return ds_index

   filtered: List[DatastreamIndexRow] = []
   for ds in ds_index:
      # Match property tokens against both observed_property_name AND the datastream
      # name (which always contains the pollutant suffix, e.g. 'HLL_hl_device_484-12-pm25_kal').
      # This handles cases where ObservedProperty.name is empty in nested API expands.
      prop_norm = _normalize_property_name(
         ds.observed_property_name or ds.datastream_name
      )
      sensor_id = ds.sensor_id
      sensor_search_text = f"{sensor_id} {ds.thing_name} {ds.datastream_name}".lower()

      if include_tokens and not any(token in prop_norm for token in include_tokens):
         continue
      if exclude_tokens and any(token in prop_norm for token in exclude_tokens):
         continue
      if sensor_substrings and not any(token in sensor_search_text for token in sensor_substrings):
         continue
      if sensor_id_filter is not None and sensor_id not in sensor_id_filter:
         continue

      filtered.append(ds)

   return filtered


def _pollutant_name_from_datastream(ds: DatastreamIndexRow) -> str:
   """Return observed_property_name, or derive it from the datastream name suffix.

   The Samen Meten API's nested expand does not populate ObservedProperty.name,
   but the datastream name always ends with the pollutant, e.g.:
     HLL_hl_device_239-12-pm25_kal  ->  pm25_kal
   """
   if ds.observed_property_name:
      return ds.observed_property_name
   # Extract suffix after the last '-'
   parts = ds.datastream_name.rsplit("-", maxsplit=1)
   return parts[-1] if len(parts) > 1 else ds.datastream_name


def _observation_row(ds: DatastreamIndexRow, obs: Dict[str, Any]) -> Dict[str, Any]:
   return {
      "sensor_id": ds.sensor_id,
      "datastream_id": ds.datastream_id,
      "observation_id": str(obs.get("@iot.id", "")),
      "timestamp_utc": parse_observation_time(obs.get("phenomenonTime")),
      "pollutant_name": _pollutant_name_from_datastream(ds),
      "value_raw": obs.get("result"),
      "value_calibrated": "",
      "unit": ds.unit_symbol or ds.unit_name,
      "temperature_c": "",
      "relative_humidity_pct": "",
      "wind_speed_mps": "",
      "wind_direction_deg": "",
      "qc_flag_code": "",
      "qc_flag_reason": "",
      "ingestion_source": "api-samenmeten-rivm",
      "record_version": 1,
   }


def _fetch_observations_for_datastream(
   base_url: str,
   ds: DatastreamIndexRow,
   page_size: int,
   max_pages: Optional[int],
   sleep_s: float,
   observation_filter: Optional[str],
   max_observations_per_datastream: Optional[int],
) -> List[Dict[str, Any]]:
   endpoint = f"{base_url}/Datastreams({ds.datastream_id})/Observations"
   params = {
      "$top": page_size,
      "$orderby": "phenomenonTime asc",
      "$select": "@iot.id,phenomenonTime,result,resultTime",
      "$filter": observation_filter,
   }

   rows: List[Dict[str, Any]] = []
   loaded_for_stream = 0
   for obs in iter_collection(endpoint, params=params, max_pages=max_pages, sleep_s=sleep_s):
      rows.append(_observation_row(ds, obs))
      loaded_for_stream += 1
      if max_observations_per_datastream is not None and loaded_for_stream >= max_observations_per_datastream:
         break

   return rows


def _write_manifest(manifest_path: Path, payload: Dict[str, Any]) -> None:
   manifest_path.parent.mkdir(parents=True, exist_ok=True)
   manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _build_resume_signature(args: argparse.Namespace) -> Dict[str, Any]:
   return {
      "base_url": args.base_url,
      "start_time": args.start_time,
      "end_time": args.end_time,
      "page_size": args.page_size,
      "max_pages": args.max_pages,
      "max_observations_per_datastream": args.max_observations_per_datastream,
      "observed_property_contains": args.observed_property_contains,
      "exclude_observed_property_contains": args.exclude_observed_property_contains,
      "sensor_id_contains": args.sensor_id_contains,
      "sensor_ids_file": args.sensor_ids_file,
   }


def export_sample_data(args: argparse.Namespace) -> None:
   base_url = args.base_url.rstrip("/")
   out_dir = Path(args.out_dir) / "sample"
   out_dir.mkdir(parents=True, exist_ok=True)

   endpoints = discover_endpoints(base_url)
   write_json(out_dir / "discovered_endpoints.json", endpoints)

   for endpoint in endpoints:
      name = endpoint.get("name")
      url = endpoint.get("url")
      if not name or not url:
         continue

      params = {"$top": args.sample_size}
      rows = list(iter_collection(url, params=params, max_pages=1, sleep_s=args.sleep_s))
      write_json(out_dir / f"{name}.json", rows)
      print(f"[sample] {name}: {len(rows)} rows -> {out_dir / f'{name}.json'}")


def export_full_datasets(args: argparse.Namespace) -> None:
   base_url = args.base_url.rstrip("/")
   out_dir = Path(args.out_dir) / "full"
   out_dir.mkdir(parents=True, exist_ok=True)

   # 1) Build datastream index: use server-side API filtering for speed,
   #    then apply any remaining client-side filters (e.g. sensor_ids_file).
   # Preserve original casing — the Samen Meten API is case-sensitive on Thing/name.
   thing_name_prefixes = [p.strip() for p in (args.thing_name_prefix or "").split(",") if p.strip()]
   observed_property_tokens = _parse_csv_tokens(args.observed_property_contains)

   ds_index_raw = build_datastream_index(
      base_url=base_url,
      page_size=args.page_size,
      max_pages=args.max_pages,
      max_datastreams=args.max_datastreams,
      sleep_s=args.sleep_s,
      thing_name_prefixes=thing_name_prefixes,
      observed_property_tokens=observed_property_tokens,
   )
   print(f"[full] datastream index fetched: {len(ds_index_raw)} datastreams")

   # Client-side secondary filter (sensor_ids_file, sensor_id_contains,
   # exclude_observed_property_contains, etc.)
   ds_index = _filter_datastreams(ds_index_raw, args)
   if len(ds_index) != len(ds_index_raw):
      print(f"[full] client-side filter applied: {len(ds_index_raw)} -> {len(ds_index)}")

   write_csv(
      out_dir / "datastream_index.csv",
      rows=[r.__dict__ for r in ds_index],
      fieldnames=list(DatastreamIndexRow.__annotations__.keys()),
   )
   print(f"[full] datastream_index.csv rows: {len(ds_index)}")

   # 2) Build sensor metadata (deduplicated by sensor_id).
   sensor_seen: set[str] = set()
   sensor_metadata_rows: List[Dict[str, Any]] = []
   for row in ds_index:
      if row.sensor_id in sensor_seen:
         continue
      sensor_seen.add(row.sensor_id)
      sensor_metadata_rows.append(
         {
            "sensor_id": row.sensor_id,
            "sensor_name": row.thing_name or row.sensor_name,
            "sensor_type": row.sensor_name,
            "manufacturer": "",
            "model": "",
            "installation_height_m": "",
            "latitude": row.latitude,
            "longitude": row.longitude,
            "neighborhood": row.municipality,
            "community_id": "",
            "start_date_utc": "",
            "end_date_utc": "",
            "status": "active",
            "intended_reference_station_id": row.intended_reference_station_id,
            "knmicode": row.knmicode,
            "notes": "Auto-generated from Thing/Location metadata and Thing properties",
         }
      )

   write_csv(
      out_dir / "sensor_metadata.csv",
      rows=sensor_metadata_rows,
      fieldnames=[
         "sensor_id",
         "sensor_name",
         "sensor_type",
         "manufacturer",
         "model",
         "installation_height_m",
         "latitude",
         "longitude",
         "neighborhood",
         "community_id",
         "start_date_utc",
         "end_date_utc",
         "status",
         "intended_reference_station_id",
         "knmicode",
         "notes",
      ],
   )
   print(f"[full] sensor_metadata.csv rows: {len(sensor_metadata_rows)}")

   # 3) Extract sensor observations in long format.
   observation_filter = _build_observation_filter(args.start_time, args.end_time)
   observation_fields = [
      "sensor_id",
      "datastream_id",
      "observation_id",
      "timestamp_utc",
      "pollutant_name",
      "value_raw",
      "value_calibrated",
      "unit",
      "temperature_c",
      "relative_humidity_pct",
      "wind_speed_mps",
      "wind_direction_deg",
      "qc_flag_code",
      "qc_flag_reason",
      "ingestion_source",
      "record_version",
   ]
   partial_obs_path = out_dir / "sensor_observations_long.partial.csv"
   final_obs_path = out_dir / "sensor_observations_long.csv"
   manifest_path = out_dir / "sensor_observations_resume_manifest.json"

   resume_signature = _build_resume_signature(args)
   completed_ids: set[str] = set()
   total_obs_rows = 0

   if args.resume and manifest_path.exists() and partial_obs_path.exists():
      manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
      if manifest.get("signature") == resume_signature:
         completed_ids = set(str(x) for x in (manifest.get("completed_datastream_ids") or []))
         total_obs_rows = int(manifest.get("total_rows", 0))
         print(f"[full] resume enabled: {len(completed_ids)} datastreams already completed")
      else:
         print("[full] resume manifest signature mismatch; starting a fresh observation export")

   pending_ds = [ds for ds in ds_index if ds.datastream_id not in completed_ids]
   # Only reuse the partial CSV if ALL datastreams in this run are already completed.
   # Guard against ds_index being empty after filtering (which would wrongly reuse a
   # stale partial file from a previous unfiltered run).
   all_done_resume = (
      not pending_ds
      and len(ds_index) > 0
      and partial_obs_path.exists()
      and completed_ids.issuperset(ds.datastream_id for ds in ds_index)
   )
   if all_done_resume:
      shutil.copy2(partial_obs_path, final_obs_path)
      print(f"[full] no pending datastreams; reused existing observation export: {final_obs_path}")
   else:
      write_header = not (args.resume and partial_obs_path.exists() and completed_ids)
      obs_mode = "a" if not write_header else "w"
      with partial_obs_path.open(obs_mode, encoding="utf-8", newline="") as obs_file:
         writer = csv.DictWriter(obs_file, fieldnames=observation_fields, extrasaction="ignore")
         if write_header:
            writer.writeheader()

         workers = max(1, int(args.workers))
         if workers == 1:
            for idx, ds in enumerate(pending_ds):
               rows = _fetch_observations_for_datastream(
                  base_url=base_url,
                  ds=ds,
                  page_size=args.page_size,
                  max_pages=args.max_pages,
                  sleep_s=args.sleep_s,
                  observation_filter=observation_filter,
                  max_observations_per_datastream=args.max_observations_per_datastream,
               )
               if rows:
                  writer.writerows(rows)
                  obs_file.flush()
               total_obs_rows += len(rows)
               completed_ids.add(ds.datastream_id)

               if ((idx + 1) % max(1, int(args.checkpoint_every)) == 0) or (idx + 1 == len(pending_ds)):
                  _write_manifest(
                     manifest_path,
                     {
                        "status": "running",
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "signature": resume_signature,
                        "completed_datastream_ids": sorted(completed_ids),
                        "total_rows": total_obs_rows,
                     },
                  )
               if (idx + 1) % 25 == 0 or (idx + 1) == len(pending_ds):
                  print(f"[full] processed datastreams: {idx + 1}/{len(pending_ds)}")
         else:
            print(f"[full] fetching observations in parallel with {workers} workers")
            completed = 0
            with ThreadPoolExecutor(max_workers=workers) as executor:
               futures = {
                  executor.submit(
                     _fetch_observations_for_datastream,
                     base_url,
                     ds,
                     args.page_size,
                     args.max_pages,
                     args.sleep_s,
                     observation_filter,
                     args.max_observations_per_datastream,
                  ): ds.datastream_id
                  for ds in pending_ds
               }

               for future in as_completed(futures):
                  ds_id = futures[future]
                  rows = future.result()
                  if rows:
                     writer.writerows(rows)
                     obs_file.flush()
                  total_obs_rows += len(rows)
                  completed_ids.add(ds_id)
                  completed += 1

                  if (completed % max(1, int(args.checkpoint_every)) == 0) or (completed == len(pending_ds)):
                     _write_manifest(
                        manifest_path,
                        {
                           "status": "running",
                           "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "signature": resume_signature,
                           "completed_datastream_ids": sorted(completed_ids),
                           "total_rows": total_obs_rows,
                        },
                     )
                  if completed % 25 == 0 or completed == len(pending_ds):
                     print(f"[full] processed datastreams: {completed}/{len(pending_ds)}")

      shutil.copy2(partial_obs_path, final_obs_path)

   _write_manifest(
      manifest_path,
      {
         "status": "done",
         "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "signature": resume_signature,
         "completed_datastream_ids": sorted(completed_ids),
         "total_rows": total_obs_rows,
      },
   )
   print(f"[full] sensor_observations_long.csv rows: {total_obs_rows}")

   # 4) Create challenge-2 wind-rose-ready aggregates from long observations.
   if args.build_wind_rose:
      with final_obs_path.open("r", encoding="utf-8", newline="") as f:
         obs_rows = list(csv.DictReader(f))
      speed_edges = _parse_speed_bin_edges(args.wind_speed_bin_edges)
      wind_rose_rows = build_wind_rose_aggregates(
         obs_rows=obs_rows,
         direction_bin_size_deg=args.wind_direction_bin_size_deg,
         speed_bin_edges=speed_edges,
         granularity=args.wind_granularity,
      )
      write_csv(
         out_dir / "wind_rose_aggregates.csv",
         rows=wind_rose_rows,
         fieldnames=[
            "sensor_id",
            "pollutant_name",
            "period_start_utc",
            "period_end_utc",
            "direction_bin_deg_start",
            "direction_bin_deg_end",
            "speed_bin_mps_start",
            "speed_bin_mps_end",
            "concentration_mean",
            "sample_count",
            "granularity_label",
         ],
      )
      print(f"[full] wind_rose_aggregates.csv rows: {len(wind_rose_rows)}")
      if not wind_rose_rows:
         print(
            "[full] warning: no wind-rose rows generated. "
            "No matching wind-direction/wind-speed datastreams were found for the selected scope. "
            "Try increasing --max-datastreams, extending the time range, or enriching with external wind data."
         )

   # 5) Create schema-aligned placeholders for datasets not directly inferable from API alone.
   write_csv(
      out_dir / "reference_station_observations.csv",
      rows=[],
      fieldnames=[
         "reference_station_id",
         "timestamp_utc",
         "pollutant_name",
         "value_reference",
         "unit",
         "qc_flag_code",
         "data_provider",
      ],
   )
   mapping_rows: List[Dict[str, Any]] = []
   for row in sensor_metadata_rows:
      ref_id = str(row.get("intended_reference_station_id", "")).strip()
      if not ref_id:
         continue
      mapping_rows.append(
         {
            "sensor_id": row.get("sensor_id", ""),
            "reference_station_id": ref_id,
            "mapping_method": "assigned_by_samenmeten_property",
            "distance_km": "",
            "valid_from_utc": args.start_time or "",
            "valid_to_utc": args.end_time or "",
            "confidence_score": 0.9,
         }
      )

   write_csv(
      out_dir / "sensor_reference_mapping.csv",
      rows=mapping_rows,
      fieldnames=[
         "sensor_id",
         "reference_station_id",
         "mapping_method",
         "distance_km",
         "valid_from_utc",
         "valid_to_utc",
         "confidence_score",
      ],
   )
   print(f"[full] sensor_reference_mapping.csv rows: {len(mapping_rows)}")
   write_csv(
      out_dir / "historical_quality_events.csv",
      rows=[],
      fieldnames=[
         "event_id",
         "sensor_id",
         "start_time_utc",
         "end_time_utc",
         "label_type",
         "severity",
         "confirmed_by",
         "description",
         "remediation_action",
         "outcome",
      ],
   )

   notes = {
      "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "base_url": base_url,
      "notes": [
         "reference_station_observations.csv, sensor_reference_mapping.csv and historical_quality_events.csv are placeholder files.",
         "Populate them from external reference-network data and quality label sources.",
         "Use datastream_index.csv to map Thing/Sensor/ObservedProperty for further enrichment.",
         "sensor_observations_resume_manifest.json + sensor_observations_long.partial.csv enable resume mode.",
      ],
   }
   write_json(out_dir / "README_export_notes.json", notes)

   print(f"[full] Wrote files under: {out_dir}")


def build_parser() -> argparse.ArgumentParser:
   parser = argparse.ArgumentParser(description="Download SensorThings data for sample and full dataset preparation.")
   parser.add_argument("mode", choices=["sample", "full", "build-wind-rose"], help="sample: quick endpoint snapshot, full: challenge-ready CSV exports, build-wind-rose: aggregate wind-rose from existing merged observations CSV")
   parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="SensorThings API base URL")
   parser.add_argument("--out-dir", default="data_exports", help="Output directory root")
   parser.add_argument("--page-size", type=int, default=500, help="API page size ($top)")
   parser.add_argument("--max-pages", type=int, default=None, help="Maximum pages per endpoint/datastream")
   parser.add_argument("--sleep-s", type=float, default=0.0, help="Sleep between page requests (seconds)")
   parser.add_argument("--workers", type=int, default=1, help="Parallel workers for datastream observation fetch (full mode)")
   parser.add_argument("--resume", action="store_true", help="Resume interrupted full-mode observation export from manifest/partial CSV")
   parser.add_argument("--checkpoint-every", type=int, default=25, help="Write resume manifest every N completed datastreams")

   # Sample mode arguments
   parser.add_argument("--sample-size", type=int, default=25, help="Rows per endpoint for sample mode")

   # Full mode arguments
   parser.add_argument("--start-time", default=None, help="Observation lower bound, ISO UTC e.g. 2025-01-01T00:00:00Z")
   parser.add_argument("--end-time", default=None, help="Observation upper bound, ISO UTC e.g. 2025-02-01T00:00:00Z")
   parser.add_argument("--max-datastreams", type=int, default=None, help="Cap number of datastreams for first runs")
   parser.add_argument(
      "--thing-name-prefix",
      default="",
      help="Comma-separated Thing name prefixes to filter server-side (e.g. HLL_hl_device,GLOBE). Fastest way to target a project.",
   )
   parser.add_argument(
      "--observed-property-contains",
      default="",
      help="Comma-separated property tokens to include (e.g. pm25,no2). Applied server-side.",
   )
   parser.add_argument(
      "--exclude-observed-property-contains",
      default="",
      help="Comma-separated property tokens to exclude (e.g. winddirection,windspeed)",
   )
   parser.add_argument(
      "--sensor-id-contains",
      default="",
      help="Comma-separated substrings; only matching sensor_id values are included",
   )
   parser.add_argument(
      "--sensor-ids-file",
      default=None,
      help="Optional text file with sensor IDs (one per line or comma-separated)",
   )
   parser.add_argument(
      "--max-observations-per-datastream",
      type=int,
      default=None,
      help="Cap observations per datastream to avoid very large first export",
   )
   parser.add_argument(
      "--build-wind-rose",
      action="store_true",
      help="Generate wind_rose_aggregates.csv from matched wind and pollutant observations",
   )
   parser.add_argument(
      "--wind-granularity",
      choices=["hourly", "daily", "weekly"],
      default="daily",
      help="Time aggregation granularity for wind-rose output",
   )
   parser.add_argument(
      "--wind-direction-bin-size-deg",
      type=float,
      default=22.5,
      help="Direction bin size in degrees (22.5 gives 16 wind sectors)",
   )
   parser.add_argument(
      "--wind-speed-bin-edges",
      default="0,1,3,5,8,12,20,inf",
      help="Comma-separated wind speed bin edges, include inf as last edge",
   )
   # build-wind-rose mode arguments
   parser.add_argument(
      "--obs-file",
      default=None,
      help="Path to merged sensor_observations_long.csv for build-wind-rose mode (defaults to <out-dir>/full/sensor_observations_long.csv)",
   )
   parser.add_argument(
      "--chunk-months",
      type=int,
      default=0,
      help="When >0 in full mode, split [start,end) into chunk windows of this many months and export each chunk separately",
   )
   return parser


def export_wind_rose_from_file(args: argparse.Namespace) -> None:
   obs_file = Path(args.obs_file) if args.obs_file else Path(args.out_dir) / "full" / "sensor_observations_long.csv"
   out_dir = Path(args.out_dir) / "full"

   if not obs_file.exists():
      print(f"Observations file not found: {obs_file}", file=sys.stderr)
      return

   print(f"[wind-rose] Reading merged observations from: {obs_file}")
   obs_rows: list[dict] = []
   with obs_file.open("r", encoding="utf-8", newline="") as f:
      obs_rows = list(csv.DictReader(f))
   print(f"[wind-rose] Loaded {len(obs_rows)} rows")

   speed_edges = _parse_speed_bin_edges(args.wind_speed_bin_edges)
   wind_rose_rows = build_wind_rose_aggregates(
      obs_rows=obs_rows,
      direction_bin_size_deg=args.wind_direction_bin_size_deg,
      speed_bin_edges=speed_edges,
      granularity=args.wind_granularity,
   )
   write_csv(
      out_dir / "wind_rose_aggregates.csv",
      rows=wind_rose_rows,
      fieldnames=[
         "sensor_id",
         "pollutant_name",
         "period_start_utc",
         "period_end_utc",
         "direction_bin_deg_start",
         "direction_bin_deg_end",
         "speed_bin_mps_start",
         "speed_bin_mps_end",
         "concentration_mean",
         "sample_count",
         "granularity_label",
      ],
   )
   print(f"[wind-rose] wind_rose_aggregates.csv rows: {len(wind_rose_rows)}")
   if not wind_rose_rows:
      print(
         "[wind-rose] warning: no rows generated. Ensure wind_speed_mps and wind_direction_deg "
         "columns are populated in the observations file (run merge_wind.py first)."
      )


def main() -> int:
   parser = build_parser()
   args = parser.parse_args()

   try:
      if args.mode == "sample":
         export_sample_data(args)
      elif args.mode == "build-wind-rose":
         export_wind_rose_from_file(args)
      else:
         if args.chunk_months > 0:
            if not args.start_time or not args.end_time:
               raise ValueError("--chunk-months requires --start-time and --end-time")
            start_iso = _to_utc_iso_or_raise(args.start_time)
            end_iso = _to_utc_iso_or_raise(args.end_time)
            windows = _split_time_windows(start_iso, end_iso, int(args.chunk_months))
            print(f"[full] chunk mode: {len(windows)} windows")

            base_out = Path(args.out_dir)
            for i, (w_start, w_end) in enumerate(windows, start=1):
               chunk_label = f"{w_start[:10]}_{w_end[:10]}"
               chunk_out = base_out / "chunks" / chunk_label
               chunk_args = argparse.Namespace(**vars(args))
               chunk_args.start_time = w_start
               chunk_args.end_time = w_end
               chunk_args.out_dir = str(chunk_out)
               print(f"[full] chunk {i}/{len(windows)}: {w_start} -> {w_end}  out={chunk_out}")
               export_full_datasets(chunk_args)
         else:
            export_full_datasets(args)
   except KeyboardInterrupt:
      print("Interrupted by user", file=sys.stderr)
      return 130
   except Exception as exc:  # pylint: disable=broad-except
      print(f"Error: {exc}", file=sys.stderr)
      return 1

   return 0


if __name__ == "__main__":
   raise SystemExit(main())