# Hollandse Luchten Hackathon: Dataset Preparation Checklist

## Global Standards

1. Timestamp format: ISO 8601 in UTC (example: 2026-08-10T14:00:00Z).
2. Primary sensor key: sensor_id as a stable string.
3. Geolocation: latitude and longitude in WGS84 decimal degrees.
4. Missing values: use null (avoid sentinel values like -999).
5. Units: include a unit field for each measured variable.
6. Quality flags: store both raw flag code and human-readable label.
7. Granularity: choose one format and stay consistent:
	- Long format: one row per sensor per timestamp per pollutant.
	- Wide format: one row per sensor per timestamp with multiple pollutant columns.

## Dataset 1: Sensor Observations (Core, All Challenges)

Fields:

1. sensor_id (string, required)
2. timestamp_utc (datetime, required)
3. pollutant_name (string, required; examples: PM2.5, PM10, NO2)
4. value_raw (float, optional but recommended)
5. value_calibrated (float, required for analysis)
6. unit (string, required; example: ug/m3)
7. temperature_c (float, optional)
8. relative_humidity_pct (float, optional)
9. wind_speed_mps (float, required for challenge 2)
10. wind_direction_deg (float 0-360, required for challenge 2)
11. qc_flag_code (string, optional; examples: OK, SUSPECT, BAD)
12. qc_flag_reason (string, optional)
13. ingestion_source (string, optional; example: api, batch)
14. record_version (integer, optional, for traceability)

Suggested key: sensor_id + timestamp_utc + pollutant_name

## Dataset 2: Sensor Metadata (Core, All Challenges)

Fields:

1. sensor_id (string, required, unique)
2. sensor_name (string, optional)
3. sensor_type (string, required)
4. manufacturer (string, optional)
5. model (string, optional)
6. installation_height_m (float, optional)
7. latitude (float, required)
8. longitude (float, required)
9. neighborhood (string, optional)
10. community_id (string, optional)
11. start_date_utc (datetime, required)
12. end_date_utc (datetime, optional)
13. status (string, required; examples: active, inactive, maintenance)
14. intended_reference_station_id (string, optional, useful for challenge 1)
15. notes (string, optional)

## Dataset 3: Reference Station Observations (Core, Challenge 1 Critical)

Fields:

1. reference_station_id (string, required)
2. timestamp_utc (datetime, required)
3. pollutant_name (string, required)
4. value_reference (float, required)
5. unit (string, required)
6. qc_flag_code (string, optional)
7. data_provider (string, optional)

Suggested key: reference_station_id + timestamp_utc + pollutant_name

## Dataset 4: Sensor to Reference Mapping (Core, Challenge 1 Critical)

Fields:

1. sensor_id (string, required)
2. reference_station_id (string, required)
3. mapping_method (string, required; examples: nearest, assigned_by_domain_expert)
4. distance_km (float, optional)
5. valid_from_utc (datetime, optional)
6. valid_to_utc (datetime, optional)
7. confidence_score (float 0-1, optional)

## Dataset 5: Historical Quality and Incident Labels

Challenge relevance:

- Challenge 1: critical
- Challenge 2: useful

Fields:

1. event_id (string, required, unique)
2. sensor_id (string, required)
3. start_time_utc (datetime, required)
4. end_time_utc (datetime, required)
5. label_type (string, required; examples: calibration_drift, transmission_fault, maintenance)
6. severity (string, optional; examples: low, medium, high)
7. confirmed_by (string, optional; examples: operator, algorithm, both)
8. description (string, optional)
9. remediation_action (string, optional)
10. outcome (string, optional; examples: recalibrated, replaced, false_positive)

## Dataset 6: Neighborhood Context for Correlation (Challenge 1 Useful)

Fields:

1. sensor_id (string, required)
2. neighbor_sensor_id (string, required)
3. relation_type (string, required; examples: nearest_1km, same_community)
4. distance_km (float, optional)
5. prevailing_wind_alignment_score (float 0-1, optional)

Note: this can also be computed on the fly, but precomputing often speeds iteration.

## Dataset 7A: Precomputed Wind Roses (Challenge 2 Option)

Fields:

1. rose_id (string, required)
2. sensor_id (string, required)
3. period_start_utc (datetime, required)
4. period_end_utc (datetime, required)
5. pollutant_name (string, required)
6. direction_bin_deg_start (float, required)
7. direction_bin_deg_end (float, required)
8. speed_bin_mps_start (float, optional)
9. speed_bin_mps_end (float, optional)
10. concentration_stat (float, required; for example mean or percentile)
11. sample_count (integer, required)
12. granularity_label (string, required; examples: hourly, daily, weekly)

Note: one wind rose consists of multiple rows (one row per bin).

## Dataset 7B: Wind Rose Image Index (Challenge 2 Option)

Fields:

1. rose_id (string, required)
2. sensor_id (string, required)
3. period_start_utc (datetime, required)
4. period_end_utc (datetime, required)
5. pollutant_name (string, required)
6. image_path_or_uri (string, required)
7. rendering_version (string, optional)
8. granularity_label (string, required)

Use this if your approach is vision-first and image-based.

## Dataset 8: Geospatial Layers (Challenge 2 Useful)

Fields:

1. region_id (string, required)
2. geometry_type (string, required; example: polygon)
3. geometry_wkt_or_geojson (string, required)
4. layer_name (string, required; examples: neighborhoods, industrial_zones)
5. source (string, optional)

## Dataset 9: Analysis Tool Catalog (Challenge 3 Critical)

Fields:

1. tool_name (string, required, unique)
2. tool_category (string, required; examples: statistics, timeseries, geospatial)
3. description (string, required)
4. input_schema_json (string/json, required)
5. output_schema_json (string/json, required)
6. required_parameters (string/json array, required)
7. optional_parameters (string/json array, optional)
8. constraints (string/json, optional)
9. failure_modes (string/json array, optional)
10. examples (string/json array, required)
11. implementation_ref (string, required)
12. version (string, required)

## Dataset 10: Tool Documentation Snippets (Challenge 3 Critical)

Fields:

1. doc_id (string, required)
2. tool_name (string, required)
3. section_title (string, required)
4. content (text, required)
5. source_link_or_repo_path (string, optional)
6. last_updated_utc (datetime, required)

## Dataset 11: Agent Audit Trail Logs (Challenge 3 Deliverable-Critical)

Fields:

1. request_id (string, required)
2. user_query (text, required)
3. query_timestamp_utc (datetime, required)
4. interpreted_intent (string, required)
5. selected_tools (string/json array, required)
6. execution_steps (string/json array, required)
7. tool_inputs (string/json, required)
8. tool_outputs_summary (string/json, required)
9. confidence_score (float 0-1, required)
10. final_answer (text, required)
11. warnings_or_uncertainty (text, optional)
12. latency_ms (integer, optional)
13. model_version (string, optional)

## Minimum Validation Rules Before Modeling

1. No duplicate primary keys in any dataset.
2. Timestamp coverage check per sensor: expected cadence versus actual cadence.
3. Unit consistency per pollutant.
4. Physically impossible value checks (for example negative PM values).
5. Wind validation: direction in 0-360 and speed >= 0.
6. Sensor-reference overlap windows exist for evaluation periods.
7. Sufficient labeled fault windows for challenge 1 train/validation/testing.
8. For challenge 2, each rose period has enough samples.
9. For challenge 3, every tool in the catalog has at least one executable example.
