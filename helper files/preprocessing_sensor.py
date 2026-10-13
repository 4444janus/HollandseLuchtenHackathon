"""
Hollandse Luchten · Low-cost sensor preprocessing pipeline
===========================================================================
This script implements a modular, stepwise pipeline to explore, QA/QC, filter,
detect outliers, and impute low-cost air quality sensor measurements (default: PM2.5)
with temporal diagnostics and outputs.

IMPORTANT: The input sensor measurements are assumed to be **already calibrated**.
No correction or calibration is performed here. External data (reference, meteorology)
are used **only for diagnostics and flagging**, never to adjust the sensor values.

Pipeline overview
-----------------
01) Ingest & standardize
02) Basic quality control (QC) & flagging (bitmask)
03) Statistics & EDA (Plotly)
04) Outlier detection (Hampel, STL, optional reference residuals; optional RH context flags)
05) Filtering (flag-based, configurable)
06) Imputation (gap-filling, conservative)
07) Clustering (optional; correlation-based)
08) Export & simple HTML index

Hooks for external data (reference + meteorology)
-------------------------------------------------
- Reference stations (e.g., Luchtmeetnet API) can be joined to create **residual outlier flags**.
- Meteorology (RH, T, wind) can be joined to create **contextual QC flags** (e.g., high RH),
  and to enrich EDA; **no corrections** are applied to sensor time series.

Author:  Jorane Rogier
Updated: 2025-09-07
"""

from __future__ import annotations

import argparse
import json
import textwrap
import warnings
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Dict, List, Literal, Optional, Tuple
import logging
import numpy as np                                          
import pandas as pd
from pathlib import Path

# Plotly
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

# Science & stats
from scipy import stats
from scipy.cluster.hierarchy import linkage, fcluster
from statsmodels.tsa.seasonal import STL

# Local
from data_ingestion import (
    resolve_sensor_list,
    ingest_sensor_data,
)

logging.basicConfig(level=logging.INFO)
pd.set_option('future.no_silent_downcasting', True)
# ------------------------------
# 0) CONFIGURATION 
# ------------------------------

@dataclass
class Config:
    # --- Sensor selection ---
    # "all"       : all HLL sensors from the Samen Meten API (no project filter)
    # "holu"      : HoLu project sensors (device id >= 221 OR gemeente in holu_municipalities)
    # "holu_plus" : HoLu sensors + additional sensors from sensor_list
    # "manual"    : only the sensors listed in sensor_list
    sensor_mode: Literal["all", "holu", "holu_plus", "manual"] = "holu_plus"
    sensor_list: List[str] = field(default_factory=lambda: ["LTD_90916"])  # used if sensor_mode is "manual" or "holu_plus"
    holu_min_id: int = 221
    holu_municipalities: List[str] = field(default_factory=lambda: ["Hilversum"])

    pollutant: str = "pm25_kal_rivm"
    value_col: str = "pm25_kal_rivm"
    sensor_id_col: str = "id"
    timestamp_col: str = "time"
    lml_station_col: str = "luchtmeetnetstation"
    lon_col: str = "lon"
    lat_col: str = "lat"
    start_date: str = "2026-06-30"
    end_date: str   = "2026-07-14"
    tz: str = "UTC"
    expected_value_range: Tuple[float, float] = (0.0, 200.0)  # plausible bounds (µg/m³)
    spike_threshold: float = 50.0   # μg/m³ absolute jump between consecutive hours to flag
    spike_zscore: float = 3.0        # z-score threshold for local spikes
    relocation_distance_m: float = 100.0  # meters; flag if sensor moves > this
    missing_impute_short_max: int = 3     # hours; short gaps imputed by linear/LOCF
    missing_impute_long_max: int = 12     # hours; longer gaps imputed by clim or STL
    low_var_const : float = 0.5        # concentration for low variability flagging
    stl_period_hours: int = 24       # daily seasonality for STL
    hampel_window: int = 23          # rolling window (hours) for Hampel filter
    hampel_n_sigma: float = 6
    stl_resid_z: float = 6
    many_missing_window : float = 24
    many_missing_frac_threshold : float = 0.75
    rh_high_threshold: float = 90.0  # %RH threshold for context flagging
    temp_diff_threshold: float = 10.0 # temperature difference sensor vs KNMI (°C)
    humidity_diff_threshold: float = 10.0 # humidity difference sensor vs KNMI (%)
    return_tolerance: float = 3.0  # after absolute spike check if value returns back

    # --- Data paths ---
    knmi_dir: Path = Path(r"\\tsn.tno.nl\data\sv\sv-052045\Ralph\projects\HollandseLuchten\data\knmiUurgegevens")
    lml_dir: Path = Path(r"\\app-reken12\L\UserData\bakrit\RIVMfeedApiBatch")
    lml_stations_fp: Path = Path("data/in/lml/luchtmeetnet_meetlocaties.csv")
    gemeenten_gpkg: Path = Path("data/in/BestuurlijkeGebieden_2025.gpkg")

    # --- Output paths ---
    output_dir: Path = Path(f"data/out/sensor_data/{start_date}_{end_date}")
    tmp_dir: Path    = Path(f"data/tmp/sensor_data/{start_date.replace('-', '')}_{end_date.replace('-', '')}")
    figs_dir: Path   = output_dir / 'figs'
    cleaned_dir: Path = output_dir / "cleaned_sensor_data"

    # Visualization
    write_png: bool = True  # requires 'kaleido'; HTML will always be saved
    map_scatter_geo: bool = True  # use px.scatter_geo (no token needed)


CONFIG = Config()

# QA Flag bitmask (power-of-two values so flags can be combined)
# << is a left shift operator, moving the binary 1 X spots
QA_FLAGS = {
    "OK": 0,
    "OUT_OF_RANGE_MIN": 1 << 0,
    "OUT_OF_RANGE_MAX": 1 << 1,
    "DUPLICATE": 1 << 2,
    "MISSING": 1 << 3,
    "SPIKE_ABS": 1 << 4,
    "SPIKE_Z": 1 << 5,
    "RELOCATION": 1 << 6,
    "DEVIANT_TEMP": 1 << 7,
    "DEVIANT_HUMIDITY": 1 << 8,
    "LOW_VARIABILITY_CONST": 1 << 9,
    "LOW_VARIABILITY_LML": 1 << 10,
    "HAMPEL_OUTLIER": 1 << 11,
    "STL_OUTLIER": 1 << 12,
    "REF_RESID_OUTLIER": 1 << 13,
    "RH_CONTEXT": 1 << 14,
    "HUMID_SPIKE": 1 << 15,
    "MANY_MISSING_WINDOW": 1 << 16
}

# -------------#
#  UTILITIES   #
# -------------#

def _jsonify(obj):
    """
    Recursively convert objects to JSON-serializable forms:
    - Path -> str
    - pandas.Timestamp -> ISO string
    - numpy numeric -> Python int/float
    - numpy datetime64 -> ISO string
    - dict/list/tuple -> recurse
    """
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, pd.Timestamp):
        # Always serialize timestamps as ISO-8601 UTC strings
        return obj.isoformat()
    if isinstance(obj, np.generic):
        # numpy scalar (e.g., np.int64, np.float32)
        return obj.item()
    if isinstance(obj, (np.datetime64, )):
        # Convert to pandas Timestamp then to ISO
        return pd.to_datetime(obj).isoformat()
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(v) for v in obj]
    # Fallback: leave as-is if it's already JSON-friendly (str, int, float, bool, None)
    return obj

def ensure_dirs(cfg: Config):
    for p in [cfg.output_dir, cfg.tmp_dir, cfg.figs_dir, cfg.cleaned_dir]:
        Path(p).mkdir(parents=True, exist_ok=True)

def save_fig(fig: go.Figure, out_html: Path, out_png: Optional[Path]=None, write_png=False):
    pio.write_html(fig, file=str(out_html), auto_open=False, include_plotlyjs="cdn")
    if write_png and out_png is not None:
        try:
            fig.write_image(str(out_png), scale=2)
        except Exception as e:
            warnings.warn(f"PNG export failed (install 'kaleido'?): {e}")

def haversine_km(lat1, lon1, lat2, lon2):
    # Great-circle distance (km)
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (np.sin(dlat/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlon/2)**2)
    return 2 * R * np.arcsin(np.sqrt(a))

def meter_distance(lat1, lon1, lat2, lon2):
    return haversine_km(lat1, lon1, lat2, lon2) * 1000.0

def add_flag(current: int, flag_name: str) -> int:
    return current | QA_FLAGS[flag_name]

def within_date_range(ts: pd.Series, start: str, end: str) -> pd.Series:
    return (ts >= pd.Timestamp(start, tz="UTC")) & (ts <= pd.Timestamp(end, tz="UTC"))

def hour_of_week_index(ts: pd.Series) -> pd.Series:
    # 0..167 index: Monday=0
    return (ts.dt.dayofweek * 24 + ts.dt.hour).astype(int)

def stl_outliers(y: pd.Series, period: int, z_th=4.0) -> pd.Series:
    """
    Helper function to flag  points that don't fit the expected seasonal + trend structure of sensor time series.
    """
    y = y.astype(float)
    # fill short gaps so STL can run on (quasi) regularly spaced data
    y_filled = y.interpolate(method='time', limit=6)
    # decompose into seasonal + trend + residual using STL (robust to outliers)
    res = STL(y_filled, period=period, robust=True).fit()
    # residuals relative to the *original* series (not the filled one)
    resid = y - res.trend - res.seasonal
    # z-score the residuals and flag large absolute values
    z = (resid - resid.mean()) / (resid.std(ddof=1) + 1e-9)
    return z.abs() > z_th

def hampel_filter(y: pd.Series, window: int = 7, n_sigma: float = 3.5) -> pd.Series:
    """
    Hampel filter for outlier detection on a 1-D time series.

    Parameters
    ----------
    y : pd.Series
        Series of numeric values. Index can be DatetimeIndex or any index.
        (Rolling window is in number of samples; if you resample hourly,
        window=7 means ~7 hours.)
    window : int
        Rolling window size (in samples). Use an odd number for symmetric centering.
    n_sigma : float
        Threshold in robust z-score units based on MAD (median absolute deviation).

    Returns
    -------
    pd.Series (bool)
        Boolean mask aligned to y.index. True where an outlier is detected.

    Notes
    -----
    - Uses rolling median and rolling MAD; robust to local spikes.
    - Scale factor k=1.4826 turns MAD into a robust estimate of std for Gaussian-like data.
    - https://pub.towardsai.net/comprehensive-guide-to-hampel-filter-for-outlier-detection-53e34da118c6
    """
    y = pd.Series(y).astype(float)

    # Ensure a reasonable window
    win = int(window)
    if win < 3:
        win = 3
    # Centered rolling; require at least ~half the window to compute
    minp = max(3, win // 2)

    # Rolling median and MAD
    med = y.rolling(win, center=True, min_periods=minp).median()
    mad = (y - med).abs().rolling(win, center=True, min_periods=minp).median()

    k = 1.4826  # robust scale factor
    z = (y - med) / (k * mad + 1e-9)
    return z.abs() > n_sigma


def write_provenance(cfg: Config, step: str, extra: dict):
    prov_path = Path(cfg.output_dir) / "pipeline_provenance.json"

    # Build meta and sanitize it before dumping to JSON
    cfg_dict = asdict(cfg)
    meta = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "step": step,
        "config": cfg_dict,
        "extra": extra,
        "note": "Input sensor data assumed pre-calibrated; no calibration/correction applied."
    }
    meta_jsonable = _jsonify(meta)

    if prov_path.exists():
        try:
            old = json.loads(prov_path.read_text())
        except Exception:
            # If file got corrupted or not a list, start fresh as list
            old = []
        if isinstance(old, list):
            old.append(meta_jsonable)
            prov_path.write_text(json.dumps(old, indent=2))
        else:
            prov_path.write_text(json.dumps([old, meta_jsonable], indent=2))
    else:
        prov_path.write_text(json.dumps([meta_jsonable], indent=2))

def decode_qa_flags(flag_column, qa_flags_dict):
    """
    Decodeert een kolom met QA-flags naar een lijst van actieve labels per rij.

    Parameters:
    - flag_column: een pandas Series met integerwaarden (bitmask).
    - qa_flags_dict: een dictionary met flag-namen en hun bitwaarden.

    Returns:
    - Een pandas Series met lijsten van actieve flag-namen.
    """
    return flag_column.apply(
        lambda val: [name for name, bit in qa_flags_dict.items() if (val & bit) > 0]
    )


def make_excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Convert tz-aware datetime columns to naive UTC timestamps for Excel export."""
    out = df.copy()
    for col in out.columns:
        s = out[col]

        if pd.api.types.is_datetime64tz_dtype(s):
            out[col] = s.dt.tz_convert("UTC").dt.tz_localize(None)
            continue

        if s.dtype == "object":
            sample = s.dropna().head(1)
            if not sample.empty:
                v = sample.iloc[0]
                if isinstance(v, pd.Timestamp) and v.tzinfo is not None:
                    out[col] = s.apply(
                        lambda x: x.tz_convert("UTC").tz_localize(None)
                        if isinstance(x, pd.Timestamp) and x.tzinfo is not None
                        else x
                    )

    return out

# --------------------#
# get the sensor data #
# --------------------#

def get_sensor_data(cfg: Config) -> pd.DataFrame:
    """
    Resolve which sensors to process, then fetch PM2.5, LML, and KNMI data
    via the data_ingestion module.
    """
    # 1) Determine sensor list
    sensor_names = resolve_sensor_list(
        mode=cfg.sensor_mode,
        sensor_list=cfg.sensor_list,
        gemeenten_gpkg=cfg.gemeenten_gpkg,
        holu_min_id=cfg.holu_min_id,
        holu_municipalities=cfg.holu_municipalities,
    )
    logging.info(f"Sensors to process ({cfg.sensor_mode}): {len(sensor_names)}")

    # 2) Ingest data (PM2.5 + LML + KNMI)
    df = ingest_sensor_data(
        sensor_names=sensor_names,
        start_date=cfg.start_date,
        end_date=cfg.end_date,
        knmi_dir=cfg.knmi_dir,
        lml_dir=cfg.lml_dir,
        lml_stations_fp=cfg.lml_stations_fp,
    )

    return df

# -------------------#
# 1) BASIC QC FLAGS  #
# -------------------#

def detect_relocations(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    Function to detect of the sensor has moved in the current period.
    If a sensor has moved more than a certain theshold,
    the RELOCATION flag is added to the data.

    Input
    -----------
    df      :   Dataframe containing the sensor observations
    cfg     :   Configuration object

    Returns
    -----------
    df      :   Dataframe with 
                    - additional column moved: stating if the sensor has moved compared to the previous measurement (moved above a certain threshold)
                    - added flag RELOCATION in the qa_flag column if it has moved
    """
    df = df.sort_values([cfg.sensor_id_col, cfg.timestamp_col])

    if "qa_flag" not in df:
        df["qa_flag"] = 0
    for sid, g in df.groupby(cfg.sensor_id_col):
        idx = g.index
        lat = g[cfg.lat_col].astype(float)
        lon = g[cfg.lon_col].astype(float)
        moved = pd.Series(False, index=idx)
        if lat.notna().sum() > 1 and lon.notna().sum() > 1:
            lat_prev = lat.shift(1)
            lon_prev = lon.shift(1)
            dist_m = meter_distance(lat_prev, lon_prev, lat, lon)
            moved = (dist_m > cfg.relocation_distance_m).fillna(False)
        df.loc[idx, "relocated"] = moved
        df.loc[idx, "qa_flag"] = np.where(moved, add_flag(df.loc[idx, "qa_flag"], "RELOCATION"), df.loc[idx, "qa_flag"])
    return df



# ------------------#
# OUTLIER DETECTION #
# ------------------#
def compute_spikes(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    df = df.sort_values([cfg.sensor_id_col, cfg.timestamp_col])
    for sid, g in df.groupby(cfg.sensor_id_col):
        idx = g.index
        val = g[cfg.value_col]
        jump = (val - val.shift(1)).abs()
        prev_jump = jump.shift(1)

        # Detect return-to-original (within tolerance)
        return_to_original = (val == val.shift(2)) | ((val - val.shift(2)).abs() < cfg.return_tolerance)

        # spike_abs = jump > cfg.spike_threshold
        spike_abs = (jump > cfg.spike_threshold) & ~return_to_original # don't flag if the value goes back immediately
        df.loc[idx, "qa_flag"] = np.where(spike_abs, add_flag(df.loc[idx, "qa_flag"], "SPIKE_ABS"), df.loc[idx, "qa_flag"])

        # Local z-score spike on rolling window
        z = (val - val.rolling(24, min_periods=8).mean()) / (val.rolling(24, min_periods=8).std(ddof=1) + 1e-9)# + 1e-9 voorkomt deling door 0
        spike_z_raw = z.abs() > cfg.spike_zscore
        # prev_spike_z = spike_z_raw.shift(1)
        return_to_original_z = (val == val.shift(2)) | ((val - val.shift(2)).abs() < cfg.return_tolerance)
        # spike_z = spike_z_raw & ((prev_spike_z == False) | prev_spike_z.isna())
        spike_z = spike_z_raw & ~return_to_original_z

        # spike_z = z.abs() > cfg.spike_zscore
        df.loc[idx, "qa_flag"] = np.where(spike_z, add_flag(df.loc[idx, "qa_flag"], "SPIKE_Z"), df.loc[idx, "qa_flag"])

        # E: Low variability (sensor stuck) - based on variability of reference station
        ref = g["luchtmeetnet"]
        # Rolling standard deviation for sensor and reference
        roll_std_sensor = val.rolling(24, min_periods=12).std(ddof=1)
        roll_std_ref = ref.rolling(24, min_periods=12).std(ddof=1)

        # Condition: sensor variability is much lower than reference variability
        low_var_lml = (roll_std_sensor < 0.5 * roll_std_ref) & (val.rolling(24, min_periods=12).count() >= 12)
        df.loc[idx, "qa_flag"] = np.where(low_var_lml, add_flag(df.loc[idx, "qa_flag"], "LOW_VARIABILITY_LML"), df.loc[idx, "qa_flag"])

        low_var_const = (roll_std_sensor < cfg.low_var_const) & (val.rolling(24, min_periods=12).count() >= 12)
        df.loc[idx, "low_var"] = low_var_const
        df.loc[idx, "qa_flag"] = np.where(low_var_const, add_flag(df.loc[idx, "qa_flag"], "LOW_VARIABILITY_CONST"), df.loc[idx, "qa_flag"])
    return df

def compute_outliers(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    # logging.info("Outlier detection (STL → Hampel)")
    df = df.copy()
    vcol = cfg.value_col

    # Sort for consistency
    df = df.sort_values([cfg.sensor_id_col, cfg.timestamp_col])

    for sid, g in df.groupby(cfg.sensor_id_col, group_keys=False):
        g = g.sort_values(cfg.timestamp_col)

        # Build time-indexed series
        vals_ts = pd.Series(g[vcol].astype(float).values, index=pd.to_datetime(g[cfg.timestamp_col]))
        vals_ts.name = vcol
        vals_ts = vals_ts.asfreq('h')  # hourly frequency
        # vals_ts = vals_ts.interpolate(method='time')  # or forward fill (if many missing values will still result in NaN)

        vals_ts = (
            vals_ts.interpolate(method='time', limit=24)  # interpolate up to 24 consecutive NaNs
                .ffill(limit=24)                       # forward fill up to 24 hours
                .bfill(limit=24)                       # backward fill up to 24 hours
        )

        # STL decomposition
        period = int(cfg.stl_period_hours)  # e.g., 24 for daily seasonality
        stl = STL(vals_ts, period=period, robust=True)
        res = stl.fit()
        residuals = res.resid

        # Hampel on residuals
        hampel_mask = hampel_filter(residuals, window=cfg.hampel_window, n_sigma=cfg.hampel_n_sigma)

        # STL outlier detection (optional: z-score on residuals)
        stl_mask = (abs((residuals - residuals.mean()) / residuals.std()) > cfg.stl_resid_z)

        # Combine flags
        flags_df = pd.DataFrame({
            "HAMPEL_OUTLIER": hampel_mask,
            "STL_OUTLIER": stl_mask
        }, index=residuals.index)

        # Join back to original
        g_flags = g.join(flags_df, on=cfg.timestamp_col)
        g_flags = g_flags.fillna(False).astype(bool)

        # Apply QA flags
        idx = g_flags.index
        df.loc[idx, "qa_flag"] = np.where(g_flags["HAMPEL_OUTLIER"],
                                          add_flag(df.loc[idx, "qa_flag"], "HAMPEL_OUTLIER"),
                                          df.loc[idx, "qa_flag"])
        df.loc[idx, "qa_flag"] = np.where(g_flags["STL_OUTLIER"],
                                          add_flag(df.loc[idx, "qa_flag"], "STL_OUTLIER"),
                                          df.loc[idx, "qa_flag"])
        # print(sum(g_flags["HAMPEL_OUTLIER"]))
        
    # Save output
    # p = Path(cfg.tmp_dir) / f"outliers_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.parquet"
    # df.to_parquet(p, index=False)
    write_provenance(cfg, "outliers", {"rows": len(df)})
    return df


def step_01_qc_basics(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    logging.info("Step 01: Basic QC & flagging")
    logging.info(f"Total measurements: {len(df)}")
    df = df.copy()
    df["qa_flag"] = 0

    # A: Remove duplicates
    dup_mask = df.duplicated(subset=[cfg.sensor_id_col, cfg.timestamp_col])
    df.loc[dup_mask, "qa_flag"] = add_flag(df.loc[dup_mask, "qa_flag"], "DUPLICATE")
    df = df[~dup_mask].copy()
    logging.info(f'Step 1.A. Removed duplicates if there were any (sensor name + timestamp) : {dup_mask.sum()} ')

    # B: Physical bounds
    v = df[cfg.value_col]
    oor_min = (v < cfg.expected_value_range[0])
    oor_max = (v > cfg.expected_value_range[1])
    df.loc[oor_min, "qa_flag"] = add_flag(df.loc[oor_min, "qa_flag"], "OUT_OF_RANGE_MIN")
    df.loc[oor_max, "qa_flag"] = add_flag(df.loc[oor_max, "qa_flag"], "OUT_OF_RANGE_MAX")
    logging.info(f'Step 1.B. Checking values outside of plausible bounds ({cfg.expected_value_range[0]}, {cfg.expected_value_range[1]}) :')
    logging.info(f' Number of samples < {cfg.expected_value_range[0]} : {oor_min.sum()}')
    logging.info(f' Number of samples > {cfg.expected_value_range[1]} : {oor_max.sum()}')

    # C: Missing values
    logging.info(f' Number of samples before adding row for missing sensor-timestamp combination : {len(df)}')
    df[cfg.timestamp_col] = pd.to_datetime(df[cfg.timestamp_col])
    
    # Unique sensors and full time range -> create full combinatinon of sensors and timestamps
    sensors = df[cfg.sensor_id_col].unique()
    sensor_df = pd.DataFrame({cfg.sensor_id_col: sensors})
    time_df = pd.DataFrame({cfg.timestamp_col: pd.date_range(
        start=pd.Timestamp(cfg.start_date, tz='UTC'),
        end=pd.Timestamp(cfg.end_date, tz='UTC') - pd.Timedelta(hours=1),
        freq='h', tz='UTC'
    )})
    full_df = sensor_df.merge(time_df, how='cross')
    df = full_df.merge(df, on=[cfg.sensor_id_col, cfg.timestamp_col], how='left')

    logging.info(f' Number of samples after adding row for missing sensor-timestamp combination : {len(df)}')
    # df.to_excel('added_timestamps.xlsx', index=Falses)
    # there were NaN values added in qa_flag due to the extension of the data so we
    # need to reset them to 0 (as done earlier when initialising this column)
    df["qa_flag"] = df["qa_flag"].fillna(0).astype(int)

    v = df['pm25_kal_rivm']
    miss = v.isna()
    df.loc[miss, "qa_flag"] = add_flag(df.loc[miss, "qa_flag"], "MISSING")
    logging.info(f'Step 1.C. Number of missing: {miss.sum()}')
    logging.info(f'Step 1.C. New total number of samples after adding rows for missing measurements: {len(df)}')

    # Add flag variable that states if this missing values is part of a larger sequence of mssing values
    
    # --- Add this block below ---
    window = cfg.many_missing_window  # e.g., 24 for 24 hours
    for sid, g in df.groupby(cfg.sensor_id_col):
        idx = g.index
        is_missing = g['pm25_kal_rivm'].isna().astype(int)
        # Rolling window: fraction missing in each window
        frac_missing = is_missing.rolling(window, min_periods=1, center=True).mean()
        # Mask: more than threshold missing in window
        many_missing_mask = frac_missing > cfg.many_missing_frac_threshold
        # Add flag
        df.loc[idx, "qa_flag"] = np.where(
            many_missing_mask,
            add_flag(df.loc[idx, "qa_flag"], "MANY_MISSING_WINDOW"),
            df.loc[idx, "qa_flag"]
        )

    # D: Spikes (absolute jump and z-score)
    df = compute_spikes(df, cfg)
    df = compute_outliers(df, cfg)
    
    logging.info("Step 1.D. Number of spikes and outliers")
    logging.info(f" Number of spikes absolute value: {(df['qa_flag'] & QA_FLAGS['SPIKE_ABS'] > 0).sum()}")
    logging.info(f" Number of spikes z-score: {(df['qa_flag'] & QA_FLAGS['SPIKE_Z'] > 0).sum()}")
    logging.info(f" Number of outliers: {(df['qa_flag'] & QA_FLAGS['HAMPEL_OUTLIER'] > 0).sum()}")

    logging.info("Step 1.E. Number of low variability samples")
    logging.info(f" Number of low variability samples: {df['low_var'].sum()}")

    # F: Relocations
    df = detect_relocations(df, cfg)
    logging.info(f"Step 1.F. Number of relocated samples : {df['relocated'].sum()}")

    # G: Deviant temperature (compared to KNMI)
    if 'temperatuur' in df.columns:
        df['temp_knmi'] = pd.to_numeric(df['temp_knmi'], errors='coerce')
        df['temperatuur'] = pd.to_numeric(df['temperatuur'], errors='coerce')
        df['temp_diff'] = (df['temp_knmi'] - df['temperatuur']).abs()
        df['temp_deviant'] = df['temp_diff'] > cfg.temp_diff_threshold
        df.loc[df['temp_deviant'], "qa_flag"] = add_flag(df.loc[df['temp_deviant'], "qa_flag"], "DEVIANT_TEMP")
        logging.info(f"Step 1.G. Number of strongly deviating temperature values : {df['temp_deviant'].sum()}")
    else:
        logging.info("Step 1.G. Skipped (no sensor temperature column 'temperatuur' available)")

    # H: Deviant humidity (compared to KNMI)
    if 'luchtvochtigheid' in df.columns:
        df['luchtvochtigheid_knmi'] = pd.to_numeric(df['luchtvochtigheid_knmi'], errors='coerce')
        df['luchtvochtigheid'] = pd.to_numeric(df['luchtvochtigheid'], errors='coerce')
        df['luchtvochtigheid_diff'] = (df['luchtvochtigheid_knmi'] - df['luchtvochtigheid']).abs()
        df['luchtvochtigheid_deviant'] = df['luchtvochtigheid_diff'] > cfg.humidity_diff_threshold
        df.loc[df['luchtvochtigheid_deviant'], "qa_flag"] = add_flag(df.loc[df['luchtvochtigheid_deviant'], "qa_flag"], "DEVIANT_HUMIDITY")
        logging.info(f"Step 1.H. Number of strongly deviating humidity values : {df['luchtvochtigheid_deviant'].sum()}")
    else:
        logging.info("Step 1.H. Skipped (no sensor humidity column 'luchtvochtigheid' available)")

    if 'neerslag_uur_knmi' in df.columns:
        df['neerslag_uur_knmi'] = pd.to_numeric(df['neerslag_uur_knmi'], errors='coerce')

    # Save results in both machine-friendly CSV and Excel-friendly XLSX.
    p_csv = Path(cfg.tmp_dir) / f"01_qc_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.csv"
    p_xlsx = Path(cfg.tmp_dir) / f"01_qc_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.xlsx"
    df["decoded_flags"] = decode_qa_flags(df["qa_flag"], QA_FLAGS)
    # print(df.loc[df['qa_flag']>0]

    df.to_csv(p_csv, sep=';', index=False, encoding='utf-8-sig')
    make_excel_safe(df).to_excel(p_xlsx, index=False)
    write_provenance(cfg, "01_qc_basics", {"rows": len(df)})
    return df

# -----------------------------#
# 2) STATS & EXPLORATORY PLOTS #
# -----------------------------#

def plot_sensor_differences_weather(df, cfg):
    # Temperature time series plot
    fig_temp = go.Figure()

    # Plot temperature difference per sensor
    for sensor_id, group in df.groupby('id'):
        fig_temp.add_trace(go.Scatter(
            x=group['time'],
            y=group['temperatuur'],
            mode='lines',
            name=f'Sensor {sensor_id}',
            hovertemplate=(
                f"Sensor ID: {sensor_id}<br>"
                "Timestamp: %{x}<br>"
                "Sensor Temp: %{y}°C<br>"
                "Temp Diff: %{customdata[0]}°C<br>"
                "KNMI Temp: %{customdata[1]}°C<br>"
                "PM2.5 measurement: %{customdata[2]}"
            ),
            customdata=group[['temp_diff', 'temp_knmi', 'pm25_kal_rivm']],
            line=dict(width=1)
        ))

    # Highlight deviant temperature values
    deviant_temp = df[df['temp_diff'].abs() > cfg.temp_diff_threshold]
    fig_temp.add_trace(go.Scatter(
        x=deviant_temp['time'],
        y=deviant_temp['temperatuur'],
        mode='markers',
        marker=dict(color='red', symbol='x', size=8),
        name='Deviant Temperature',
        hovertemplate=(
            f"Sensor ID: {sensor_id}<br>"
            "Deviant Temperature<br>"
            "Timestamp: %{x}<br>"
            "Sensor Temp: %{y}°C<br>"
            "Temp Diff: %{customdata[0]}°C<br>"
            "KNMI Temp: %{customdata[1]}°C<br>"
            "PM2.5 measurement: %{customdata[2]}"
        ),
        customdata=deviant_temp[['temp_diff', 'temp_knmi', 'pm25_kal_rivm']]
    ))

    fig_temp.update_layout(
        title='Temperature Difference Over Time per Sensor',
        xaxis_title='Timestamp',
        yaxis_title='Temperature (°C)',
        legend_title='Sensor ID'
    )
    fig_temp.write_html(Path(cfg.figs_dir) / '02_temperature_timeseries_plot.html')

    # Humidity time series plot
    fig_humidity = go.Figure()

    # Plot humidity difference per sensor
    for sensor_id, group in df.groupby('id'):
        fig_humidity.add_trace(go.Scatter(
            x=group['time'],
            y=group['luchtvochtigheid'],
            mode='lines',
            name=f'Sensor {sensor_id}',
            hovertemplate=(
                f"Sensor ID: {sensor_id}<br>"
                "Timestamp: %{x}<br>"
                "Sensor Humidity: %{y}%<br>"
                "Humidity Diff: %{customdata[0]}%<br>"
                "KNMI Humidity: %{customdata[1]}%<br>"
                "PM2.5 measurement: %{customdata[2]}"
            ),
            customdata=group[['luchtvochtigheid_diff', 'luchtvochtigheid_knmi', 'pm25_kal_rivm']],
            line=dict(width=1)
        ))

    # Highlight deviant humidity values
    deviant_humidity = df[df['luchtvochtigheid_deviant'] == True]
    fig_humidity.add_trace(go.Scatter(
        x=deviant_humidity['time'],
        y=deviant_humidity['luchtvochtigheid'],
        mode='markers',
        marker=dict(color='red', symbol='x', size=8),
        name='Deviant Humidity',
        hovertemplate=(
            "Deviant Humidity<br>"
            "Timestamp: %{x}<br>"
            "Sensor Humidity: %{y}%<br>"
            "Humidity diff: %{customdata[0]}%<br>"
            "KNMI Humidity: %{customdata[1]}%<br>"
            "PM2.5 measurement: %{customdata[2]}"
        ),
        customdata=deviant_humidity[['luchtvochtigheid_diff', 'luchtvochtigheid_knmi', 'pm25_kal_rivm']]
    ))

    fig_humidity.update_layout(
        title='Humidity Difference Over Time per Sensor',
        xaxis_title='Timestamp',
        yaxis_title='Humidity Difference (%)',
        legend_title='Sensor ID'
    )
    fig_humidity.write_html(Path(cfg.figs_dir) / '02_humidity_timeseries_plot.html')
    print("Time series plots with hover info have been saved as HTML files.")

def step_02_stats_eda(df: pd.DataFrame, cfg: Config):
    logging.info("Step 02: Stats & EDA")
    ensure_dirs(cfg)

    # Completeness per sensor
    comp = (
        df.assign(available=~df[cfg.value_col].isna())
          .groupby(cfg.sensor_id_col)
          .agg(n=("available", "size"),
               n_avail=("available","sum"))
          .assign(completeness=lambda x: 100.0 * x["n_avail"] / x["n"])
          .reset_index()
    )
    comp_csv = Path(cfg.output_dir) / "02_completeness_per_sensor.csv"
    comp_xlsx = Path(cfg.output_dir) / "02_completeness_per_sensor.xlsx"
    comp.to_csv(comp_csv, sep=';', index=False, encoding='utf-8-sig')
    make_excel_safe(comp).to_excel(comp_xlsx, index=False)
    logging.info(f"Step 2.A Checking for completeness per sensor. Saving in {comp_csv} and {comp_xlsx}")

    # Missingness heatmap (sensor x time)
    df[cfg.timestamp_col] = pd.to_datetime(df[cfg.timestamp_col], errors="coerce")
    df["date"] = df[cfg.timestamp_col].dt.date
    
    miss = (df[cfg.value_col].isna()).astype(int)
    miss_pivot = miss.groupby([df[cfg.sensor_id_col], df["date"]]).mean().unstack(0)
    fig_miss = px.imshow(
        miss_pivot.T,
        aspect="auto",
        color_continuous_scale="Reds",
        labels=dict(color="Missing fraction"),
        title="Missingness heatmap (daily fraction missing by sensor)"
    )
    save_fig(fig_miss,
             Path(cfg.figs_dir)/"02_missingness_heatmap.html",
             Path(cfg.figs_dir)/"02_missingness_heatmap.png",
             write_png=cfg.write_png)
    logging.info(f" Saving heatmap of missing values per sensor: {Path(cfg.figs_dir)/'02_missingness_heatmap.png'}")
    

    # Distribution & diurnal/weekly cycles
    fig_hist = px.histogram(df, x=cfg.value_col, color=cfg.sensor_id_col, nbins=60, opacity=0.5,
                            title=f"Distribution of {cfg.pollutant.upper()} values")
    save_fig(fig_hist, Path(cfg.figs_dir)/"02_distribution.html", Path(cfg.figs_dir)/"02_distribution.png", cfg.write_png)

    df["hour"] = df[cfg.timestamp_col].dt.hour
    df["dow"] = df[cfg.timestamp_col].dt.day_name()
    diurnal = df.groupby([cfg.sensor_id_col,"hour"])[cfg.value_col].median().reset_index()
    weekly = df.groupby([cfg.sensor_id_col,"dow"])[cfg.value_col].median().reset_index()

    fig_diurnal = px.line(diurnal, x="hour", y=cfg.value_col, color=cfg.sensor_id_col,
                          title="Median diurnal cycle")
    save_fig(fig_diurnal, Path(cfg.figs_dir)/"02_diurnal.html", Path(cfg.figs_dir)/"02_diurnal.png", cfg.write_png)

    cat_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekly["dow"] = pd.Categorical(weekly["dow"], categories=cat_order, ordered=True)
    fig_week = px.line(weekly.sort_values("dow"), x="dow", y=cfg.value_col, color=cfg.sensor_id_col,
                       title="Median weekly cycle")
    save_fig(fig_week, Path(cfg.figs_dir)/"02_weekly.html", Path(cfg.figs_dir)/"02_weekly.png", cfg.write_png)

    if cfg.map_scatter_geo and cfg.lat_col in df and cfg.lon_col in df:
        locs = df.groupby(cfg.sensor_id_col).agg(lat=(cfg.lat_col, "median"), lon=(cfg.lon_col, "median")).reset_index()
        
        # Calculate bounds and center
        lat_min, lat_max = locs['lat'].min(), locs['lat'].max()
        lon_min, lon_max = locs['lon'].min(), locs['lon'].max()
        lat_center = (lat_min + lat_max) / 2
        lon_center = (lon_min + lon_max) / 2

        # Create scatter_mapbox plot
        fig_map = px.scatter_mapbox(
            locs,
            lat='lat',
            lon='lon',
            hover_name=cfg.sensor_id_col,
            title="Sensor locations (median position)",
            zoom=8,
            center={"lat": lat_center, "lon": lon_center}
        )

        # Update layout with mapbox style and margins
        fig_map.update_layout(
            mapbox_style="open-street-map",
            mapbox_zoom=8,
            mapbox_center={"lat": lat_center, "lon": lon_center},
            margin={"r":0, "t":40, "l":0, "b":0}
        )
        fig_map.update_traces(marker=dict(size=8))
        # fig_map = px.scatter_geo(locs, lat='lat', lon='lon', hover_name=cfg.sensor_id_col,
        #                          title="Sensor locations (median position)")
        save_fig(fig_map, Path(cfg.figs_dir)/"02_map.html", Path(cfg.figs_dir)/"02_map.png", cfg.write_png)

    # View the temperature and humidity distribution between KNMI and sensor measurements
    if 'temperatuur' in df.columns and 'temp_diff' in df.columns:
        plot_sensor_differences_weather(df, cfg)

    # # Time series sample
    # to_plot = df["sensor_id"].unique()[:6]
    # fig_ts = go.Figure()
    # for sid in to_plot:
    #     g = df[df["sensor_id"]==sid].sort_values("timestamp")
    #     fig_ts.add_trace(go.Scatter(x=g["timestamp"], y=g[cfg.value_col], mode="lines", name=str(sid)))
    # fig_ts.update_layout(title="Time series (sample sensors)", yaxis_title=f"{cfg.pollutant.upper()} (µg/m³)")
    # save_fig(fig_ts, Path(cfg.figs_dir)/"02_timeseries_sample.html", Path(cfg.figs_dir)/"02_timeseries_sample.png", cfg.write_png)

    write_provenance(cfg, "02_stats_eda", {"n_sensors": int(df[cfg.sensor_id_col].nunique())})


# --------------#
# 3) FILTERING  #
# --------------#

def step_03_filtering(df: pd.DataFrame, cfg: Config, remove_flags: Optional[List[str]]=None) -> pd.DataFrame:
    logging.info("Step 03: Filtering")
    df = df.copy()
    if remove_flags is None:
        # Default: drop impossible/extreme outliers; keep context flags for analysis
        remove_flags = ["OUT_OF_RANGE_MIN", 
                        "OUT_OF_RANGE_MAX",
                        "HAMPEL_OUTLIER", 
                        # "STL_OUTLIER",
                        # "REF_RESID_OUTLIER",
                        # "MISSING",
                        "MANY_MISSING_WINDOW",
                        "LOW_VARIABILITY_CONST",
                        "LOW_VARIABILITY_CONST"]
    remove_mask = np.zeros(len(df), dtype=bool)
    for f in remove_flags:
        remove_mask |= (df["qa_flag"] & QA_FLAGS[f]) > 0
    df["filtered"] = remove_mask
    
    # Create a filtered version of the measurement column
    df["measurement_filtered"] = df["pm25_kal_rivm"].copy()
    df.loc[df["filtered"], "measurement_filtered"] = np.nan

    df.to_parquet(Path(cfg.tmp_dir)/f"03_filtered_{cfg.pollutant}.parquet", index=False)
    write_provenance(cfg, "03_filtering", {"filtered": int(remove_mask.sum())})
    logging.info(f"Step 3. Number of samples that are (set to) NaN : {len(df.loc[df['filtered']==True])}")

    return df

# ---------------#
# 4) IMPUTATION  #
# ---------------#


def mask_gaps(ts: pd.Series, max_gap: int) -> pd.Series:
    is_na = ts.isna()
    # Create a group ID for each block of consecutive NaNs
    group = (~is_na).cumsum()
    gap_lengths = is_na.groupby(group).transform("sum")
    # Mask only short gaps
    return is_na & (gap_lengths <= max_gap)


def impute_series(ts: pd.Series, cfg: Config) -> pd.Series:
    # 1) short gaps: linear (limit = missing_impute_short_max)
    # only fill in the very short gaps
    short_gap_mask = mask_gaps(ts, max_gap=cfg.missing_impute_short_max)
    y = ts.copy()
    imputation_method = pd.Series(np.nan, index=ts.index, dtype="object")  # Start with NaN
    
    y[short_gap_mask] = y.interpolate(cfg.timestamp_col)[short_gap_mask]    
    imputation_method[short_gap_mask] = "short_gap_linear"
    # logging.info(f"Number of short gaps (max length = {cfg.missing_impute_short_max}) : {short_gap_mask.sum()}")

    # 2) longer gaps: hour-of-week climatology 
    # Removed for now because the results show weird patterns in the data when using this
    # longer_gap_mask = mask_gaps(y, max_gap=cfg.missing_impute_long_max)
    # hw = hour_of_week_index(y.index.to_series())
    # # print(f'hw : {hw} ')
    # # print(f'hw : {hw.unique()} ')
    # clim = y.groupby(hw).transform(lambda z: z.mean())
    # # print(f'clim {clim}')
    # y[longer_gap_mask] = y[longer_gap_mask].fillna(clim)
    # imputation_method[longer_gap_mask] = "longer_gap_climatology"
    # logging.info(f"Number of longer gaps (max length = {cfg.missing_impute_long_max}) : {longer_gap_mask.sum()}")

    # 3) STL reconstruction to smooth if needed
    # try:
    #     res = STL(y, period=24, robust=True).fit()
    #     y_rec = res.trend + res.seasonal
    #     y = y.fillna(y_rec)
    # except Exception:
    #     pass
    return y, imputation_method

def step_04_imputation(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    Impute the values in measurement_filtered column
    """
    logging.info("Step 04: Imputation")
    df = df.copy()
    out = []
    vcol = cfg.value_col
    for sid, g in df.groupby(cfg.sensor_id_col):
        g = g.set_index(cfg.timestamp_col).sort_index()
        g[f"{vcol}_imputed"], g[f"{vcol}_imputation_method"] = impute_series(g['measurement_filtered'], cfg)
        out.append(g.reset_index())
    res = pd.concat(out, ignore_index=True)
    res.to_parquet(Path(cfg.tmp_dir)/f"04_imputed_{cfg.pollutant}.parquet", index=False)
    logging.info(f"Step 4. Number imputed values : {len(res.loc[~res[f'{vcol}_imputation_method' ].isna()])}")
    write_provenance(cfg, "04_imputation", {})
    return res

# -------------------------------#
# 5: (OPTIONAL) SENSOR CLUSTERING#
# -------------------------------#

def step_05_clustering(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    Sensor clustering could be in theory used for imputation (step 5)
    """
    logging.info("Step 06: Clustering sensors (correlation-based)")
    pvt = df.pivot_table(index=cfg.timestamp_col, columns=cfg.sensor_id_col, values=cfg.value_col, aggfunc="mean")
    corr = pvt.corr(method="pearson").fillna(0.0)
    Z = linkage(corr.values, method="average")
    labels = fcluster(Z, t=0.7, criterion="distance")
    clusters = pd.DataFrame({cfg.sensor_id_col: corr.index, "cluster": labels})
    clusters_csv = Path(cfg.output_dir) / "06_sensor_clusters.csv"
    clusters_xlsx = Path(cfg.output_dir) / "06_sensor_clusters.xlsx"
    clusters.to_csv(clusters_csv, sep=';', index=False, encoding='utf-8-sig')
    make_excel_safe(clusters).to_excel(clusters_xlsx, index=False)

    fig_corr = px.imshow(corr, color_continuous_scale="RdBu_r", zmin=-1, zmax=1, title="Sensor correlation matrix")
    save_fig(fig_corr, Path(cfg.figs_dir)/"06_corr_matrix.html", Path(cfg.figs_dir)/"06_corr_matrix.png", cfg.write_png)

    write_provenance(cfg, "06_clustering", {"n_clusters": int(len(set(labels)))})
    return clusters

# ----------------#
# 6) EXPORT FINAL #
# ----------------#

def step_06_export(df_raw: pd.DataFrame, df_clean: pd.DataFrame, df_imp: pd.DataFrame, cfg: Config):
    logging.info("Step 06: Export")
    ensure_dirs(cfg)
    clean_fp_csv = Path(cfg.cleaned_dir) / f"clean_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.csv"
    clean_fp_xlsx = Path(cfg.cleaned_dir) / f"clean_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.xlsx"
    df_clean.to_csv(clean_fp_csv, sep=';', index=False, encoding='utf-8-sig')
    make_excel_safe(df_clean).to_excel(clean_fp_xlsx, index=False)

    imp_fp_csv = Path(cfg.cleaned_dir) / f"imputed_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.csv"
    imp_fp_xlsx = Path(cfg.cleaned_dir) / f"imputed_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.xlsx"
    df_imp.to_csv(imp_fp_csv, sep=';', index=False, encoding='utf-8-sig')
    make_excel_safe(df_imp).to_excel(imp_fp_xlsx, index=False)

    idx = Path(cfg.output_dir)/"index.html"
    idx.write_text(textwrap.dedent(f"""
    <html><head><title>Sensor preprocessing report</title></head>
    <body>
    <h1>Sensor preprocessing report — {cfg.pollutant.upper()}</h1>
    <p>Period: {cfg.start_date} to {cfg.end_date}</p>
    <p><em>Note:</em> Input data assumed pre-calibrated; pipeline applies QC/flagging, no corrections.</p>
    <ul>
      <li><a href="FIGS/02_missingness_heatmap.html">Missingness heatmap</a></li>
      <li><a href="FIGS/02_distribution.html">Distribution</a></li>
      <li><a href="FIGS/02_diurnal.html">Diurnal cycle</a></li>
      <li><a href="FIGS/02_weekly.html">Weekly cycle</a></li>
      <li><a href="FIGS/02_timeseries_sample.html">Sample time series</a></li>
      <li><a href="FIGS/02_map.html">Sensor locations</a></li>
      <li><a href="FIGS/06_corr_matrix.html">Sensor correlation matrix</a></li>
    </ul>
    <p>Provenance: <a href="pipeline_provenance.json">pipeline_provenance.json</a></p>
    </body></html>
    """).strip())

    write_provenance(
        cfg,
        "06_export",
        {
            "clean_fp_csv": str(clean_fp_csv),
            "clean_fp_xlsx": str(clean_fp_xlsx),
            "imputed_fp_csv": str(imp_fp_csv),
            "imputed_fp_xlsx": str(imp_fp_xlsx),
        },
    )



# -------------------------#
# MAIN ORCHESTRATION (CLI) #
# -------------------------#
def parse_args():
    ap = argparse.ArgumentParser(description="Low-cost sensor preprocessing pipeline (no calibration).")
    ap.add_argument("--run-all", action="store_true", help="Run all steps 01..08", default=False)
    ap.add_argument("--steps", nargs="+", help="List of steps to run, e.g., 01 02 03")
    ap.add_argument("--write-png", action="store_true", help="Also write PNGs (requires kaleido)")
    return ap.parse_args()

def main():
    args = parse_args()
    cfg = CONFIG
    cfg.write_png = bool(args.write_png)
    ensure_dirs(cfg)

    steps_to_run = []
    if args.run_all:
        # steps_to_run = ["01","02","03","04","05","06","07","08"]
        steps_to_run = ["01", "02", "03", "04", "06"]
    elif args.steps:
        steps_to_run = args.steps
    else:
        print("Nothing to do. Use --run-all or --steps 01 02 ...")
        return
    print(f"Steps to run: {steps_to_run}")

    # Shared frames across steps
    df01 = df02 = df03 = df04 = df05 = df06 = None
    # ref_df = pd.DataFrame()
    # met_df = pd.DataFrame()

    # Ingest
    df = get_sensor_data(cfg)

    # QC
    if "01" in steps_to_run and df is not None:
        df01 = step_01_qc_basics(df, cfg)
    elif df01 is None:
        # Support all historical formats from step 01.
        p_csv = Path(cfg.tmp_dir) / f"01_qc_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.csv"
        p_xlsx = Path(cfg.tmp_dir) / f"01_qc_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.xlsx"
        p_parquet = Path(cfg.tmp_dir) / f"01_qc_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.parquet"
        if p_csv.exists():
            df01 = pd.read_csv(p_csv, sep=';')
        elif p_xlsx.exists():
            df01 = pd.read_excel(p_xlsx)
        elif p_parquet.exists():
            df01 = pd.read_parquet(p_parquet)

    # EDA
    if "02" in steps_to_run and df01 is not None:
        step_02_stats_eda(df01, cfg)

    # # Reference (diagnostic only)
    # if ("03" in steps_to_run or "07" in steps_to_run) and args.with_reference and cfg.ref_api_enabled:
    #     ref_df = fetch_luchtmeetnet_reference(cfg, cfg.start_date, cfg.end_date, cfg.pollutant)
    #     if ref_df.empty:
    #         warnings.warn("No reference data retrieved.")

    # # Meteorology (context only)
    # if ("03" in steps_to_run or "05" in steps_to_run) and cfg.meteo_file and Path(cfg.meteo_file).exists():
    #     met_df = pd.read_csv(cfg.meteo_file, parse_dates=["timestamp"])
    #     if met_df["timestamp"].dt.tz is None:
    #         met_df["timestamp"] = met_df["timestamp"].dt.tz_localize("UTC")

    # Filtering
    df03 = None

    if "03" in steps_to_run and df01 is not None:
        df03 = step_03_filtering(df01, cfg)
    elif df03 is None:
        p = Path(cfg.tmp_dir) / f"03_filtered_{cfg.pollutant}.parquet"
        if p.exists():
            df03 = pd.read_parquet(p)

    # Imputation
    df04 = None
    if "04" in steps_to_run and df03 is not None:
        df04 = step_04_imputation(df03, cfg)
    elif df04 is None:
        p = Path(cfg.tmp_dir)/f"04_imputed_{cfg.pollutant}.parquet"
        if p.exists(): df04 = pd.read_parquet(p)

    # Clustering
    if "05" in steps_to_run and df04 is not None:
        step_05_clustering(df04, cfg)

    # Export
    if "06" in steps_to_run:
        if df is None:
            # p = Path(cfg.tmp_dir)/f"01_ingested_{cfg.pollutant}_{cfg.start_date}_{cfg.end_date}.parquet"
            # df01 = pd.read_parquet(p) if p.exists() else pd.DataFrame()
            df = get_sensor_data(cfg)
        if df03 is None:
            p = Path(cfg.tmp_dir)/f"03_filtered_{cfg.pollutant}.parquet"
            df03 = pd.read_parquet(p) if p.exists() else pd.DataFrame()
        if df04 is None:
            p = Path(cfg.tmp_dir)/f"04_imputed_{cfg.pollutant}.parquet"
            df04 = pd.read_parquet(p) if p.exists() else pd.DataFrame()
        if df04.empty and df03 is not None and not df03.empty:
            logging.warning("Imputed dataframe is empty during export; recomputing from filtered data.")
            df04 = step_04_imputation(df03, cfg)
        step_06_export(df, df03, df04, cfg)

if __name__ == "__main__":
    main()
