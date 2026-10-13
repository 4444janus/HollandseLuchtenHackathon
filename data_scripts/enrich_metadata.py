#!/usr/bin/env python3
"""Enrich sensor_metadata.csv with coordinates and KNMI station codes.

Fetches each Thing's Locations and properties.knmicode from the Samen Meten API
and back-fills latitude, longitude, and knmicode into sensor_metadata.csv.

Usage:
    python data_scripts/enrich_metadata.py
    python data_scripts/enrich_metadata.py \\
        --metadata data_exports/full/sensor_metadata.csv \\
        --base-url https://api-samenmeten.rivm.nl/v1.0 \\
        --sleep-s 0.1
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api-samenmeten.rivm.nl/v1.0"


def _get(url: str) -> Dict[str, Any]:
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _knmi_station_id_from_code(knmicode: str) -> Optional[str]:
    text = (knmicode or "").strip()
    if not text:
        return None
    numeric = text.replace("knmi_", "").strip()
    if not numeric.isdigit():
        return None
    # Most KNMI station IDs in EDR locations endpoint use this prefix.
    return f"0-20000-0-0{numeric}"


def _load_knmi_station_coords(path: Path) -> Dict[str, tuple[float, float]]:
    coords: Dict[str, tuple[float, float]] = {}
    if not path.exists():
        return coords

    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sid = (row.get("station_id") or "").strip()
            lat = _to_float(row.get("lat"))
            lon = _to_float(row.get("lon"))
            if sid and lat is not None and lon is not None:
                coords[sid] = (lat, lon)
    return coords


def fetch_thing_enrichment(
    base_url: str,
    thing_id: str,
    sleep_s: float,
) -> Dict[str, Any]:
    """Return dict with lat, lon and key Thing properties for a Thing ID."""
    result: Dict[str, Any] = {
        "lat": None,
        "lon": None,
        "knmicode": "",
        "intended_reference_station_id": "",
        "municipality": "",
    }

    # Fetch Thing properties for knmicode.
    thing_url = f"{base_url}/Things({thing_id})?$select=properties"
    thing_data = _get(thing_url)
    props = thing_data.get("properties") or {}
    knmicode = str(props.get("knmicode") or "").strip()
    ref_station = str(props.get("pm25closecode") or "").strip()
    municipality = str(props.get("gemeente") or "").strip()
    result["knmicode"] = knmicode
    result["intended_reference_station_id"] = ref_station
    result["municipality"] = municipality

    if sleep_s > 0:
        time.sleep(sleep_s)

    # Fetch linked Locations for coordinates.
    locs_url = f"{base_url}/Things({thing_id})/Locations?$top=1&$select=location"
    locs_data = _get(locs_url)
    for loc in (locs_data.get("value") or []):
        geo = loc.get("location") or {}
        coords = geo.get("coordinates") or []
        if len(coords) >= 2:
            result["lon"] = _to_float(coords[0])
            result["lat"] = _to_float(coords[1])
        break

    return result


def enrich(metadata_path: Path, base_url: str, sleep_s: float, knmi_stations_csv: Optional[Path]) -> None:
    with metadata_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames: List[str] = list(reader.fieldnames or [])
        rows = list(reader)

    if not rows:
        print("Metadata file is empty.")
        return

    # Ensure target enrichment columns exist.
    for col in [
        "intended_reference_station_id",
        "knmicode",
        "municipality",
        "distance_to_knmi_km",
    ]:
        if col not in fieldnames:
            fieldnames.append(col)

    enriched = 0
    for i, row in enumerate(rows):
        sensor_id = (row.get("sensor_id") or "").strip()
        if not sensor_id:
            continue

        already_has_coords = (row.get("latitude") or "").strip() and (row.get("longitude") or "").strip()
        already_has_knmi = (row.get("knmicode") or "").strip()
        already_has_ref = (row.get("intended_reference_station_id") or "").strip()
        if already_has_coords and already_has_knmi and already_has_ref:
            continue  # nothing to fill in

        print(f"  Enriching sensor {sensor_id} ({i + 1}/{len(rows)})...")
        try:
            info = fetch_thing_enrichment(base_url, sensor_id, sleep_s=sleep_s)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"    Warning: {exc}")
            continue

        if not already_has_coords:
            if info["lat"] is not None:
                row["latitude"] = info["lat"]
            if info["lon"] is not None:
                row["longitude"] = info["lon"]
        if not already_has_knmi and info["knmicode"]:
            row["knmicode"] = info["knmicode"]
        if not already_has_ref and info["intended_reference_station_id"]:
            row["intended_reference_station_id"] = info["intended_reference_station_id"]
        if not (row.get("municipality") or "").strip() and info["municipality"]:
            row["municipality"] = info["municipality"]

        enriched += 1

        if sleep_s > 0:
            time.sleep(sleep_s)

    # Optional: calculate distance to assigned KNMI station when station list is available.
    knmi_station_map = _load_knmi_station_coords(knmi_stations_csv) if knmi_stations_csv else {}
    if knmi_station_map:
        for row in rows:
            lat = _to_float(row.get("latitude"))
            lon = _to_float(row.get("longitude"))
            knmi_code = (row.get("knmicode") or "").strip()
            if lat is None or lon is None or not knmi_code:
                continue

            station_id = _knmi_station_id_from_code(knmi_code)
            if station_id and station_id in knmi_station_map:
                s_lat, s_lon = knmi_station_map[station_id]
                row["distance_to_knmi_km"] = round(_haversine_km(lat, lon, s_lat, s_lon), 2)

    # Back up original.
    bak_path = metadata_path.with_suffix(".csv.bak")
    import shutil
    shutil.copy2(metadata_path, bak_path)
    print(f"Backup saved: {bak_path}")

    with metadata_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # Ensure all new columns have a default.
            row.setdefault("intended_reference_station_id", "")
            row.setdefault("knmicode", "")
            row.setdefault("municipality", "")
            row.setdefault("distance_to_knmi_km", "")
            writer.writerow(row)

    print(f"Enriched {enriched}/{len(rows)} sensors.")
    print(f"Wrote: {metadata_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enrich sensor_metadata.csv with coordinates and knmicode")
    parser.add_argument("--metadata", default="data_exports/full/sensor_metadata.csv")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--sleep-s", type=float, default=0.15)
    parser.add_argument(
        "--knmi-stations-csv",
        default="data_exports/knmi/knmi_stations.csv",
        help="Optional KNMI stations CSV to derive distance_to_knmi_km",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    path = Path(args.metadata)
    if not path.exists():
        print(f"Metadata file not found: {path}")
        return 2
    knmi_stations_csv = Path(args.knmi_stations_csv) if args.knmi_stations_csv else None
    enrich(path, base_url=args.base_url, sleep_s=args.sleep_s, knmi_stations_csv=knmi_stations_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
