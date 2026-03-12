# Safe Journeys — Technical Overview

How the prediction engine works, for solution architects and technical stakeholders.

---

## Two Models, One Risk Score

Safe Journeys uses **two separate models** that answer different questions, then combines them into a single risk score per location.

---

## 1. Severity Model — "If a crash happens here, how bad will it be?"

**Type:** LightGBM gradient-boosted decision tree (binary classification)

- **Target:** Predicts the probability of **Death or Serious Injury (DSI)** — a binary yes/no outcome
- **Algorithm:** LightGBM is an ensemble of decision trees built sequentially. Each new tree corrects the errors of the previous ones (gradient boosting). The production model is 938 simple decision trees, each one slightly improving on the last
- **Training data:** 789,270 crashes from 2000–2021 (temporal split — the model never sees future data)
- **Validation data:** 62,742 crashes from 2022–2023
- **Test data:** 58,811 crashes from 2024–2025
- **Test performance:** AUC 0.8186
- **Input features:** 80 features across:
  - Road attributes (speed limit, lanes, advisory speed)
  - Weather and visibility (rain, poor visibility, frost, wind)
  - Light conditions (dark, twilight)
  - Road characteristics (urban/rural, sealed, hill, intersection, traffic control)
  - Vehicle types (pedestrian, cyclist, motorcycle, truck, bus, etc.)
  - Impact objects (tree, fence, guard rail, cliff, ditch)
  - Compound risk factors (wet+dark, vulnerable+high speed, hill+wet)
  - Temporal (years since 2000, holiday flag)
  - Cell history (crash count, annual rate, mean speed, % rain/dark/intersection/urban/hill)
  - Area history (wider-region crash count and annual rate)
  - Categoricals (region, TLA, road character, surface, weather, light)
- **Output:** A probability between 0 and 1 (e.g. 0.25 = 25% chance a crash at this location results in DSI)

### Feature importance

The top predictors are vulnerable road users and location crash history — not speed alone:

| Rank | Feature | Category |
|------|---------|----------|
| 1 | vulnerableUser | Vehicle type |
| 2 | cell_crash_count | Cell history |
| 3 | area_crash_count | Area history |
| 4 | cell_annual_rate | Cell history |
| 5 | tlaName | Categorical |
| 6 | vulnerableAndHighSpeed | Compound risk |
| 7 | hasPedestrian | Vehicle type |
| 8 | area_annual_rate | Area history |
| 9 | carStationWagon | Vehicle type |
| 10 | totalVehicles | Vehicle type |
| 11 | motorcycle | Vehicle type |
| 12 | speedLimit | Road attribute |

**Key insight:** Who is on the road and where it is matters more than how fast.

### Model selection process

Seven configurations were tested. The winning model uses a low learning rate (0.01) with strong regularisation (alpha=0.5, lambda=2.0) and deeper trees (127 leaves), allowing very fine-grained gradient steps over 938 iterations.

| Configuration | Test AUC | Notes |
|---------------|----------|-------|
| LightGBM slow_deep (production) | 0.8186 | Low LR, deep trees, strong regularisation |
| LightGBM moderate | 0.8182 | 2x faster convergence, nearly identical AUC |
| LightGBM baseline | 0.8174 | Fast convergence, shallower trees |
| Ensemble (3-model avg) | 0.8184 | No benefit — models agree too much |
| LightGBM v1 (28 features) | 0.7786 | Original model, fewer features |
| LightGBM is_unbalance | 0.6819 | Failed — degenerate solution in 3 iterations |
| LightGBM with target leakage | 0.6761 | Failed — cell severity features leak the target |

### Target leakage lesson

An early version included per-cell DSI rates and mean severity as input features. These are essentially lookups of the target variable's historical average, so the model latched onto them and stopped learning real patterns. The fix: only use cell history features that describe the *location* (crash volume, road characteristics), not the *outcome* (severity, DSI rate).

This model is a **pre-trained artifact** (`lgb_dsi_model.pkl`). It does not retrain at runtime — it only runs inference, which takes milliseconds on CPU.

---

## 2. Frequency Model — "How often do crashes happen here?"

**Type:** Empirical Poisson rate estimation (statistical, not ML)

For each H3 hexagonal cell (~600m across), the model:

1. Counts historical crashes in that cell
2. Divides by the number of years of data to get an **annual crash rate**
3. Converts to an **hourly crash rate** (annual rate ÷ 8,760)

This is a `λ = count / time` calculation — the Poisson distribution's maximum likelihood estimator. It assumes crashes are independent, random events occurring at a roughly constant rate per location, which is a reasonable approximation for traffic incidents.

### Condition multipliers

The frequency model adjusts for current conditions by comparing the fraction of crashes that occur under certain conditions to the fraction of time those conditions exist:

```
multiplier = (% of crashes in condition) / (% of hours that condition exists)
```

Production values:

| Condition | Multiplier | Interpretation |
|-----------|-----------|----------------|
| Base | 1.00x | Average across all conditions |
| Rain | 1.06x | 6% more crashes per hour of rain |
| Dark | 0.72x | Fewer crashes at night (less traffic) |
| Rain + Dark | 1.02x | Roughly average |
| Fine daylight | 1.19x | Most crashes happen in good conditions — more traffic |

The exposure baselines (18% rain, 38% dark) are derived from NZ climate data.

---

## 3. Combined Output — ETNA

The two models combine into **ETNA (Expected Time to Next Accident)**:

```
adjusted_hourly_rate = base_hourly_rate × condition_multiplier
ETNA = 1 / adjusted_hourly_rate
```

ETNA is a **statistical average**, not a countdown or prediction of when the next crash will happen. A cell with an ETNA of 6 months means "at the current rate and conditions, crashes occur here roughly every 6 months on average."

The ETNA and DSI probability together form the risk score: a location that has frequent crashes *and* high severity probability is flagged as a hotspot.

---

## Spatial Indexing — H3 Hexagonal Grid

Crash locations are grouped into **H3 hexagonal cells** (Uber's hierarchical spatial index) at resolution 9, which produces hexagons roughly 600 metres across. This provides:

- Consistent cell sizes (unlike variable-sized suburbs or census areas)
- Neighbour lookups for area-level statistics (resolution 7 parent cells, ~5km)
- Efficient spatial queries without PostGIS

The app displays the top 5,000 cells ranked by crash count.

---

## Data Pipeline

### Data source

A single source: **NZTA/Waka Kotahi Crash Analysis System (CAS)** — 910,823 crash records from 2000–2025.

### Bootstrap

The initial dataset is pre-processed into `cas_features.parquet` with all feature engineering applied (H3 indexing, coordinate conversion, derived features). This file seeds the PostgreSQL database on first startup.

### Ongoing refresh

An APScheduler background job queries the **CAS ArcGIS FeatureServer REST API** every 4 hours for new crash records. The pipeline:

1. Queries the API for records newer than the most recent crash year in the database
2. Paginates through results (API returns max 2,000 per request)
3. Applies the same feature engineering pipeline as the bootstrap data
4. Upserts new records into PostgreSQL (deduplicates by OBJECTID)
5. Recomputes cell-level aggregation statistics
6. Logs the refresh outcome to `data_refresh_log`

On startup, if no successful refresh has occurred within the configured interval, an immediate refresh is triggered.

### Reverse geocoding

Street names in popups come from **OpenStreetMap Nominatim** (reverse geocoding coordinates to addresses). This is display-only — not used for predictions.

---

## Architecture Summary

| Component | Type | Purpose |
|-----------|------|---------|
| LightGBM classifier | Pre-trained ML model | Severity probability (DSI) |
| Poisson rate estimator | Statistical model | Crash frequency per cell |
| Condition multipliers | Statistical ratios | Real-time adjustment for weather/light |
| H3 hexagonal grid | Spatial indexing | Groups crashes into ~600m cells |
| PostgreSQL | Database | Persistent storage of crash records |
| CAS API ingestion | Scheduled ETL (4-hourly) | Pulls new crash data |
| Flask + Gunicorn | Web server | Serves API and map UI |
| Leaflet.js | Frontend | Interactive map visualisation |

---

## Runtime Requirements

- **CPU only** — LightGBM inference on 5,000 cells is trivial; no GPU needed
- **Memory** — ~1 GB for the full dataset in memory
- **Startup time** — ~30 seconds (load data, build frequency model, pre-compute GeoJSON)
- **Inference time** — milliseconds per API request

---

## Known Limitations and Ceiling

The model has reached approximately the ceiling of what the CAS dataset can provide (~0.82 AUC). All hyperparameter configurations converge to similar performance. Further improvement would require new data sources:

| Data Source | Expected Impact | Why |
|-------------|----------------|-----|
| Vehicle safety ratings (ANCAP) | High | Directly predicts occupant protection |
| Driver impairment (BAC, fatigue) | High | Strongest known predictor of fatal crashes |
| Road geometry (curvature, grade) | Medium | Currently only a flat/hill binary flag |
| AADT traffic volumes | Medium | Enables crash rate vs exposure analysis |
| Real-time weather intensity | Medium | Currently only rain yes/no |
| EMS response times | Medium | Affects fatal vs serious outcome |
| Seatbelt/helmet compliance | High | Directly affects injury severity |

---

## What It Is NOT

- **Not a neural network / deep learning** — gradient-boosted trees are more interpretable and perform better on structured tabular data of this size
- **Not real-time crash prediction** — it uses historical patterns adjusted for current conditions, not live sensor data
- **Not causal inference** — the model identifies correlations (e.g. motorcycles correlate with severe crashes) but cannot prove causation
- **Not a countdown** — ETNA is a statistical average, not a timer

---

*Dataset: NZ CAS 2000–2025, 910,823 records*
*Best model: LightGBM Booster, 938 iterations, 80 features, AUC 0.8186*
