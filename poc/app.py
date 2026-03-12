"""
Safe Journeys — Interactive Risk Explorer v2

Predicts:
  1. DSI probability (severity model)
  2. Expected time to next crash per cell (frequency model)
  3. Condition-adjusted real-time risk using current time + live weather
"""
import os
import pickle
import math
import threading
import requests as _requests
from datetime import datetime

import numpy as np
import pandas as pd
import h3
import lightgbm as lgb
from flask import Flask, jsonify, request, render_template

# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Lock for safe in-memory data reload
_data_lock = threading.RLock()


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ---------------------------------------------------------------------------
# Load model & data
# ---------------------------------------------------------------------------
print("Loading model and data...")

with open(os.path.join(ROOT_DIR, "lgb_dsi_model.pkl"), "rb") as f:
    lgb_model = pickle.load(f)
with open(os.path.join(ROOT_DIR, "model_features.pkl"), "rb") as f:
    meta = pickle.load(f)

MODEL_FEATURES = meta["features"]
CAT_COLUMNS = meta["cat_columns"]
CAT_ENCODINGS = meta["cat_encodings"]

# Load data — try database first, fall back to parquet
USE_DB = bool(os.environ.get("DATABASE_URL"))
if USE_DB:
    print("Loading data from database...")
    from poc.db.connection import get_engine
    _db_engine = get_engine()
    df = pd.read_sql("SELECT * FROM crash_records", _db_engine)
    print(f"  Loaded {len(df):,} records from database.")
else:
    df = pd.read_parquet(os.path.join(ROOT_DIR, "cas_features.parquet"))

# Encode categoricals
for col in CAT_COLUMNS:
    if col in df.columns:
        enc = CAT_ENCODINGS.get(col, {})
        df[col] = df[col].astype(str).map(enc).fillna(-1).astype(int)

train = df[df["crashYear"] <= 2021]

# ---------------------------------------------------------------------------
# Crash FREQUENCY model — Poisson rates per cell per condition
# ---------------------------------------------------------------------------
print("Building crash frequency model...")

# Base annual rate per cell
cell_freq = train.groupby("h3_index").agg(
    total_crashes=("OBJECTID", "count"),
    years_span=("crashYear", lambda x: x.max() - x.min() + 1),
    # Condition breakdowns
    rain_crashes=("isRain", "sum"),
    dark_crashes=("isDark", "sum"),
    rain_dark_crashes=("wetAndDark", "sum"),
    fine_day_crashes=("isFine", lambda x: ((x == 1) & (train.loc[x.index, "isDark"] == 0)).sum()),
).reset_index()

cell_freq["annual_rate"] = cell_freq["total_crashes"] / cell_freq["years_span"].clip(lower=1)
cell_freq["hourly_rate"] = cell_freq["annual_rate"] / 8760  # hours per year

# Condition multipliers (relative to overall rate)
# NZ average: ~15% of hours are dark, ~20% have rain
# If X% of crashes happen in rain but rain only occurs Y% of the time,
# the rain multiplier is (X/Y) relative to baseline
total_crashes = len(train)
rain_frac_crashes = train["isRain"].mean()       # fraction of crashes in rain
dark_frac_crashes = train["isDark"].mean()        # fraction of crashes in dark
raindark_frac = train["wetAndDark"].mean()        # fraction in rain+dark

# Approximate exposure fractions for NZ (based on climate data)
RAIN_EXPOSURE = 0.18    # ~18% of hours have rain in NZ on average
DARK_EXPOSURE = 0.38    # ~38% of hours are dark (varies by season/latitude)
RAINDARK_EXPOSURE = RAIN_EXPOSURE * DARK_EXPOSURE  # assuming independence

CONDITION_MULTIPLIERS = {
    "base": 1.0,
    "rain": rain_frac_crashes / RAIN_EXPOSURE if RAIN_EXPOSURE > 0 else 1.0,
    "dark": dark_frac_crashes / DARK_EXPOSURE if DARK_EXPOSURE > 0 else 1.0,
    "rain_dark": raindark_frac / RAINDARK_EXPOSURE if RAINDARK_EXPOSURE > 0 else 1.0,
    "fine_day": (1 - rain_frac_crashes - dark_frac_crashes + raindark_frac) / ((1 - RAIN_EXPOSURE) * (1 - DARK_EXPOSURE)),
}

# Holiday multiplier — holiday periods have elevated crash rates
# CAS holiday periods cover ~30 days/year across all periods
# If X% of crashes happen during holidays but holidays only cover Y% of the year,
# the holiday multiplier is X/Y
HOLIDAY_EXPOSURE = 30 / 365  # ~8.2% of the year is a holiday period
if "isHoliday" in train.columns:
    holiday_frac_crashes = train["isHoliday"].mean()
    CONDITION_MULTIPLIERS["holiday"] = holiday_frac_crashes / HOLIDAY_EXPOSURE if HOLIDAY_EXPOSURE > 0 else 1.0
    CONDITION_MULTIPLIERS["non_holiday"] = (1 - holiday_frac_crashes) / (1 - HOLIDAY_EXPOSURE)
else:
    CONDITION_MULTIPLIERS["holiday"] = 1.0
    CONDITION_MULTIPLIERS["non_holiday"] = 1.0

print(f"  Condition multipliers:")
for k, v in CONDITION_MULTIPLIERS.items():
    print(f"    {k}: {v:.2f}x")

# ---------------------------------------------------------------------------
# Cell history features for severity model
# ---------------------------------------------------------------------------
cell_history = train.groupby("h3_index").agg(
    cell_crash_count=("OBJECTID", "count"),
    cell_years=("crashYear", lambda x: x.max() - x.min() + 1),
    cell_mean_speed=("speedLimit", "mean"),
    cell_pct_rain=("isRain", "mean"),
    cell_pct_dark=("isDark", "mean"),
    cell_pct_intersection=("isIntersection", "mean"),
    cell_pct_urban=("isUrban", "mean"),
    cell_pct_hill=("isHill", "mean"),
    # Counts for mitigation evidence
    n_rain=("isRain", "sum"),
    n_dark=("isDark", "sum"),
    n_twilight=("isTwilight", "sum"),
    n_intersection=("isIntersection", "sum"),
    n_hill=("isHill", "sum"),
    n_wet_dark=("wetAndDark", "sum"),
    n_wet_highspeed=("wetAndHighSpeed", "sum"),
    n_dark_highspeed=("darkAndHighSpeed", "sum"),
    n_poor_vis=("isPoorVisibility", "sum"),
    n_pedestrian=("hasPedestrian", "sum"),
    n_bicycle=("hasBicycle", "sum"),
    n_motorcycle=("hasMotorcycle", "sum"),
    n_vulnerable_dark=("vulnerableAndDark", "sum"),
    n_vulnerable_highspeed=("vulnerableAndHighSpeed", "sum"),
    n_hill_wet=("hillAndWet", "sum"),
    n_has_streetlight=("hasStreetLight", "sum"),
    n_fatal=("fatalCount", "sum"),
    n_serious=("seriousInjuryCount", "sum"),
).reset_index()
cell_history["cell_annual_rate"] = (
    cell_history["cell_crash_count"] / cell_history["cell_years"].clip(lower=1)
)

# Bright sun crashes — light column is encoded, "Bright sun" = 0
BRIGHT_SUN_CODE = CAT_ENCODINGS.get("light", {}).get("Bright sun", 0)
train["_is_bright_sun"] = (train["light"] == BRIGHT_SUN_CODE).astype(int)
_sun = train.groupby("h3_index")["_is_bright_sun"].sum().reset_index()
_sun.columns = ["h3_index", "n_bright_sun"]
cell_history = cell_history.merge(_sun, on="h3_index", how="left")
cell_history["n_bright_sun"] = cell_history["n_bright_sun"].fillna(0).astype(int)
train.drop("_is_bright_sun", axis=1, inplace=True)

# National averages for comparison
NATIONAL_AVG = {
    "pct_dark": float(train["isDark"].mean()),
    "pct_rain": float(train["isRain"].mean()),
    "pct_intersection": float(train["isIntersection"].mean()),
    "pct_hill": float(train["isHill"].mean()),
    "pct_bright_sun": float((train["light"] == BRIGHT_SUN_CODE).mean()),
    "pct_pedestrian": float(train["hasPedestrian"].mean()),
    "pct_bicycle": float(train["hasBicycle"].mean()),
    "pct_motorcycle": float(train["hasMotorcycle"].mean()),
}
print(f"  National averages: { {k: f'{v:.1%}' for k,v in NATIONAL_AVG.items()} }")

r7_history = train.groupby("h3_r7").agg(
    area_crash_count=("OBJECTID", "count"),
    area_years=("crashYear", lambda x: x.max() - x.min() + 1),
).reset_index()
r7_history["area_annual_rate"] = (
    r7_history["area_crash_count"] / r7_history["area_years"].clip(lower=1)
)

# Build per-cell base profiles for severity model
df_feats = [f for f in MODEL_FEATURES if f in df.columns]
cell_profiles = df.groupby("h3_index")[df_feats].median().reset_index()
cell_profiles = cell_profiles.merge(cell_history, on="h3_index", how="left",
                                     suffixes=("_orig", ""))
for c in list(cell_profiles.columns):
    if c.endswith("_orig"):
        cell_profiles.drop(c, axis=1, inplace=True)

cell_to_r7 = df[["h3_index", "h3_r7"]].drop_duplicates("h3_index")
cell_profiles = cell_profiles.merge(cell_to_r7, on="h3_index", how="left")
cell_profiles = cell_profiles.merge(
    r7_history[["h3_r7", "area_crash_count", "area_annual_rate"]],
    on="h3_r7", how="left", suffixes=("_orig", "")
)
for c in list(cell_profiles.columns):
    if c.endswith("_orig"):
        cell_profiles.drop(c, axis=1, inplace=True)

for f in MODEL_FEATURES:
    if f not in cell_profiles.columns:
        cell_profiles[f] = 0

# Merge frequency data and cell_stats
cell_stats = pd.read_parquet(os.path.join(ROOT_DIR, "cas_cell_stats.parquet"))
cell_profiles = cell_profiles.merge(
    cell_stats[["h3_index", "crash_count", "fatal_count", "serious_count",
                "annual_crash_rate", "mean_speed_limit", "cell_lat", "cell_lng"]],
    on="h3_index", how="left"
)
cell_profiles = cell_profiles.merge(
    cell_freq[["h3_index", "hourly_rate", "annual_rate"]],
    on="h3_index", how="left", suffixes=("_cs", "_freq")
)

# Last crash year and most common road per cell (from ALL data, not just train)
print("Computing last crash info per cell...")
_last_crash = df.groupby("h3_index").agg(
    last_crash_year=("crashYear", "max"),
    first_crash_year=("crashYear", "min"),
    total_all_years=("OBJECTID", "count"),
).reset_index()
# Most common road name per cell
_roads = df.groupby("h3_index")["crashLocation1"].agg(
    lambda x: x.value_counts().index[0] if len(x) > 0 else ""
).reset_index()
_roads.columns = ["h3_index", "main_road"]
_last_crash = _last_crash.merge(_roads, on="h3_index", how="left")
cell_profiles = cell_profiles.merge(_last_crash, on="h3_index", how="left")

# Pre-compute baseline severity scores
X_base = cell_profiles[MODEL_FEATURES].fillna(0)
cell_profiles["base_dsi_prob"] = lgb_model.predict(
    X_base, num_iteration=lgb_model.best_iteration
)

# Compute baseline crash density from data (for route risk scoring)
_cell_diameter_km = 0.6  # H3 resolution 8
_baseline_crashes_per_km = (cell_profiles["hourly_rate"].fillna(0).mean() * 8760) / _cell_diameter_km
print(f"Baseline crash density: {_baseline_crashes_per_km:.2f} crashes/km/year (from {len(cell_profiles)} cells)")

# Top N cells by crash count
TOP_N = 5000
top_cells = cell_profiles.nlargest(TOP_N, "crash_count").copy().reset_index(drop=True)

# Pre-build GeoJSON boundaries
print(f"Building GeoJSON for {len(top_cells)} cells...")
cell_boundaries = {}
for _, row in top_cells.iterrows():
    try:
        boundary = h3.cell_to_boundary(row["h3_index"])
        coords = [[lng, lat] for lat, lng in boundary]
        coords.append(coords[0])
        cell_boundaries[row["h3_index"]] = coords
    except Exception:
        pass

# ---------------------------------------------------------------------------
# AADT traffic volume data (loaded in background to not block startup)
# ---------------------------------------------------------------------------
_cell_aadt = {}  # h3_index -> {adt, pct_heavy, road_name, ...}
_aadt_loaded = threading.Event()


def _load_aadt_background():
    """Fetch AADT data from NZTA and map to H3 cells."""
    global _cell_aadt
    try:
        from poc.traffic import fetch_aadt_data, map_aadt_to_h3
        aadt_data = fetch_aadt_data(min_adt=100)
        _cell_aadt.update(map_aadt_to_h3(aadt_data))
        print(f"AADT data loaded: {len(_cell_aadt)} cells with traffic volume data.", flush=True)
    except Exception as e:
        print(f"AADT load failed (non-fatal): {e}", flush=True)
    finally:
        _aadt_loaded.set()


threading.Thread(target=_load_aadt_background, daemon=True).start()

print(f"Ready. {len(top_cells)} cells, {len(MODEL_FEATURES)} features.")
del df, train


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def condition_multiplier(is_rain, is_dark, is_holiday=False):
    """Get crash rate multiplier for current conditions."""
    if is_rain and is_dark:
        mult = CONDITION_MULTIPLIERS["rain_dark"]
    elif is_rain:
        mult = CONDITION_MULTIPLIERS["rain"]
    elif is_dark:
        mult = CONDITION_MULTIPLIERS["dark"]
    else:
        mult = CONDITION_MULTIPLIERS["fine_day"]

    # Apply holiday adjustment
    if is_holiday:
        mult *= CONDITION_MULTIPLIERS["holiday"]
    else:
        mult *= CONDITION_MULTIPLIERS["non_holiday"]
    return mult


def hours_to_human(hours):
    """Convert hours to human-readable string."""
    if hours < 1:
        return f"{hours * 60:.0f}min"
    if hours < 24:
        return f"{hours:.0f}hrs"
    if hours < 24 * 30:
        return f"{hours / 24:.0f}d"
    if hours < 24 * 365:
        return f"{hours / (24 * 30):.0f}mo"
    return f"{hours / (24 * 365):.1f}yr"


def get_mitigations(row):
    """Generate prioritised mitigation suggestions citing actual crash evidence."""
    mitigations = []
    total = int(row.get("cell_crash_count", 0) or row.get("crash_count", 0) or 0)
    if total == 0:
        return []

    speed = row.get("mean_speed_limit", 0) or 0
    pct_dark = row.get("cell_pct_dark", 0) or 0
    pct_rain = row.get("cell_pct_rain", 0) or 0
    pct_intersection = row.get("cell_pct_intersection", 0) or 0
    pct_hill = row.get("cell_pct_hill", 0) or 0
    pct_urban = row.get("cell_pct_urban", 0) or 0

    n_dark = int(row.get("n_dark", 0) or 0)
    n_rain = int(row.get("n_rain", 0) or 0)
    n_intersection = int(row.get("n_intersection", 0) or 0)
    n_hill = int(row.get("n_hill", 0) or 0)
    n_ped = int(row.get("n_pedestrian", 0) or 0)
    n_bike = int(row.get("n_bicycle", 0) or 0)
    n_moto = int(row.get("n_motorcycle", 0) or 0)
    n_wet_dark = int(row.get("n_wet_dark", 0) or 0)
    n_wet_hs = int(row.get("n_wet_highspeed", 0) or 0)
    n_dark_hs = int(row.get("n_dark_highspeed", 0) or 0)
    n_hill_wet = int(row.get("n_hill_wet", 0) or 0)
    n_vuln_dark = int(row.get("n_vulnerable_dark", 0) or 0)
    n_vuln_hs = int(row.get("n_vulnerable_highspeed", 0) or 0)
    n_poor_vis = int(row.get("n_poor_vis", 0) or 0)
    n_bright_sun = int(row.get("n_bright_sun", 0) or 0)
    n_streetlight = int(row.get("n_has_streetlight", 0) or 0)
    n_fatal = int(row.get("n_fatal", 0) or row.get("fatal_count", 0) or 0)
    n_serious = int(row.get("n_serious", 0) or row.get("serious_count", 0) or 0)

    nat = NATIONAL_AVG

    # --- Fatalities: always top priority ---
    if n_fatal >= 2:
        mitigations.append({
            "action": "Install median barriers or wire rope barriers",
            "reason": f"{n_fatal} people killed and {n_serious} seriously injured "
                      f"out of {total:,} crashes — physical separation can prevent head-on collisions",
            "type": "barrier",
            "priority": 1,
        })

    # --- Darkness ---
    if n_dark > 5 and pct_dark > nat["pct_dark"] * 1.2:
        no_light = n_dark - n_streetlight
        reason = (f"{n_dark} out of {total:,} crashes ({pct_dark*100:.0f}%) "
                  f"happened in the dark — well above the national average of {nat['pct_dark']*100:.0f}%")
        if no_light > n_dark * 0.4:
            reason += f". {no_light} of these had no street lighting"
        mitigations.append({
            "action": "Install or upgrade street lighting",
            "reason": reason,
            "type": "lighting",
            "priority": 1 if pct_dark > 0.45 else 2,
        })
    if n_vuln_dark > 3:
        mitigations.append({
            "action": "Add lit pedestrian/cyclist crossings and reflective markings",
            "reason": f"{n_vuln_dark} crashes involved vulnerable road users "
                      f"(pedestrians, cyclists) in the dark",
            "type": "lighting_vuln",
            "priority": 1,
        })

    # --- Bright sun / glare ---
    pct_sun = n_bright_sun / total if total > 0 else 0
    if n_bright_sun > 5 and pct_sun > nat["pct_bright_sun"] * 1.1:
        mitigations.append({
            "action": "Add sun-glare warning signs and reduce speed during sunrise/sunset",
            "reason": f"{n_bright_sun} out of {total:,} crashes ({pct_sun*100:.0f}%) "
                      f"occurred in bright sun conditions — {pct_sun/nat['pct_bright_sun']:.1f}x "
                      f"the national average, suggesting sun strike is a factor",
            "type": "sun_glare",
            "priority": 2,
        })

    # --- Rain / wet ---
    if n_rain > 5 and pct_rain > nat["pct_rain"] * 1.2:
        reason = (f"{n_rain} out of {total:,} crashes ({pct_rain*100:.0f}%) "
                  f"happened in rain — {pct_rain/nat['pct_rain']:.1f}x the national average of {nat['pct_rain']*100:.0f}%")
        if n_wet_hs > 3:
            reason += f". {n_wet_hs} of these were at high speed (80+ km/h) in wet conditions"
        mitigations.append({
            "action": "Improve road drainage and high-friction surface treatment",
            "reason": reason,
            "type": "weather",
            "priority": 1 if pct_rain > 0.3 else 2,
        })

    # --- Wet + dark compound ---
    if n_wet_dark > 5:
        mitigations.append({
            "action": "Add reflective road markings and advisory speed signs for wet nights",
            "reason": f"{n_wet_dark} crashes occurred in combined rain and darkness — "
                      f"drivers lose both grip and visibility simultaneously",
            "type": "wet_dark",
            "priority": 1,
        })

    # --- Poor visibility ---
    if n_poor_vis > 3 and (n_poor_vis / total) > 0.05:
        mitigations.append({
            "action": "Install fog warning signs and reduce speed in low-visibility zone",
            "reason": f"{n_poor_vis} out of {total:,} crashes ({n_poor_vis/total*100:.0f}%) "
                      f"occurred in poor visibility (fog, mist, heavy rain)",
            "type": "visibility",
            "priority": 2,
        })

    # --- Speed ---
    if speed >= 80:
        reason = f"Average speed limit is {speed:.0f} km/h"
        if n_fatal + n_serious > 5:
            reason += (f" and {n_fatal + n_serious} crashes resulted in death or "
                       f"serious injury — impact energy doubles between 80 and 100 km/h")
        if n_wet_hs > 3:
            reason += f". {n_wet_hs} high-speed crashes happened in wet conditions"
        mitigations.append({
            "action": "Reduce speed limit or add variable speed signs",
            "reason": reason,
            "type": "speed",
            "priority": 1 if n_fatal > 0 else 2,
        })
    if speed >= 60 and pct_urban > 0.5:
        mitigations.append({
            "action": "Implement 40-50 km/h urban speed zone",
            "reason": f"Urban area with {speed:.0f} km/h speed limit — "
                      f"{n_ped + n_bike} crashes involved pedestrians or cyclists "
                      f"who are highly vulnerable above 40 km/h",
            "type": "speed_urban",
            "priority": 1 if n_ped > 0 else 2,
        })

    # --- Intersections ---
    if n_intersection > 5 and pct_intersection > nat["pct_intersection"] * 1.2:
        mitigations.append({
            "action": "Upgrade to roundabout or add traffic signals",
            "reason": f"{n_intersection} out of {total:,} crashes ({pct_intersection*100:.0f}%) "
                      f"occurred at intersections — "
                      f"{pct_intersection/nat['pct_intersection']:.1f}x the national average. "
                      f"Roundabouts reduce injury crashes by 75%",
            "type": "intersection",
            "priority": 1,
        })

    # --- Pedestrians ---
    pct_ped = n_ped / total if total > 0 else 0
    if n_ped > 3 and pct_ped > nat["pct_pedestrian"] * 1.2:
        mitigations.append({
            "action": "Add signalised pedestrian crossings and speed tables",
            "reason": f"{n_ped} out of {total:,} crashes ({pct_ped*100:.0f}%) "
                      f"involved pedestrians — "
                      f"{pct_ped/nat['pct_pedestrian']:.1f}x the national average. "
                      f"A pedestrian hit at 50 km/h has a 45% chance of dying vs 5% at 30 km/h",
            "type": "pedestrian",
            "priority": 1,
        })

    # --- Cyclists ---
    pct_bike = n_bike / total if total > 0 else 0
    if n_bike > 3 and pct_bike > nat["pct_bicycle"] * 1.2:
        reason = (f"{n_bike} out of {total:,} crashes ({pct_bike*100:.0f}%) "
                  f"involved cyclists — {pct_bike/nat['pct_bicycle']:.1f}x the national average")
        if speed >= 60:
            reason += f". At {speed:.0f} km/h, cyclists need physical separation from traffic"
        mitigations.append({
            "action": "Install separated cycle lanes or shared path",
            "reason": reason,
            "type": "cyclist",
            "priority": 1,
        })

    # --- Motorcyclists ---
    pct_moto = n_moto / total if total > 0 else 0
    if n_moto > 3 and pct_moto > nat["pct_motorcycle"] * 1.2:
        mitigations.append({
            "action": "Install motorcycle-friendly barriers and improve surface grip",
            "reason": f"{n_moto} out of {total:,} crashes ({pct_moto*100:.0f}%) "
                      f"involved motorcycles — "
                      f"{pct_moto/nat['pct_motorcycle']:.1f}x the national average. "
                      f"Wire rope barriers can be fatal for motorcyclists; use post-and-rail",
            "type": "motorcycle",
            "priority": 2,
        })

    # --- Hill + wet ---
    if n_hill > 5 and pct_hill > nat["pct_hill"] * 1.2:
        reason = (f"{n_hill} out of {total:,} crashes ({pct_hill*100:.0f}%) "
                  f"occurred on hill roads — {pct_hill/nat['pct_hill']:.1f}x the national average")
        if n_hill_wet > 3:
            reason += f". {n_hill_wet} of these were in wet conditions — gradient + rain reduces stopping distance"
        mitigations.append({
            "action": "Add curve advisory signs, rumble strips, and anti-skid surface",
            "reason": reason,
            "type": "hill",
            "priority": 1 if n_hill_wet > 3 else 2,
        })

    # Sort by priority, deduplicate by type
    seen_types = set()
    result = []
    for m in sorted(mitigations, key=lambda x: x["priority"]):
        key = m["type"]
        if key not in seen_types:
            seen_types.add(key)
            result.append(m)

    return result[:5]


def cell_risk_profile(row):
    """Extract risk profile characteristics for a cell."""
    return {
        "pct_dark": round(float(row.get("cell_pct_dark", 0) or 0) * 100),
        "pct_rain": round(float(row.get("cell_pct_rain", 0) or 0) * 100),
        "pct_intersection": round(float(row.get("cell_pct_intersection", 0) or 0) * 100),
        "pct_urban": round(float(row.get("cell_pct_urban", 0) or 0) * 100),
        "pct_hill": round(float(row.get("cell_pct_hill", 0) or 0) * 100),
    }


def get_risk_factors(row):
    """Generate plain-language explanations of why the model predicts this risk level."""
    factors = []
    total = int(row.get("cell_crash_count", 0) or row.get("crash_count", 0) or 0)
    if total == 0:
        return factors

    nat = NATIONAL_AVG
    speed = row.get("mean_speed_limit", 0) or 0
    dsi_prob = row.get("base_dsi_prob", 0) or 0
    annual_rate = row.get("annual_crash_rate", 0) or 0
    pct_dark = row.get("cell_pct_dark", 0) or 0
    pct_rain = row.get("cell_pct_rain", 0) or 0
    pct_hill = row.get("cell_pct_hill", 0) or 0
    pct_urban = row.get("cell_pct_urban", 0) or 0
    n_fatal = int(row.get("n_fatal", 0) or row.get("fatal_count", 0) or 0)
    n_serious = int(row.get("n_serious", 0) or row.get("serious_count", 0) or 0)
    n_ped = int(row.get("n_pedestrian", 0) or 0)
    n_bike = int(row.get("n_bicycle", 0) or 0)
    n_moto = int(row.get("n_motorcycle", 0) or 0)
    n_vuln = n_ped + n_bike + n_moto
    n_wet_dark = int(row.get("n_wet_dark", 0) or 0)
    n_dark = int(row.get("n_dark", 0) or 0)
    n_rain = int(row.get("n_rain", 0) or 0)

    # --- Frequency explanation ---
    if annual_rate >= 50:
        factors.append(
            f"Extremely high crash volume — {annual_rate:.0f} crashes per year "
            f"({total:,} total). This is one of the most crash-prone locations in NZ."
        )
    elif annual_rate >= 10:
        factors.append(
            f"High crash volume — averaging {annual_rate:.0f} crashes per year ({total:,} total)."
        )
    elif total >= 50:
        factors.append(
            f"{total:,} crashes recorded at this location over the data period."
        )

    # --- Severity explanation ---
    if n_fatal >= 1:
        factors.append(
            f"{n_fatal} {'person has' if n_fatal == 1 else 'people have'} been killed "
            f"and {n_serious} seriously injured here. "
            f"The model weighs fatal history heavily when predicting future severity."
        )
    elif n_serious >= 5:
        factors.append(
            f"{n_serious} people have been seriously injured here. "
            f"Locations with repeated serious injuries are strong predictors of future DSI crashes."
        )

    # --- Vulnerable road users (the #1 model feature) ---
    if n_vuln > 0:
        pct_vuln = n_vuln / total * 100
        parts = []
        if n_ped > 0:
            parts.append(f"{n_ped} pedestrian")
        if n_bike > 0:
            parts.append(f"{n_bike} cyclist")
        if n_moto > 0:
            parts.append(f"{n_moto} motorcycle")
        vuln_desc = ", ".join(parts)
        factors.append(
            f"Vulnerable road users involved in {pct_vuln:.0f}% of crashes ({vuln_desc}). "
            f"This is the single strongest predictor of severity — "
            f"motorcyclists face a 32% DSI rate vs 5% for car occupants."
        )

    # --- Speed ---
    if speed >= 90:
        factors.append(
            f"High speed environment (avg {speed:.0f} km/h). "
            f"Crashes at 100 km/h have an 11% DSI rate vs 4.5% at 50 km/h — "
            f"impact energy quadruples when speed doubles."
        )
    elif speed >= 70:
        factors.append(
            f"Moderate-high speed environment (avg {speed:.0f} km/h). "
            f"Speed is the 12th most important predictor in the model."
        )

    # --- Urban/rural context ---
    if pct_urban < 0.3 and speed >= 80:
        factors.append(
            "Rural high-speed location. Rural roads have double the DSI rate (10.4%) "
            "of urban roads (4.8%) — fewer safety features, longer emergency response times."
        )
    elif pct_urban > 0.7:
        factors.append(
            "Urban area — more crashes but generally lower severity. "
            "Risk increases when pedestrians or cyclists are present."
        )

    # --- Darkness ---
    if pct_dark > nat["pct_dark"] * 1.3 and n_dark > 5:
        ratio = pct_dark / nat["pct_dark"]
        factors.append(
            f"{pct_dark*100:.0f}% of crashes here happen in darkness "
            f"({ratio:.1f}x the national average of {nat['pct_dark']*100:.0f}%). "
            f"Dark conditions increase DSI rate from 6.4% to 7.4%."
        )

    # --- Rain ---
    if pct_rain > nat["pct_rain"] * 1.3 and n_rain > 5:
        ratio = pct_rain / nat["pct_rain"]
        factors.append(
            f"{pct_rain*100:.0f}% of crashes here happen in rain "
            f"({ratio:.1f}x the national average of {nat['pct_rain']*100:.0f}%). "
            f"Wet roads increase stopping distances by up to 2x."
        )

    # --- Combined wet + dark ---
    if n_wet_dark > 5:
        factors.append(
            f"{n_wet_dark} crashes occurred in combined rain and darkness — "
            f"drivers lose both grip and visibility simultaneously."
        )

    # --- Hill ---
    if pct_hill > nat["pct_hill"] * 1.3:
        factors.append(
            f"{pct_hill*100:.0f}% of crashes here are on hill roads "
            f"(national average: {nat['pct_hill']*100:.0f}%). "
            f"Gradients reduce braking effectiveness, especially in wet conditions."
        )

    return factors[:6]  # cap at 6 factors


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/cells")
def get_cells():
    """Return GeoJSON with baseline scores + frequency data."""
    features = []
    for _, row in top_cells.iterrows():
        h3id = row["h3_index"]
        if h3id not in cell_boundaries:
            continue

        hourly = row.get("hourly_rate", 0) or 0
        etna = (1 / hourly) if hourly > 0 else 999999  # expected time to next accident

        mits = get_mitigations(row)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [cell_boundaries[h3id]]},
            "properties": {
                "h3": h3id,
                "dsi_pct": round(row["base_dsi_prob"] * 100, 1),
                "crashes": int(row.get("crash_count", 0)),
                "fatal": int(row.get("fatal_count", 0)),
                "serious": int(row.get("serious_count", 0)),
                "annual_rate": round(row.get("annual_crash_rate", 0), 1),
                "speed_limit": round(row.get("mean_speed_limit", 0)),
                "hourly_rate": round(hourly, 6),
                "etna_hours": round(etna, 1),
                "etna_label": hours_to_human(etna),
                "lat": round(row.get("cell_lat", 0), 4),
                "lng": round(row.get("cell_lng", 0), 4),
                "last_crash_year": int(row.get("last_crash_year", 0) or 0),
                "first_crash_year": int(row.get("first_crash_year", 0) or 0),
                "main_road": str(row.get("main_road", "") or ""),
                "risk_profile": cell_risk_profile(row),
                "risk_factors": get_risk_factors(row),
                "mitigations": [{"action": m["action"], "reason": m["reason"]} for m in mits],
            },
        })

        # Add AADT data if available
        aadt_info = _cell_aadt.get(h3id)
        if aadt_info:
            props = features[-1]["properties"]
            props["adt"] = aadt_info["adt"]
            props["pct_heavy"] = round(aadt_info.get("pct_heavy", 0), 1)
            from poc.traffic import compute_exposure_rate, classify_exposure_risk
            crash_count = int(row.get("crash_count", 0))
            years = int(row.get("cell_years", 1) or 1)
            exp_rate = compute_exposure_rate(crash_count, years, aadt_info["adt"])
            props["crash_rate_per_100m_vkm"] = exp_rate
            props["exposure_risk"] = classify_exposure_risk(exp_rate)

    return jsonify({"type": "FeatureCollection", "features": features})


@app.route("/api/score", methods=["POST"])
def score():
    """Re-score all cells with modified conditions — severity + frequency."""
    params = request.json or {}

    # --- Severity model ---
    X = top_cells[MODEL_FEATURES].fillna(0).copy()
    overrides = {}

    weather = params.get("weather", "any")
    light = params.get("light", "any")

    # Auto-detect from live weather data
    live_conditions = None
    if weather == "auto" or light == "auto":
        try:
            from poc.weather import get_current_conditions
            live_conditions = get_current_conditions()
        except Exception:
            live_conditions = None

    if weather == "auto" and live_conditions:
        is_rain = live_conditions["is_rain"]
        if is_rain:
            overrides.update({"isRain": 1, "isPoorVisibility": 1 if live_conditions.get("poor_visibility") else 0, "isFine": 0, "weatherCode": 1})
        else:
            overrides.update({"isRain": 0, "isPoorVisibility": 0, "isFine": 1, "weatherCode": 0})
    elif weather == "rain":
        is_rain = True
        overrides.update({"isRain": 1, "isPoorVisibility": 1, "isFine": 0, "weatherCode": 1})
    elif weather == "fine":
        is_rain = False
        overrides.update({"isRain": 0, "isPoorVisibility": 0, "isFine": 1, "weatherCode": 0})
    else:
        is_rain = False

    if light == "auto" and live_conditions:
        detected_light = live_conditions["light"]
        is_dark = detected_light == "dark"
        if detected_light == "dark":
            overrides.update({"isDark": 1, "isTwilight": 0, "lightCode": 3})
        elif detected_light == "twilight":
            overrides.update({"isDark": 0, "isTwilight": 1, "lightCode": 2})
        else:
            overrides.update({"isDark": 0, "isTwilight": 0, "lightCode": 0})
    elif light == "dark":
        is_dark = True
        overrides.update({"isDark": 1, "isTwilight": 0, "lightCode": 3})
    elif light == "twilight":
        is_dark = False
        overrides.update({"isDark": 0, "isTwilight": 1, "lightCode": 2})
    elif light == "day":
        is_dark = False
        overrides.update({"isDark": 0, "isTwilight": 0, "lightCode": 0})
    else:
        is_dark = False

    speed = params.get("speed_limit")
    if speed and speed != "any":
        overrides["speedLimit"] = int(speed)

    vehicle = params.get("vehicle", "any")
    if vehicle == "motorcycle":
        overrides.update({"hasMotorcycle": 1, "motorcycle": 1, "vulnerableUser": 1})
    elif vehicle == "pedestrian":
        overrides.update({"hasPedestrian": 1, "vulnerableUser": 1})
    elif vehicle == "bicycle":
        overrides.update({"hasBicycle": 1, "bicycle": 1, "vulnerableUser": 1})
    elif vehicle == "truck":
        overrides.update({"hasTruck": 1, "truck": 1})

    road = params.get("road", "any")
    if road == "intersection":
        overrides.update({"isIntersection": 1, "hasTrafficControl": 1})
    elif road == "hill":
        overrides.update({"isHill": 1})
    elif road == "straight":
        overrides.update({"isIntersection": 0, "isHill": 0})

    for col, val in overrides.items():
        if col in X.columns:
            X[col] = val

    # Recompute compound features
    rain_col = X["isRain"] if "isRain" in X.columns else 0
    dark_col = X["isDark"] if "isDark" in X.columns else 0
    speed_col = X["speedLimit"] if "speedLimit" in X.columns else 50
    vuln_col = X["vulnerableUser"] if "vulnerableUser" in X.columns else 0

    for feat, expr in [
        ("wetAndDark", lambda: (rain_col.astype(bool) & dark_col.astype(bool)).astype(int)),
        ("wetAndHighSpeed", lambda: (rain_col.astype(bool) & (speed_col >= 80)).astype(int)),
        ("darkAndHighSpeed", lambda: (dark_col.astype(bool) & (speed_col >= 80)).astype(int)),
        ("wetDarkHighSpeed", lambda: (rain_col.astype(bool) & dark_col.astype(bool) & (speed_col >= 80)).astype(int)),
        ("hillAndWet", lambda: ((X["isHill"] if "isHill" in X.columns else 0).astype(bool) & rain_col.astype(bool)).astype(int)),
        ("vulnerableAndDark", lambda: (vuln_col.astype(bool) & dark_col.astype(bool)).astype(int)),
        ("vulnerableAndHighSpeed", lambda: (vuln_col.astype(bool) & (speed_col >= 70)).astype(int)),
    ]:
        if feat in X.columns:
            X[feat] = expr()

    severity_probs = lgb_model.predict(X, num_iteration=lgb_model.best_iteration)

    # --- Holiday detection ---
    is_holiday = False
    holiday_info = None
    if weather == "auto" or light == "auto":
        try:
            from poc.weather import get_current_holiday_info
            holiday_info = get_current_holiday_info()
            is_holiday = holiday_info.get("is_holiday", False)
        except Exception:
            pass

    # --- Frequency model: per-cell condition multiplier ---
    use_per_cell_weather = (weather == "auto" or light == "auto")
    region_conditions = {}
    if use_per_cell_weather:
        try:
            from poc.weather import get_conditions_per_region, nearest_weather_point
            region_conditions = get_conditions_per_region()
        except Exception:
            region_conditions = {}

    # Fallback single multiplier (used when not auto, or if per-cell fails)
    fallback_mult = condition_multiplier(
        is_rain=is_rain or (weather == "any" and False),
        is_dark=is_dark or (light == "any" and False),
        is_holiday=is_holiday,
    )

    def cell_multiplier(lat, lng):
        """Get per-cell condition multiplier based on nearest weather station."""
        if not use_per_cell_weather or not region_conditions:
            return fallback_mult
        try:
            region_name, _ = nearest_weather_point(lat, lng)
            rc = region_conditions.get(region_name)
            if rc is None:
                return fallback_mult
            cell_rain = rc["is_rain"] if weather == "auto" else is_rain
            cell_dark = rc["is_dark"] if light == "auto" else is_dark
            return condition_multiplier(cell_rain, cell_dark, is_holiday)
        except Exception:
            return fallback_mult

    result = {}
    cell_mults = []
    for i, (_, row) in enumerate(top_cells.iterrows()):
        h3id = row["h3_index"]
        if h3id not in cell_boundaries:
            cell_mults.append(fallback_mult)
            continue

        lat = row.get("cell_lat", 0) or 0
        lng = row.get("cell_lng", 0) or 0
        mult = cell_multiplier(lat, lng)
        cell_mults.append(mult)

        base_hourly = row.get("hourly_rate", 0) or 0
        adj_hourly = base_hourly * mult
        etna = (1 / adj_hourly) if adj_hourly > 0 else 999999

        result[h3id] = {
            "dsi_pct": round(float(severity_probs[i]) * 100, 1),
            "etna_hours": round(etna, 1),
            "etna_label": hours_to_human(etna),
            "hourly_rate": round(adj_hourly, 6),
        }

    # Stats
    dsi_arr = severity_probs
    etna_arr = []
    for i, (_, row) in enumerate(top_cells.iterrows()):
        m = cell_mults[i] if i < len(cell_mults) else fallback_mult
        hr = (row.get("hourly_rate", 0) or 0) * m
        etna_arr.append((1 / hr) if hr > 0 else 999999)
    etna_arr = np.array(etna_arr)

    # Highest risk = shortest time to next crash
    top50_indices = np.argsort(etna_arr)[:50]
    hotspots = []
    for idx in top50_indices:
        row = top_cells.iloc[idx]
        mits = get_mitigations(row)
        hs = {
            "h3": row["h3_index"],
            "etna_label": hours_to_human(etna_arr[idx]),
            "etna_hours": round(float(etna_arr[idx]), 1),
            "dsi_pct": round(float(severity_probs[idx]) * 100, 1),
            "crashes": int(row.get("crash_count", 0)),
            "lat": round(row.get("cell_lat", 0), 4),
            "lng": round(row.get("cell_lng", 0), 4),
            "speed_limit": round(row.get("mean_speed_limit", 0)),
            "last_crash_year": int(row.get("last_crash_year", 0) or 0),
            "main_road": str(row.get("main_road", "") or ""),
            "risk_factors": get_risk_factors(row),
            "mitigations": [{"action": m["action"], "reason": m["reason"]} for m in mits],
        }
        aadt_info = _cell_aadt.get(row["h3_index"])
        if aadt_info:
            hs["adt"] = aadt_info["adt"]
            hs["pct_heavy"] = round(aadt_info.get("pct_heavy", 0), 1)
        hotspots.append(hs)

    stats = {
        "mean_dsi": round(float(np.mean(dsi_arr)) * 100, 1),
        "max_dsi": round(float(np.max(dsi_arr)) * 100, 1),
        "high_risk_count": int((dsi_arr >= 0.15).sum()),
        "elevated_count": int(((dsi_arr >= 0.08) & (dsi_arr < 0.15)).sum()),
        "cells_scored": len(dsi_arr),
        "condition_multiplier": round(float(np.median(cell_mults)), 2),
        "shortest_etna": hours_to_human(float(np.min(etna_arr))),
        "median_etna": hours_to_human(float(np.median(etna_arr))),
        "hotspots": hotspots,
    }
    if holiday_info:
        stats["holiday"] = holiday_info

    # Include live conditions in response if auto-detected
    if live_conditions:
        from poc.weather import get_risk_description
        stats["live_weather"] = {
            "weather_description": live_conditions.get("weather_description", ""),
            "light": live_conditions.get("light", ""),
            "rain_mm": live_conditions.get("rain_mm", 0),
            "temp_c": live_conditions.get("temp_c"),
            "wind_kmh": live_conditions.get("wind_kmh"),
            "ice_risk": live_conditions.get("ice_risk", False),
            "high_wind": live_conditions.get("high_wind", False),
            "risk_descriptions": get_risk_description(live_conditions),
        }

    return jsonify({"scores": result, "stats": stats})


@app.route("/api/route", methods=["POST"])
def route_risk():
    """Score a driving route for crash risk."""
    params = request.json or {}
    origin_lat = params.get("origin_lat")
    origin_lng = params.get("origin_lng")
    dest_lat = params.get("dest_lat")
    dest_lng = params.get("dest_lng")

    if not all([origin_lat, origin_lng, dest_lat, dest_lng]):
        return jsonify({"error": "origin_lat, origin_lng, dest_lat, dest_lng required"}), 400

    from poc.routing import get_route, route_to_h3_cells, score_route

    # Get driving route
    route = get_route(origin_lng, origin_lat, dest_lng, dest_lat)
    if "error" in route:
        return jsonify(route), 400

    # Map route to H3 cells
    cells = route_to_h3_cells(route["coordinates"], resolution=8)

    # Build cell data lookup from ALL cell_profiles (not just top_cells)
    # Routes pass through many cells that may not be in the top 5000
    cell_lookup = {}
    for _, row in cell_profiles.iterrows():
        cell_lookup[row["h3_index"]] = row.to_dict()

    # Per-cell weather multiplier
    is_holiday = False
    try:
        from poc.weather import get_current_holiday_info, get_conditions_per_region, nearest_weather_point
        holiday_info = get_current_holiday_info()
        is_holiday = holiday_info.get("is_holiday", False)
        region_conditions = get_conditions_per_region()
    except Exception:
        region_conditions = {}

    def cell_mult_fn(lat, lng):
        try:
            region_name, _ = nearest_weather_point(lat, lng)
            rc = region_conditions.get(region_name)
            if rc:
                return condition_multiplier(rc["is_rain"], rc["is_dark"], is_holiday)
        except Exception:
            pass
        return condition_multiplier(False, False, is_holiday)

    # Score the route
    result = score_route(cells, cell_lookup, cell_mult_fn, aadt_data=_cell_aadt, baseline_crashes_per_km=_baseline_crashes_per_km)
    result["route_coordinates"] = route["coordinates"]
    result["distance_km"] = round(route["distance_m"] / 1000, 1)
    result["duration_min"] = round(route["duration_s"] / 60)

    return jsonify(result)


@app.route("/api/weather")
def weather_endpoint():
    """Return current weather conditions + 24hr forecast + holiday info.
    Optional: ?lat=...&lng=... for location-specific weather.
    """
    try:
        from poc.weather import get_current_conditions, get_conditions_for_location, get_risk_description, get_current_holiday_info
        lat = request.args.get("lat", type=float)
        lng = request.args.get("lng", type=float)
        if lat is not None and lng is not None:
            conditions = get_conditions_for_location(lat, lng)
        else:
            conditions = get_current_conditions()
        conditions["risk_descriptions"] = get_risk_description(conditions)
        conditions["holiday"] = get_current_holiday_info()
        return jsonify(conditions)
    except Exception as e:
        return jsonify({"error": str(e), "weather_description": "Unavailable"}), 500


@app.route("/api/traffic")
def traffic_endpoint():
    """Return AADT traffic volume data mapped to H3 cells."""
    if not _aadt_loaded.is_set():
        return jsonify({"status": "loading", "message": "Traffic data is still loading..."}), 202
    return jsonify({
        "cells_with_adt": len(_cell_aadt),
        "data": {h3id: info for h3id, info in _cell_aadt.items()},
    })


@app.route("/api/stats/yearly")
def yearly_stats():
    """Return crash counts by year, with severity breakdown."""
    if USE_DB:
        from sqlalchemy import text
        with _db_engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT "crashYear" AS year,
                       COUNT(*) AS total,
                       SUM("fatalCount") AS fatal,
                       SUM("seriousInjuryCount") AS serious,
                       SUM("minorInjuryCount") AS minor
                FROM crash_records
                GROUP BY "crashYear"
                ORDER BY "crashYear"
            """)).fetchall()
        data = [{"year": r[0], "total": r[1], "fatal": int(r[2] or 0),
                 "serious": int(r[3] or 0), "minor": int(r[4] or 0)} for r in rows]
    else:
        # Fallback for parquet mode — use top_cells parent df isn't available
        data = []
    return jsonify(data)


@app.route("/api/geocode")
def geocode():
    """Reverse geocode lat/lng via Nominatim (proxied to avoid CORS)."""
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    if not lat or not lng:
        return jsonify({"address": ""})
    try:
        resp = _requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lng, "format": "json", "zoom": 16, "addressdetails": 1},
            headers={"User-Agent": "SafeJourneys/1.0 (crash-risk-explorer)"},
            timeout=5,
        )
        data = resp.json()
        a = data.get("address", {})
        road = a.get("road") or a.get("pedestrian") or a.get("cycleway") or ""
        suburb = a.get("suburb") or a.get("neighbourhood") or a.get("hamlet") or ""
        city = a.get("city") or a.get("town") or a.get("village") or ""
        parts = [p for p in [road, suburb, city] if p]
        return jsonify({"address": ", ".join(parts) if parts else data.get("display_name", "")[:80]})
    except Exception:
        return jsonify({"address": ""})


# ---------------------------------------------------------------------------
# Scheduled data refresh (only when running with database backend)
# ---------------------------------------------------------------------------
if USE_DB:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        def scheduled_refresh():
            """Fetch new data from CAS API, update DB, reload in-memory data."""
            try:
                from poc.db.ingest import run_refresh
                print(f"[{datetime.utcnow().isoformat()}] Starting scheduled data refresh...")
                new_count = run_refresh(_db_engine)
                if new_count > 0:
                    print(f"  {new_count} new records — reloading in-memory data...")
                    # Reload data from DB
                    new_df = pd.read_sql("SELECT * FROM crash_records", _db_engine)
                    # Re-encode categoricals
                    for col in CAT_COLUMNS:
                        if col in new_df.columns:
                            enc = CAT_ENCODINGS.get(col, {})
                            new_df[col] = new_df[col].astype(str).map(enc).fillna(-1).astype(int)
                    # NOTE: Full reload of frequency/cell models would go here
                    # For now, the app needs a restart for full model refresh
                    print(f"  Reload complete. {len(new_df):,} total records.")
                else:
                    print("  No new records.")
            except Exception as e:
                print(f"  Refresh error: {e}")

        refresh_hours = int(os.environ.get("REFRESH_INTERVAL_HOURS", "4"))
        scheduler = BackgroundScheduler()
        from datetime import datetime, timedelta

        # Check if a refresh ran recently (within the interval window)
        run_now = None  # default: wait for first interval
        try:
            from sqlalchemy import text
            with _db_engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT finished_at FROM data_refresh_log "
                    "WHERE status = 'success' ORDER BY finished_at DESC LIMIT 1"
                )).fetchone()
                if row is None or row[0] is None:
                    run_now = datetime.utcnow()  # never refreshed — run now
                elif (datetime.utcnow() - row[0]).total_seconds() > refresh_hours * 3600:
                    run_now = datetime.utcnow()  # last refresh too long ago — run now
        except Exception:
            run_now = datetime.utcnow()  # can't check — run now to be safe

        scheduler.add_job(scheduled_refresh, "interval", hours=refresh_hours,
                          next_run_time=run_now)
        scheduler.start()
        print(f"Scheduled data refresh every {refresh_hours} hours.")
    except ImportError:
        print("APScheduler not installed — scheduled refresh disabled.")


if __name__ == "__main__":
    app.run(debug=False, port=5001)
