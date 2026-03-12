# Safe Journeys — Local Proof of Concept Design

## Objective

Build a local, Mac-runnable proof of concept that demonstrates the core novel
capabilities of the Safe Journeys platform using the existing 911K-record NZ CAS
dataset. No cloud services required — everything runs on a MacBook Pro M1 (32GB).

---

## Missing Data Sources That Would Unlock New Capabilities

Before building the PoC, these are data sources NOT yet in the architecture that
would significantly expand what's possible:

### High-Impact Gaps

| Data Source | What It Adds | Novel Use Case It Enables |
|-------------|-------------|--------------------------|
| **DEM / Elevation data (LINZ 8m DEM)** | Road gradient, hilliness, blind crests | Predict "hidden hazard" crashes where geometry deceives drivers. Gradient + wet weather = quantified stopping distance risk |
| **NZTA Road Assessment (skid resistance, roughness, rutting)** | Pavement condition metrics from high-speed surveys | Predict surface-failure crashes: worn surfaces + rain = loss-of-grip probability. No one models this at segment level |
| **Solar position (calculated)** | Sun glare angle relative to road bearing per hour | "Sun strike" crash prediction — a known killer that nobody models computationally at scale. Entirely derivable from road geometry + astronomy |
| **NZ Police alcohol/drug checkpoint data** | Locations and results of breath testing | Temporal/spatial alcohol crash risk patterns. Model impaired driving probability by area, day, time |
| **Vehicle fleet age data (MoT/WoF registry)** | Average vehicle age per region/meshblock | Older vehicles = worse braking, no ESC, no ABS. Regional fleet age as a crash severity predictor |
| **Hospital/trauma data (ACC claims)** | Actual injury outcomes (not just police-reported severity) | True severity modelling — police-reported severity under-counts. ACC data reveals actual cost/harm |
| **Cellular/GPS trace data (anonymised)** | Actual travel speeds, acceleration, braking patterns | Real driving behaviour vs posted limits. Identify where people actually speed, brake hard, swerve |
| **Street-level imagery (Google/Mapillary)** | Visual road condition, signage, sight lines | CV model to assess road "readability" — confusing intersections, obscured signs, poor markings |
| **NIWA climate normals + frost/ice data** | Historical frost frequency, black ice probability per location | Seasonal ice crash prediction — frost hollows, shaded valleys, bridge deck icing |
| **School zone / event data** | School locations, bell times, event calendars | Pedestrian/child risk modelling by time of day. School rush periods on specific roads |
| **Fatigue corridor data** | Long straight roads, distance from rest stops | Fatigue crash prediction on monotonous highways. Model cumulative driving time risk |
| **Road curvature (derived from geometry)** | Curve radius, advisory speed adequacy | "This curve is tighter than drivers expect" — mismatch between approach speed and required speed |

### The "Never Been Done" Opportunities

These combinations are genuinely novel — I'm not aware of anyone doing these at scale:

1. **Sun Strike Prediction** — Road bearing + solar ephemeris + time of day + crash history = "don't drive westbound on SH2 at 4:30pm in March"
2. **Surface Failure Probability** — Pavement condition + rainfall intensity + speed = P(loss of grip) per segment per hour
3. **Infrastructure ROI Prediction** — "If we install a roundabout here, the model predicts X% crash reduction based on similar interventions elsewhere" (using the epoch model)
4. **Driver Expectation Mismatch** — Curvature + gradient + speed limit + crash history = "drivers enter this curve too fast because the approach is straight and flat"
5. **Compound Risk Scoring** — No one combines weather × time × surface condition × road geometry × traffic volume × fleet age into a single real-time risk score

---

## Proof of Concept Scope

### What We'll Build (runs locally on Mac)

```
┌─────────────────────────────────────────────────────────────────┐
│                    LOCAL PoC PIPELINE                            │
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  │ 1. Load  │    │ 2. Engi- │    │ 3. Train │    │ 4. Inter │  │
│  │ & Clean  │───►│ neer     │───►│ Models   │───►│ active   │  │
│  │ CAS Data │    │ Features │    │          │    │ Results  │  │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘  │
│                                                                  │
│  Notebook 1:     Notebook 2:     Notebook 3:     Notebook 4:    │
│  Data loading,   Spatial grid,   LightGBM for    Maps, risk     │
│  EDA, quality    temporal feats,  crash predict,  scoring demo,  │
│  assessment      road profiles   severity model  recommendations│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Models to Train

#### Model 1: Crash Probability Predictor
- **Question:** "Given this road segment, these conditions, this time — how likely is a crash?"
- **Target:** Binary — did a crash occur in this H3 cell in this time window?
- **Features:** Road type, speed limit, weather, light, terrain, lanes, urban/rural, intersection, month, day-of-week, hour-proxy
- **Algorithm:** LightGBM (fast on M1, handles categorical features natively)
- **Output:** P(crash) per segment-condition combination

#### Model 2: Severity Predictor
- **Question:** "Given a crash occurs, how bad will it be?"
- **Target:** Ordinal — Fatal / Serious / Minor / Non-Injury
- **Features:** Speed limit, weather, light, road surface, terrain, vehicle types, road character
- **Algorithm:** XGBoost ordinal classifier
- **Output:** Severity distribution [P(fatal), P(serious), P(minor), P(non-injury)]

#### Model 3: Optimal Speed Recommender
- **Question:** "What speed limit would minimise crash severity on this segment?"
- **Target:** Counterfactual — model severity at different speed limits
- **Method:** Causal inference (speed as treatment, severity as outcome, road characteristics as confounders)
- **Output:** "Current limit: 100. Recommended: 80. Predicted severity reduction: 43%"

#### Model 4: Temporal Risk Forecaster
- **Question:** "What will the risk be on this segment over the next 24 hours?"
- **Target:** Crash count per segment per time window
- **Features:** Historical patterns + seasonal + weather conditions
- **Algorithm:** Prophet or simple temporal model
- **Output:** Risk score time series

### Interactive Outputs

1. **Risk heatmap** — Folium map of NZ coloured by predicted crash risk
2. **Feature importance** — What actually drives crashes? (surprising factors)
3. **Speed recommendations** — Table of segments where model suggests lower limits
4. **"What if" scenarios** — Change weather/time/speed and see risk change
5. **Infrastructure effectiveness** — Before/after analysis at known intervention sites

---

## Technical Design

### Environment
- Python 3.13 (Anaconda)
- Jupyter notebooks for interactive exploration
- All processing in-memory (911K rows × 72 cols ≈ 500MB — fits easily in 32GB)

### Dependencies
```
pandas              — Data manipulation
numpy               — Numerical operations
scikit-learn        — Preprocessing, metrics, baseline models
lightgbm            — Primary crash prediction model
xgboost             — Severity prediction model
h3                  — Uber H3 spatial indexing
folium              — Interactive maps
matplotlib/seaborn  — Static visualisations
pyproj              — NZTM → WGS84 coordinate projection
scipy               — Statistical tests, changepoint detection
shap                — Model explainability
```

### Data Processing Strategy

```
Raw CAS CSV (911K rows, 72 columns)
    │
    ▼
Step 1: Clean & type-cast
    - Parse coordinates (X,Y in NZTM → lat/lng WGS84)
    - Map categorical fields (weatherA, light, crashSeverity, etc.)
    - Handle nulls and sentinel values ("Null", empty strings)
    - Parse crashYear to proper integer
    │
    ▼
Step 2: Spatial indexing
    - Convert each crash lat/lng to H3 index (resolution 8)
    - This groups crashes into ~500m hexagonal cells
    - Count crashes per cell = base risk measure
    │
    ▼
Step 3: Feature engineering
    - Temporal: year, month, day_of_week (from crashYear + proxies)
    - Road profile: speed_limit, num_lanes, flat_hill, urban_rural,
                    road_surface, road_character, intersection
    - Conditions: weather_primary, weather_secondary, light_condition
    - Vehicle mix: car_pct, truck_pct, motorcycle_pct, bicycle_pct,
                   pedestrian_involved
    - Impact objects: tree, pole, cliff, ditch, guard_rail, fence
    - Severity encoding: fatal_count, serious_count, minor_count
    │
    ▼
Step 4: Negative sampling (for crash probability model)
    - For every H3 cell with crashes, generate "no crash" samples
      representing similar conditions where crashes didn't occur
    - Use background rate estimation by cell
    │
    ▼
Step 5: Train/test split
    - Temporal split: train on 2000–2021, validate on 2022–2023,
      test on 2024–2025
    - Prevents data leakage from future knowledge
```

### Novel Analysis: Sun Strike (derivable from existing data)

```python
# Road bearing can be estimated from consecutive crash coordinates
# on the same road, or from the crashDirectionDescription field.
#
# Solar position is calculable from:
#   - Latitude/longitude (from crash coordinates)
#   - Date (crashYear + month proxy from financial year)
#   - Time of day (proxy from light condition field)
#
# If sun azimuth ≈ road bearing ± 15°, and sun elevation < 20°,
# driver is looking directly into sun = "sun strike" risk.
#
# This has never been done at national scale with crash data.
```

### Project Structure

```
Issue Prediction/
├── ARCHITECTURE.md              # Enterprise architecture (existing)
├── POC_DESIGN.md                # This document
├── CAS_Data_public.csv          # Raw data (existing)
├── environment.yml              # Conda environment specification
├── poc/
│   ├── 01_data_loading.ipynb    # Load, clean, EDA
│   ├── 02_feature_engineering.ipynb  # Spatial indexing, features
│   ├── 03_crash_prediction.ipynb     # LightGBM crash probability
│   ├── 04_severity_model.ipynb       # XGBoost severity prediction
│   ├── 05_speed_recommender.ipynb    # Causal speed analysis
│   ├── 06_interactive_maps.ipynb     # Folium risk heatmaps
│   └── utils/
│       ├── __init__.py
│       ├── data_loader.py       # CSV loading and cleaning
│       ├── feature_eng.py       # Feature engineering functions
│       ├── spatial.py           # H3 indexing, coordinate transforms
│       └── plotting.py          # Visualisation helpers
```

---

## Success Criteria

The PoC is successful if it can demonstrate:

1. **Crash probability scoring** — Given a road segment and conditions, output a
   meaningful P(crash) that validates against held-out 2024-2025 data
2. **Severity prediction** — Predict crash severity with measurable accuracy
   (AUC-ROC > 0.70 for fatal/serious vs minor/non-injury)
3. **Speed recommendations** — Identify segments where a lower speed limit would
   materially reduce predicted severity, backed by causal analysis
4. **Visual risk map** — Interactive NZ map with colour-coded risk scores that
   an end user could understand
5. **Feature insights** — Reveal non-obvious crash risk factors from the model's
   feature importance (e.g., "advisory speed mismatches are 3x more predictive
   than weather")

---

## What This Proves for the Full Platform

| PoC Capability | Platform Feature It Validates |
|----------------|------------------------------|
| Crash probability model | Journey risk scoring API |
| Severity prediction | Risk severity weighting |
| Speed recommendations | Authority speed review dashboard |
| H3 spatial indexing | Universal spatial key architecture |
| Temporal patterns | Time-of-day risk modifiers |
| Weather × road interaction | Weather-adjusted real-time scoring |
| Feature importance | Explainable risk factors for users |
| Interactive maps | Web/mobile map rendering approach |
